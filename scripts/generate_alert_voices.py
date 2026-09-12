#!/usr/bin/env python3
"""
Autoflow - pre-rendered alert voice generator
=============================================

Renders every fixed alert phrase to an MP3 once, so that at runtime the Pi
plays a file instead of calling a cloud TTS service. That means:

  * zero CPU cost when an alert fires
  * alerts still work with the internet completely down
  * a far better voice than Google Translate, at no runtime cost

RUN THIS ON YOUR MAC, not on the Pi. It writes straight into the mounted
Home Assistant config share, so the files land on the Pi automatically.

Setup (one time):

    python3 -m venv ~/tts-env
    source ~/tts-env/bin/activate
    pip install edge-tts

Then:

    python3 generate_alert_voices.py

To audition a different voice, change VOICE below and run again.
Suggested JARVIS-adjacent options, all British male:

    en-GB-RyanNeural     calm, measured, closest to JARVIS   <- default
    en-GB-ThomasNeural   slightly brighter, younger
    en-GB-SoniaNeural    British female, very clear

Full list:  edge-tts --list-voices | grep en-GB
"""

import asyncio
import os
import sys

try:
    import edge_tts
except ImportError:
    sys.exit("edge-tts is not installed. Run:  pip install edge-tts")

# --------------------------------------------------------------------------
# SETTINGS
# --------------------------------------------------------------------------

VOICE = "en-GB-RyanNeural"

# Slightly slowed and lowered. JARVIS is unhurried - this is most of what
# makes a synthetic voice read as composed rather than robotic.
RATE = "-8%"
PITCH = "-4Hz"

OUTPUT_DIR = "<HA_CONFIG_MOUNT>/www/alerts"

# --------------------------------------------------------------------------
# PHRASES
#
# Key = filename (no extension). Referenced from scripts.yaml as
#       http://127.0.0.1:8123/local/alerts/<key>.mp3
#
# Written in a deliberately level register: no exclamation marks, no shouting.
# A calm voice saying something serious lands harder than an alarmed one, and
# stays tolerable when it fires at three in the morning.
# --------------------------------------------------------------------------

