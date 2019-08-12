#!/usr/bin/python3
# -*- coding: utf-8 -*-
from __future__ import print_function
import argparse
import binascii
import time
import os
import csv
import sys
import json
import logging
from bluepy import btle
import paho.mqtt.client as mqtt
from datetime import datetime

from xiaomi_mi_scale_external.Xiaomi_Scale_Body_Metrics import bodyMetrics as XiaomiBodyMetrics

with open('config.json') as config_file:
    configuration = json.load(config_file)

JSON_KEY_WEIGHT = "Weight"
JSON_KEY_METRICS = "metrics"
JSON_KEY_BMI = "BMI"
JSON_KEY_BASAL_METABOLISM = "Basal Metabolism"
JSON_KEY_VISCERAL_FAT = "Visceral Fat"

LOGGING_LEVEL = logging.DEBUG

WEIGHT_MEASUREMENT_SERVICE = '0000181d-0000-1000-8000-00805f9b34fb'
WEIGHT_MEASUREMENT_CHARACTERISTIC = '00002a9d-0000-1000-8000-00805f9b34fb'
WEIGHT_MEASUREMENT_HISTORY_CHARACTERISTIC = '00002a2f-0000-3512-2118-0009af100700'
CURRENT_TIME_CHARACTERISTIC = '00002a2b-0000-1000-8000-00805f9b34fb'
DEVICE_INFORMATION_SERVICE = '0000180a-0000-1000-8000-00805f9b34fb'
FIRMWARE_CHARACTERISTIC = '00002a28-0000-1000-8000-00805f9b34fb'
SERIAL_NUMBER_CHARACTERISTIC = '00002a25-0000-1000-8000-00805f9b34fb'
GENERAL_ATTRIBUTES_SERVICE = '00001800-0000-1000-8000-00805f9b34fb'
APPEARANCE_CHARACTERISTIC = '00002a00-0000-1000-8000-00805f9b34fb'
DEVICE_NAME_CHARACTERISTIC = '00002a00-0000-1000-8000-00805f9b34fb'

logging.basicConfig(filename="xiaomi_scale.log", level=LOGGING_LEVEL)
logging.getLogger().addHandler(logging.StreamHandler())

userIdentifier = bytes([
    4,
    255,
    255,
    (configuration['USER_IDENTIFIER'] & 65280) >> 8,
    (configuration['USER_IDENTIFIER'] & 255) >> 0
])

class MiScaleBluetoothDelegate(btle.DefaultDelegate):
    def __init__(self, deviceObject, measurementHandle, historyHandle, timeHandle):
        btle.DefaultDelegate.__init__(self)
        self.deviceObject = deviceObject
        self.measurementHandle = measurementHandle
        self.historyHandle = historyHandle
        self.timeHandle = timeHandle
        logging.debug('Initializing notification handler')

    def handleNotification(self, cHandle, data):
        data_hex = data.hex()
        if (cHandle == self.timeHandle):
            logging.debug('Processing time synchronization request')
            scaleYear = int(data_hex[2:4] + data_hex[0:2], 16)
            scaleMonth = int(data_hex[4:6], 16)
            scaleDay = int(data_hex[6:8], 16)
            current_dt = datetime.now()

            if(not(scaleYear == current_dt.year and scaleMonth == current_dt.month and scaleDay == current_dt.day)):
                logging.info('Date is incorrect, updating date on the MiScale')
                logging.debug('Current date on MiScale is: ' + '{:04d}-{:04d}-{:04d}'.format(scaleYear, scaleMonth, scaleDay))
                # Time is not displayed in debug, it will be incorrect anyway

                dt_byte = [
                    current_dt.year % 256,
                    current_dt.year >> 8,
                    current_dt.month,
                    current_dt.day,
                    current_dt.hour,
                    current_dt.minute,
                    current_dt.second,
                    3,
                    0,
                    0
                ]

                self.deviceObject.device.writeCharacteristic(self.timeHandle, bytearray(dt_byte))
                logging.debug('MiScale date updated with current date and time')
        elif (len(data) > 0):
            #print('data incoming')
            #print(data_hex + " " + str(len(data)))
            if (data[0] == 3):
                logging.debug('Processing stop signal')
                self.deviceObject.device.writeCharacteristic(self.historyHandle, b'\0x03')
                self.deviceObject.device.writeCharacteristic(self.historyHandle, userIdentifier)

            if len(data) == 20:
                logging.debug('Processing history notification')
                self.deviceObject.ProcessWeight(data[0:10])
                self.deviceObject.ProcessWeight(data[10:20])
            elif len(data) == 10:
                logging.debug('Processing single data notification')
                self.deviceObject.ProcessWeight(data[0:10])

