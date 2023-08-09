from neopixel import NeoPixel
import uasyncio as asyncio
from machine import Pin
import network
import dht
from umqtt.robust import MQTTClient
import ubinascii
import json
from time import sleep
from config.wifi_credentials import WIFI_SSID, WIFI_PWD
import config.config as cfg


class IoTAtmoLightAndSensorTempHumThresholded:

    def __init__(self):
        self.sensor = dht.DHT22(Pin(cfg.SENSOR_PIN))
        self.temp = None
        self.hum = None
        self.prev_temp = None
        self.prev_hum = None
        self.publishes_skipped = 0
        self.leds = NeoPixel(Pin(cfg.ATMO_PIN, Pin.OUT), cfg.ATMO_LED_COUNT)
        self.bright = None
        self.rgb = None
        self.wifi = network.WLAN(network.STA_IF)
        self.client_id = self.client_id()
        self.mqtt_client = MQTTClient(self.client_id, cfg.MQTT_SERVER, keepalive=0)

    async def run(self) -> None:
        await asyncio.gather(self.measure_pub_loop(), self.atmo_sub_loop())

    async def measure_pub_loop(self) -> None:
        while True:
            try:
                self.read_sensor()
                if self.should_publish():
                    if not self.wifi.isconnected():
                        self.connect()
                    if self.wifi.isconnected():
                        self.update_previous_values()
                        self.publish()
            except Exception as e:
                print(f"Error in measure pub loop - continuing\nError: {e}")
            await asyncio.sleep(cfg.SENSOR_INTERVAL)

    async def atmo_sub_loop(self):
        while True:
            try:
                if not self.wifi.isconnected():
                    self.connect()
                if self.wifi.isconnected():
                    self.mqtt_client.check_msg()
            except Exception as e:
                print(f"Error in atmo sub loop - continuing\nError: {e}")
            await asyncio.sleep(cfg.ATMO_INTERVAL)

    def connect(self) -> None:
        self.connect_to_wifi()
        self.connect_to_mqtt()

    def connect_to_mqtt(self) -> None:
        self.mqtt_client.connect()
        self.subscribe_atmo()

    def subscribe_atmo(self) -> None:
        self.mqtt_client.set_callback(self.update_leds)
        self.mqtt_client.subscribe(cfg.ATMO_COLOR_TOPIC)
        self.mqtt_client.subscribe(cfg.ATMO_BRIGHTNESS_TOPIC)

    def update_leds(self, topic, payload) -> None:
        try:
            topic, payload = self.decode((topic, payload))
            print(f'Received topic: {topic} with payload: {payload}')
            if topic == cfg.ATMO_COLOR_TOPIC:
                self.rgb = self.hex_to_rgb(payload)
            elif topic == cfg.ATMO_BRIGHTNESS_TOPIC:
                self.bright = self.brightness_percent(payload)
            else:
                print(f'No handler found for topic {topic}')
        except Exception as e:
            print(f'Error processing callback values. topic: {topic}, payload: {payload}.')
            print(f'Error: {e}')
        if self.bright is not None and self.rgb is not None:
            # brightness ranges from 0.0 to 1.0 and is applied to set rgb brightness
            _rgb = tuple([round(c * self.bright) for c in self.rgb])
            print(f'brightness: {self.bright}, rgb = {self.rgb} -> {_rgb}')
            self.leds.fill(_rgb)
            self.leds.write()
        else:
            print(f'rgb({self.rgb}) or brightness({self.bright}) not set')

    def client_id(self) -> str:
        _client_id = ubinascii.hexlify(self.wifi.config('mac'), ":").decode().upper()
        print(f'client id = {_client_id}')
        return _client_id

    @staticmethod
    def decode(topic_msg: (bytes, bytes)) -> (str, str):
        return tuple([i.decode('utf-8') for i in topic_msg])

    @staticmethod
    def hex_to_rgb(hex_color: str) -> (int, int, int):
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))  # hex -> rgb

    @staticmethod
    def brightness_percent(brightness: str) -> int:
        # should be 0 to 100. Using it as percent to substitute NeoPixel's lacking
        # explicit brightness method.
        brightness = int(brightness)
        # accept 0-100, others mapped to 0
        return brightness / 100 if 0 < brightness <= 100 else 0

    def max_publish_skips_reached(self):
        return self.publishes_skipped >= cfg.MAX_PUBLISH_SKIPS

    def update_previous_values(self) -> None:
        self.prev_temp = self.temp
        self.prev_hum = self.hum

    def should_publish(self) -> bool:
        will_publish = True  # default - will be changed if no threshold exceeded
        if self.prev_temp is None or self.prev_hum is None:
            print('No previous value')
        elif self.publishes_skipped >= cfg.MAX_PUBLISH_SKIPS:
            print('Max times skipped reached')
        elif abs(self.temp - self.prev_temp) >= cfg.TEMP_THRESHOLD:
            print('Temperature change threshold exceeded')
        elif abs(self.hum - self.prev_hum) >= cfg.HUM_THRESHOLD:
            print('Humidity change threshold exceeded')
        else:
            print(f'Skipping update: Temp: {self.temp} Hum: {self.hum}')
            will_publish = False
        self.publishes_skipped = 0 if will_publish else self.publishes_skipped + 1
        return will_publish

    def connect_to_wifi(self) -> None:
        self.wifi.active(True)
        self.wifi.connect(WIFI_SSID, WIFI_PWD)
        while not self.wifi.isconnected():
            print('connecting...')
            sleep(0.3)
        print(f'connected: {self.wifi.ifconfig()}')

    def read_sensor(self) -> None:
        self.sensor.measure()
        self.temp = self.sensor.temperature()
        self.hum = self.sensor.humidity()

    def publish(self) -> None:
        json_data = json.dumps({'temp': self.temp, 'hum': self.hum, 'id': self.client_id})
        self.mqtt_client.publish(cfg.SENSOR_TOPIC, json_data)
        print(f"====> Published: Temp: {self.temp} Hum: {self.hum}")


if __name__ == '__main__':
    asyncio.run(IoTAtmoLightAndSensorTempHumThresholded().run())