PHRASES = {
    # ---------------- KITCHEN (top tank) ----------------
    "kitchen_empty":
        "The kitchen tank is empty.",
    "kitchen_full_manual":
        "The kitchen tank is full, but the pump is still running in manual mode.",
    "kitchen_stopping_soon":
        "The kitchen tank is full. Stopping the pump in ten seconds.",
    "kitchen_auto_start":
        "The kitchen tank is empty. Starting the pump automatically.",
    "kitchen_auto_stop":
        "The kitchen tank is full. Stopping the pump.",

    # ---------------- BATH (bottom tank) ----------------
    "bath_empty":
        "The bath tank is empty.",
    "bath_full_manual":
        "The bath tank is full, but the pump is still running in manual mode.",
    "bath_stopping_soon":
        "The bath tank is full. Stopping the pump in ten seconds.",
    "bath_auto_start":
        "The bath tank is empty. Starting the pump automatically.",
    "bath_auto_stop":
        "The bath tank is full. Stopping the pump.",

    "kitchen_full_stopping":
        "The kitchen tank is already full. The pump is turning off in twenty seconds.",
    "bath_full_stopping":
        "The bath tank is already full. The pump is turning off in twenty seconds.",

    # ---------------- UTILITY PUMP ----------------
    "utility_started":
        "The utility pump is now running.",
    "utility_stopped":
        "The utility pump has stopped.",
    "utility_no_supply":
        "No water is arriving on the utility line.",

    # ---------------- DRY RUN ----------------
    "dry_run_kitchen":
        "Dry run detected on the kitchen pump. Shutting it down to protect the motor.",
    "dry_run_bath":
        "Dry run detected on the bath pump. Shutting it down to protect the motor.",
    "dry_run_utility":
        "Dry run detected on the utility pump. Shutting it down to protect the motor.",
    "dry_run_generic":
        "A pump is running dry. Shutting it down to protect the motor.",

    # ---------------- MAX RUN TIME ----------------
    "maxrun_kitchen":
        "The kitchen pump has exceeded its maximum run time. Shutting it down.",
    "maxrun_bath":
        "The bath pump has exceeded its maximum run time. Shutting it down.",
    "maxrun_utility":
        "The utility pump has exceeded its maximum run time. Shutting it down.",
    "maxrun_generic":
        "A pump has exceeded its maximum run time. Shutting it down.",

    # ---------------- INTERLOCK ----------------
    "interlock_bath_utility":
        "The bath pump and the utility pump cannot run at the same time. "
        "The pump you just started has been stopped.",
    "interlock_two_pump_limit":
        "Only two pumps may run at once. The pump you just started has been stopped.",
    "interlock_generic":
        "That combination of pumps is not permitted. The request has been cancelled.",

    # ---------------- POWER ----------------
    "power_cut":
        "Mains power to the pumps has failed. No pump can run until it returns.",
    "power_restored":
        "Mains power to the pumps has been restored.",
    "voltage_high":
        "Supply voltage is above the safe range. Please check the incoming mains.",
    "voltage_low":
        "Supply voltage is below the safe range. Running a pump now may damage the motor.",

    # ---------------- FLOAT / SENSOR FAULTS ----------------
    "float_conflict":
        "A tank is reporting full and empty at the same time. "
        "A float switch has probably failed.",
    "sensor_offline":
        "A power monitor has stopped responding. Pump protection may be reduced.",
    "modbus_error":
        "Communication with the power monitors has failed. Please check the USB connections.",

    # ---------------- SYSTEM ----------------
    "system_online":
        "Autoflow is online. All systems are nominal.",
    "system_restarting":
        "Autoflow is restarting. Pump control will be unavailable briefly.",
    "quiet_hours_blocked":
        "The tank is empty, but it is currently quiet hours. "
        "The pump will start when quiet hours end.",
    "manual_mode_reminder":
        "This pump is in manual mode. It will not stop by itself.",
    "all_tanks_full":
        "All tanks are full.",

    # ---------------- GENERIC ATTENTION ----------------
    "attention":
        "Attention.",
    "warning":
        "Warning.",
    "critical":
        "Critical alert.",
    "acknowledged":
        "Acknowledged.",
    "test":
        "This is a test of the Autoflow alert system. No action is required.",
}


# --------------------------------------------------------------------------

async def render(name: str, text: str) -> None:
    path = os.path.join(OUTPUT_DIR, f"{name}.mp3")
    communicate = edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH)
    await communicate.save(path)
    size = os.path.getsize(path) // 1024
    print(f"  ok  {name:26s} {size:>4d} KB   \"{text[:52]}...\"")


async def main() -> None:
    if not os.path.isdir(os.path.dirname(OUTPUT_DIR)):
        sys.exit(
            f"Cannot see {os.path.dirname(OUTPUT_DIR)}\n"
            "Is the Pi's config folder mounted? Reconnect it in Finder and retry."
        )

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"\nVoice : {VOICE}   (rate {RATE}, pitch {PITCH})")
    print(f"Output: {OUTPUT_DIR}")
    print(f"Phrases: {len(PHRASES)}\n")

    failed = []
    for name, text in PHRASES.items():
        try:
            await render(name, text)
        except Exception as exc:                      # noqa: BLE001
            failed.append(name)
            print(f"  FAIL {name}: {exc}")

    print(f"\nDone. {len(PHRASES) - len(failed)} of {len(PHRASES)} rendered.")
    if failed:
        print("Failed:", ", ".join(failed))
    print("\nAudition them straight from Finder - they are just MP3 files.")
    print("Happy with the voice? Say so and the alerts get wired up to use them.\n")


if __name__ == "__main__":
    asyncio.run(main())
