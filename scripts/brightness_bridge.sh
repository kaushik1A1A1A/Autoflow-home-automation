#!/bin/bash
BRIGHTNESS_FILE="/home/USER/homeassistant/config/brightness.txt"
LAST_VAL="-1"
echo 255 > $BRIGHTNESS_FILE

while true; do
  if [ -f "$BRIGHTNESS_FILE" ]; then
    VAL=$(cat "$BRIGHTNESS_FILE")
    if [ "$VAL" != "$LAST_VAL" ]; then
      echo "$VAL" > /sys/class/backlight/*/brightness 2>/dev/null
      LAST_VAL="$VAL"
    fi
  fi
  sleep 0.5
done