class MiScaleDevice():

    def ReadPeopleData(self, file):
        people = []
        with open(file, "r") as file_obj:
            reader = csv.DictReader(file_obj)
            for line in reader:
                line['weight_min'] = int(line['weight_min'])
                line['weight_max'] = int(line['weight_max'])
                line['height'] = float(line['height'])
                people.append(line)
        return people

    def ProcessWeight(self, data):
        data_hex = data.hex()
        logging.debug('Processing weight data: ' + data_hex)
        scaleYear = int(data_hex[8:10] + data_hex[6:8], 16)
        scaleMonth = int(data[5])
        scaleDay = int(data[6])
        scaleHours = int(data[7])
        scaleMinutes = int(data[8])
        scaleSeconds = int(data[9])
        scaleWeight = round(0.01 * int(data_hex[4:6] + data_hex[2:4], 16),1)

        firstByte = data[0]
        isLBSUnit = firstByte >> 0 & 1
        isJinUnit = firstByte >> 4 & 1
        isStabilized = firstByte >> 5 & 1
        isWeightRemoved = firstByte >> 7 & 1

        unit = 'jin'
        if isLBSUnit:
            unit = 'lbs'
        elif not isJinUnit:
            scaleWeight = scaleWeight / 2
            unit = 'kg'
        logging.debug('MiScale is configured to use "' + unit + '" as measurement')

        if(isStabilized == 1 and isWeightRemoved != 1):
            logging.info('Received stabilized weight information: ' + str(scaleWeight) + ' ' + unit)
            self.PublishWeightInformation(scaleWeight, unit)
        else:
            logging.debug('Current weight: ' + str(scaleWeight) + ' ' + unit)

    def DetectScaleUser(self, scaleWeight):
        # @TODO: Fix stub
        return 'default'

    def GetUserData(self, user):
        # @TODO: Fix stub
        return {
            'height': 180,
            'age': 23,
            'sex': 'male',
        }

    def GetUserMetrics(self, user, scaleWeight, unit):
        userData = self.GetUserData(user)

        # Adapt input data to body framework library
        scaleWeight = self.ConvertWeight(scaleWeight, unit, 'kg')

        lib = XiaomiBodyMetrics(scaleWeight, userData['height'], userData['age'], userData['sex'], 0)
        bodyMetricsData = {
            JSON_KEY_BMI: round(lib.getBMI(), 2),
            JSON_KEY_BASAL_METABOLISM: round(lib.getBMR(), 2),
            JSON_KEY_VISCERAL_FAT: round(lib.getVisceralFat(), 2),
        }

        if False: #miimpendance:
            lib = Xiaomi_Scale_Body_Metrics.bodyMetrics(weight, height, age, sex, int(miimpedance))
            message += ',"Lean Body Mass":"' + "{:.2f}".format(lib.getLBMCoefficient()) + '"'
            message += ',"Body Fat":"' + "{:.2f}".format(lib.getFatPercentage()) + '"'
            message += ',"Water":"' + "{:.2f}".format(lib.getWaterPercentage()) + '"'
            message += ',"Bone Mass":"' + "{:.2f}".format(lib.getBoneMass()) + '"'
            message += ',"Muscle Mass":"' + "{:.2f}".format(lib.getMuscleMass()) + '"'
            message += ',"Protein":"' + "{:.2f}".format(lib.getProteinPercentage()) + '"'

        return bodyMetricsData

    def ConvertWeight(self, scaleWeight, unit_from, unit_to = 'kg'):
        if unit_from != unit_to:
            if unit_to == 'kg':
                if unit_from == 'jin':
                    scaleWeight = 0.5 * scaleWeight
                elif unit_from == 'lbs':
                    scaleWeight = 0.45359237 * scaleWeight
            elif unit_to == 'lbs':
                # jin = 2kg, so why not? i know it looks dirty :P
                scaleWeight = 2.2046226218488 * scaleWeight
                if unit_from == 'jin':
                    scaleWeight = 0.5 * scaleWeight
            elif unit_to == 'jin':
                if unit_from == 'kg':
                    scaleWeight = 2 * scaleWeight
                elif unit_from == 'lbs':
                    scaleWeight = 0.90718474 * scaleWeight
            scaleWeight = round(scaleWeight, 1)

        return scaleWeight

    def PublishWeightInformation(self, scaleWeight, unit):
        if self.unit is None:
            logging.debug('Received unit information. Scale uses "' + unit + '" for measurement')
            self.unit = unit

        if configuration['HOMEASSISTANT_DISCOVERY'] and configuration['HOMEASSISTANT_LAZY_DISCOVERY'] and not self.hass_discovery_sent:
            self._publish_homeassistant_discovery()

        if unit != self.unit:
            logging.warning('Received weight in unit "' + unit + '", although expected unit is "' + self.unit + '". This might either be a protocol error or a configuration mistake. Fixing.')
            scaleWeight = self.ConvertWeight(scaleWeight, unit, self.unit)
            unit = self.unit
            logging.debug('Converted weight: ' + str(scaleWeight) + ' ' + unit)

        metrics = self.GetUserMetrics(self.DetectScaleUser(scaleWeight), scaleWeight, unit)
        valueJson = json.dumps({JSON_KEY_WEIGHT: scaleWeight, JSON_KEY_METRICS: metrics})

        logging.debug('Publishing data with topic "' +  self.mqtt_topic + '" (MQTT message: "' + valueJson + '")')
        self.mqtt_client.publish(self.mqtt_topic, valueJson)
        logging.debug('Body metrics published')

    def PrintCurrentDeviceAbilities(self):
        for svc in self.device.getServices():
            print(str(svc) + ' (' + str(svc.uuid) + ')')
            for chr in svc.getCharacteristics():
                print('    ' + str(chr) + ' (' + str(chr.uuid) + ')')
                for dsc in chr.getDescriptors():
                    print('        ' + str(dsc) + ' (' + str(dsc.uuid) + ')')

    def __init__(self, address):
        print('initializing miscale')
        self.strippedAddress = address.replace(':','').lower()
        self.mqtt_client = None
        self.mqtt_topic = 'sensor/miscale_' + self.strippedAddress + '/state'
        self.connected = False
        self.unit = None
        self.hass_discovery_sent = False
        self._start_client()
        self.address = address

        if configuration['FORCE_UNIT'] is not None and configuration['FORCE_UNIT']:
            #@TODO: check for unit validity
            logging.debug('Forcing unit conversion to: ' + configuration['FORCE_UNIT'])
            self.unit = configuration['FORCE_UNIT']
        while True:
            try:
                logging.info('Connecting to Mi Scale ' + address + '...')
                self._connect_miscale()
                self._setup_miscale_v1() # assume we have v1

                if configuration['HOMEASSISTANT_DISCOVERY'] and not configuration['HOMEASSISTANT_LAZY_DISCOVERY']:
                    if self.unit is None:
                        logging.warning('Using kilograms as the default unit')
                        self.unit = 'kg'
                    self._publish_homeassistant_discovery()

                logging.debug('Beginning notification loop')
                while True:
                    if self.device.waitForNotifications(1.0):
                        logging.debug('Notification processing finished')
                        continue
            except btle.BTLEDisconnectError as e:
                logging.debug('Device went away, reconnecting')

    def _reconnect(self):
        print('device disconnected, reconnecting')
        time.sleep(1.0)

    def _publish_homeassistant_discovery(self):
        homeAssistantTopic = configuration['HOMEASSISTANT_DISCOVERY_PREFIX'] + '/sensor/miscale_' + self.strippedAddress + '/config'
        message = json.dumps({
            #"device_class": "sensor",
            "name": "Mi Scale " + self.strippedAddress,
            "state_topic": self.mqtt_topic,
            "unit_of_measurement": self.unit,
            "icon": "mdi:scale",
            "unique_id": "miscale_" + self.strippedAddress,
            "value_template": '{{ value_json.' + JSON_KEY_WEIGHT + ' }}',
            "json_attributes_topic": self.mqtt_topic,
            "json_attributes_template": '{{ value_json.' + JSON_KEY_METRICS + ' | tojson }}',
            "device": {
                "identifiers": ["miscale_" + self.strippedAddress, "miscale_" + self.device_info['serial']],
                "manufacturer": "Xiaomi Inc.",
                "model": "Mi Scale v1",
                "name": "Mi Scale " + self.strippedAddress,
                "sw_version": self.device_info['firmware']
            }
        })
        logging.info('Publishing HomeAssistant MQTT Discovery message')
        logging.debug("HASS Discovery topic " + homeAssistantTopic)
        logging.debug("HASS Discovery message " + message)
        self.mqtt_client.publish(homeAssistantTopic, message, qos=0, retain=True)
        self.hass_discovery_send = True

    def _connect_miscale(self):
        logging.debug('Connecting to bluetooth peripheral: ' + str(self.address))
        self.device = device = btle.Peripheral( self.address )

        dis = self.device.getServiceByUUID(DEVICE_INFORMATION_SERVICE)
        snc = dis.getCharacteristics(SERIAL_NUMBER_CHARACTERISTIC)[0]
        fwc = dis.getCharacteristics(FIRMWARE_CHARACTERISTIC)[0]

        gas = self.device.getServiceByUUID(GENERAL_ATTRIBUTES_SERVICE)
        dnc = gas.getCharacteristics(DEVICE_NAME_CHARACTERISTIC)[0]
        apc = gas.getCharacteristics(APPEARANCE_CHARACTERISTIC)[0]

        self.device_info = {
            'firmware': fwc.read().decode('utf-8'),
            'serial': snc.read().decode('utf-8'),
            'name': dnc.read().decode('utf-8'),
            'appearance': apc.read().decode('utf-8'),
        }

        print(self.device_info)


    def _setup_miscale_v1(self):
        print('setting up miscale v1')
        wms = self.device.getServiceByUUID(WEIGHT_MEASUREMENT_SERVICE)
        wmc = wms.getCharacteristics(WEIGHT_MEASUREMENT_CHARACTERISTIC)[0]
        wmhc = wms.getCharacteristics(WEIGHT_MEASUREMENT_HISTORY_CHARACTERISTIC)[0]
        ctc = wms.getCharacteristics(CURRENT_TIME_CHARACTERISTIC)[0]

        helper = MiScaleBluetoothDelegate(self, wmc.valHandle, wmhc.valHandle, ctc.valHandle)
        currentTime = ctc.read()
        helper.handleNotification(ctc.valHandle, currentTime)

        self.device.withDelegate(helper)

        #self.device.writeCharacteristic(wmhc.valHandle, b'\0x01\0x96\0x8a\0xbd\0x62')
        #self.device.writeCharacteristic(wmhc.valHandle+1, b'\0x01\0x00')
        self.device.writeCharacteristic(wmc.valHandle+1, b'\x01\x00')

    def _start_client(self):
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.username_pw_set(configuration['MQTT_USERNAME'], configuration['MQTT_PASSWORD'])

        def _on_connect(client, _, flags, return_code):
            self.connected = True
            print("MQTT connection returned result: %s" % mqtt.connack_string(return_code))

        def _on_message(client, _, message):
            print("MQTT received  '" + str(message.payload) + "' on topic " + str(message.topic))

        def _on_publish(client, _, mid):
            print("MQTT message " + str(mid) + " published")

        self.mqtt_client.on_connect = _on_connect
        self.mqtt_client.on_message = _on_message
        self.mqtt_client.on_publish = _on_publish

        self.mqtt_client.connect(configuration['MQTT_HOST'], configuration['MQTT_PORT'], configuration['MQTT_TIMEOUT'])
        self.mqtt_client.loop_start()

def main():
    global configuration
    if configuration['MISCALE_MAC'] is None:
        try:
            logging.info('Running preliminary Bluetooth device scan')
            scanner = btle.Scanner()
            devices = scanner.scan(10.0)
            for device in devices:
                if device.getValueText(9) == 'MI_SCALE':
                    configuration['MISCALE_MAC'] = device.addr
                    break
        except btle.BTLEManagementError as e:
            logging.error('Could not scan for devices. Please, set your MiScale\'s MAC address or run script with permissions for bluetooth scanning.')
            exit()
    # @TODO: Make multiple devices available
    MiScaleDevice(configuration['MISCALE_MAC'])


if __name__ == "__main__":
    main()
