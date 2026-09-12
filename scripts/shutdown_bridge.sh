#!/bin/bash
# =====================================================================
# Autoflow - shutdown bridge
# =====================================================================
#
# Home Assistant's shell_command runs INSIDE the container, where
# `shutdown` reaches nothing. So HA writes a file and this, running as
# root on the host, acts on it - the same arrangement brightness_bridge.sh
# uses for the backlight.
#
# Started from root's crontab:
#     @reboot bash /home/<USER>/shutdown_bridge.sh &
#
# WHY THE FILE IS DELETED AT STARTUP
#
# If the Pi loses power between HA writing the request and the shutdown
# completing, the file survives the outage. Without the line below, the
# next boot would find it, honour it, and shut down again - a machine that
# powers itself off every time you turn it on, for a reason nobody would
# guess. So a request is only ever honoured if it arrives after this
# script starts.
#
# WHY THE FILE IS REMOVED BEFORE THE VALUE IS CHECKED
#
# Otherwise unexpected content would sit there being re-read every two
# seconds, filling the log forever.

REQ="/home/<USER>/homeassistant/config/shutdown_request.txt"

rm -f "$REQ"
logger -t shutdown_bridge "watching $REQ"

while true; do
    if [ -f "$REQ" ]; then
        VAL=$(tr -d '[:space:]' < "$REQ" 2>/dev/null)
        rm -f "$REQ"

        if [ "$VAL" = "now" ]; then
            logger -t shutdown_bridge "shutdown requested from Home Assistant"
            /sbin/shutdown -h +0
            exit 0
        else
            logger -t shutdown_bridge "ignoring unexpected content: '$VAL'"
        fi
    fi
    sleep 2
done
