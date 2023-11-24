import asyncio
from asyncio_mqtt import Client, MqttError
import ssl
import json
from asyncio_vedirect_mqtt.victron import AsyncIOVeDirect
from asyncio_vedirect_mqtt.hass_discovery import Sensor, Device
import logging
import time

logger = logging.getLogger(__name__)

device_types = {
    "mppt": {
        "model": "BlueSolar 100/50",
        "category": "Solar",
        "sensors": {
            "H19": {
                "name": "Yield Total",
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "total_increasing",
                "multiplier": 0.01
            },
            "H20": {
                "name": "Yield Today",
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "total_increasing",
                "multiplier": 0.01
            },
            "V": {
                "name": "Output Voltage",
                "unit_of_measurement": "V",
                "device_class": "voltage",
                "state_class": "measurement",
                "multiplier": 0.001
            },
            "VPV": {
                "name": "Panel Voltage",
                "unit_of_measurement": "V",
                "device_class": "voltage",
                "state_class": "measurement",
                "multiplier": 0.001
            },
            "I": {
                "name": "Output Current",
                "unit_of_measurement": "A",
                "device_class": "current",
                "state_class": "measurement",
                "multiplier": 0.001
            },
            # "IL": {
            #     "name": "Load Current",
            #     "unit_of_measurement": "A",
            #     "device_class": "current",
            #     "state_class": "measurement",
            #     "multiplier": 0.001
            # },
            "PPV": {
                "name": "Panel Power",
                "unit_of_measurement": "W",
                "device_class": "power",
                "state_class": "measurement",
                "multiplier": 1
            },
        }
    },
    "shunt": {
        "model": "SmartShunt 500A/50mV",
        "category": "Battery",
        "sensors": {
            "V": {
                "name": "Battery Voltage",
                "unit_of_measurement": "V",
                "device_class": "voltage",
                "state_class": "measurement",
                "multiplier": 0.001
            },
            "I": {
                "name": "Battery Current",
                "unit_of_measurement": "A",
                "device_class": "current",
                "state_class": "measurement",
                "multiplier": 0.001
            },
        }
    }
}

class AsyncIOVeDirectMqtt:
    def __init__(self, tty, device, type, broker, *args, tls_protocol=None, windowing=60, port=1883, username=None, password=None, mqttretry=30, verbose=False, timeout=60,
                 ca_path=None, **kwargs):
        self.tty = tty
        self.device_name = device
        self.device_type = device_types[type]
        self.broker = broker
        self.port = int(port)
        self.username = username
        self.password = password
        self.mqttretry = mqttretry
        self.verbose = verbose
        self.windowing=windowing
        self.mqtt_exception = None
        self.ve_connection = AsyncIOVeDirect(tty, timeout)
        self.ssl_context = ssl.SSLContext(tls_protocol) if tls_protocol else None
        if ca_path and self.ssl_context:
            self.ssl_context.load_verify_locations(capath=ca_path)
        self.sensor_mapping = {}

    async def setup_sensors(self, mqtt_client):
        logger.info("Publishing sensors to home assistant")
        device = Device(self.device_name, self.device_type["model"], 'Victron')

        for sensor_id in self.device_type["sensors"]:
            sensor = self.device_type["sensors"][sensor_id]
            self.sensor_mapping[sensor_id] = Sensor(
                mqtt_client,
                sensor["name"],
                category=self.device_type["category"],
                parent_device=device,
                unit_of_measurement=sensor["unit_of_measurement"],
                device_class=sensor["device_class"],
                state_class=sensor["state_class"],
                multiplier=sensor["multiplier"],
                mov_avg=self.windowing
            )

        for key in self.sensor_mapping.keys():
            await self.sensor_mapping[key].publish_discovery()


    async def publish_data(self, data):
        for key, value in data.items():
            if key in self.sensor_mapping.keys():
                try:
                    await self.sensor_mapping[key].send(value)
                except MqttError:
                    self.mqtt_exception = MqttError

    async def run(self):
        while True:
            try:
                logger.info(f"Initiating connection to broker ({self.broker}:{self.port} {self.ssl_context=})")
                async with Client(hostname=self.broker, port=self.port, tls_context=self.ssl_context, username=self.username,
                                  password=self.password) as client:
                    logger.info("Connection to broker successful")
                    await self.setup_sensors(client)
                    logger.info(f"Listening for ve.direct data on {self.tty}")
                    while True:
                        if self.mqtt_exception:
                            raise self.mqtt_exception
                        ve_data = await self.ve_connection.read_data_single()
                        asyncio.create_task(self.publish_data(ve_data), name='publish_data')
            except MqttError as error:
                self.mqtt_exception = None
                logger.error(f'Error "{error}". Reconnecting in {self.mqttretry} seconds.')
                await asyncio.sleep(self.mqttretry)
