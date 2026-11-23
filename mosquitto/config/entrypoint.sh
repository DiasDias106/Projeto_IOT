#!/bin/sh
# Builds the password file from the environment variables (.env) and starts the broker.
set -e

PASSWD=/mosquitto/passwd
rm -f "$PASSWD"
touch "$PASSWD"
mosquitto_passwd -b "$PASSWD" "$MQTT_CLIENT1_USER"   "$MQTT_CLIENT1_PASSWORD"
mosquitto_passwd -b "$PASSWD" "$MQTT_PROCESSOR_USER" "$MQTT_PROCESSOR_PASSWORD"
mosquitto_passwd -b "$PASSWD" "$MQTT_NODERED_USER"   "$MQTT_NODERED_PASSWORD"
chown mosquitto:mosquitto "$PASSWD"
chmod 0700 "$PASSWD"
chown -R mosquitto:mosquitto /mosquitto/data

exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
