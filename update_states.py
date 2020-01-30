from spacedirectory.models import directory
from spacedirectory.models.space import Space
import json
import spacedirectory.tools as tools
import time
import sys
from pathlib import Path
import paho.mqtt.client as mqtt
from datetime import datetime
from datetime import timedelta

current_state = {}

def log(message):
    print("LOG: " + message)

class SpaceStateUpdater:
    def __init__(self, config, spacedirectory):
        self.topic = config['topic']
        self.state = None
        self.next_refresh = datetime.now()
        self.refresh_interval = config.get('interval', 300)
        self.error_initerval = 900
        self.mqttc = None
        self.mqttconfig = None
        self.mqtt_timeout = config.get('mqtt_timeout', 300)
        self.mqtt_connected = False
        self.mqtt_reconnect = None
        self.mqtt_reconnect_interval = config.get('mqtt_reconnect_interval', 60)

        if 'spacedirectory' in config:
            self.url = spacedirectory[config['spacedirectory']]
        elif 'url' in config:
            self.url = config['url']
        else:
            self.url = None

        if 'mqtt' in config:
            self.setup_mqtt(config['mqtt'])

    def update(self):
        if self.mqttc is not None:
            self.mqttc.loop(timeout=1.0)
            if not self.mqtt_connected and self.mqtt_reconnect and datetime.now() > self.mqtt_reconnect:
                try:
                    log("Attempting reconnect for " + self.mqttconfig['host'])
                    self.mqttc.reconnect()
                except:
                    log("Unable to reconnect MQTT for " + self.mqttconfig['host'])
                    self.mqtt_reconnect = datetime.now() + timedelta(seconds=self.mqtt_reconnect_interval)

        if self.url is None:
            self.state = 'unknown'
            return

        if datetime.now() > self.next_refresh:
            try:
                log("Updating " + self.url)
                data = tools.get_json_data_from_url(self.url)
                space = Space(data = data)
                self.state = "open" if space.status.is_open else "closed"
                self.next_refresh = datetime.now() + timedelta(seconds=self.refresh_interval)

                if 'state' in data and 'mqtt' in data['state']:
                    self.setup_mqtt(data['state']['mqtt'])

            except:
                log("Unable to check spacestate for " + self.topic)
                self.next_refresh = self.next_refresh + timedelta(seconds=self.error_initerval)

    def on_mqtt_message(self, client, userdata, message):
        if 'topic' in self.mqttconfig and message.topic == self.mqttconfig['topic']:
            payload = message.payload.decode('UTF-8')
            if payload == self.mqttconfig.get('closed', 'closed'):
                self.state = "closed"
            elif payload == self.mqttconfig.get('open', 'open'):
                self.state = "open"
            else:
                self.state = "unknown"

            self.next_refresh = datetime.now() + timedelta(seconds=self.mqtt_timeout)

    def on_mqtt_connect(self, client, userdata, flags, rc):
        self.mqtt_connected = True
        if 'topic' in self.mqttconfig:
            self.mqttc.subscribe(self.mqttconfig['topic'])

    def on_mqtt_disconnect(self, client, userdata, rc):
        self.mqtt_connected = False
        self.mqtt_reconnect = datetime.now() + timedelta(seconds=self.mqtt_reconnect_interval)
        log("MQTT for " + self.mqttconfig['host'] + " disconnected")

    def setup_mqtt(self, config):
        if self.mqttc is not None:
            return

        if self.mqttconfig is not None:
            return

        if 'host' not in config:
            return

        log("Configuring MQTT for " + config['host'])

        try:
            self.mqttconfig = config
            self.mqttc = mqtt.Client()
            self.mqttc.on_message = self.on_mqtt_message
            self.mqttc.on_connect = self.on_mqtt_connect
            self.mqttc.on_disconnect = self.on_mqtt_disconnect
            self.mqttc.connect(config['host'], config.get('port', 1833))
        except:
            log("Error connecting MQTT for " + self.topic)
            self.mqtt_reconnect = datetime.now() + timedelta(seconds=self.mqtt_reconnect_interval)

def on_message(client, userdata, message):
    global current_state
    current_state[message.topic] = message.payload.decode('UTF-8')

def on_connect(client, userdata, flags, rc):
    global config
    client.subscribe(config['prefix'] + "#", qos=2)

if __name__ == "__main__":
    global config

    log("Loading configfile")

    configfile = Path('config.json')
    if not configfile.is_file():
        log("config.json does not exist")
        sys.exit(1)

    config = json.loads(open("config.json", "r").read())

    mqttc = mqtt.Client()
    mqttc.on_message = on_message
    mqttc.on_connect = on_connect

    mqttc.connect(config['server'])
    mqttc.loop_start()

    spacedirectory = directory.get_spaces_list()

    spaces = []
    for s in config['spaces']:
        spaces.append(SpaceStateUpdater(s, spacedirectory))

    while True:
        try:
            for s in spaces:
                s.update()
                topic = config['prefix'] + s.topic
                if s.state is not None:
                    if topic in current_state and current_state[topic] != s.state:
                        log("changed from " + current_state[topic] + " to " + s.state)
                        current_state[topic] = s.state
                        mqttc.publish(topic, payload=s.state, retain=True)
                    elif topic not in current_state:
                        current_state[topic] = s.state
                        mqttc.publish(topic, payload=s.state, retain=True)
                elif topic not in current_state:
                    current_state[topic] = "unknown"
                    mqttc.publish(topic, payload="unknown", retain=True)

            time.sleep(.1)

        except KeyboardInterrupt:
            log("Error")
            sys.exit(2)
