#!/usr/bin/env python3
"""
Autoflow kiosk screen lock
==========================

Blanks the panel and makes the touchscreen genuinely inert. Unlocked by holding
a finger on the screen for six continuous seconds.

WHY A DAEMON AND NOT A HOME ASSISTANT DASHBOARD LOCK
----------------------------------------------------
Hiding buttons on a dashboard does not stop a child pressing the screen - the
touches still register, they just land on whatever is underneath. And a blanked
screen is worse, because now they are pressing things they cannot see.

This grabs the touchscreen with EVIOCGRAB, which gives this process *exclusive*
access to the device. While locked, Chromium receives no touch events at all.
Not hidden, not ignored by the page - never delivered. That is the difference
between a speed bump and a lock.

HOW IT WORKS
------------
  unlocked : device not grabbed, touches pass through normally.
             After IDLE_LOCK_SECONDS with no touch, it locks itself.
  locked   : backlight to 0, device grabbed. Touches go nowhere.
             Hold one finger for HOLD_TO_UNLOCK_SECONDS to unlock.
             Brightness ramps up as you hold, so it is obvious it is working
             rather than feeling broken.

Home Assistant can also drive it by writing "lock" or "unlock" into
screenlock_cmd.txt, and reads the current state from screenlock_state.txt.
Same file-based pattern as brightness_bridge.sh, for the same reason: the
config folder is the one place both the container and the host can see.

REQUIRES
--------
    sudo apt install -y python3-evdev

RUN
---
    sudo python3 /home/USER/screen_lock.py
(normally started by systemd - see screenlock.service)
"""

import os
import select
import sys
import time

try:
    from evdev import InputDevice, ecodes, list_devices
except ImportError:
    sys.exit("python3-evdev missing.  Run:  sudo apt install -y python3-evdev")


# ---------------------------------------------------------------- settings --
CONFIG_DIR = "/home/USER/homeassistant/config"
BRIGHTNESS_FILE = os.path.join(CONFIG_DIR, "brightness.txt")
CMD_FILE = os.path.join(CONFIG_DIR, "screenlock_cmd.txt")
STATE_FILE = os.path.join(CONFIG_DIR, "screenlock_state.txt")

HOLD_TO_UNLOCK_SECONDS = 6.0
IDLE_LOCK_SECONDS = 0           # manual lock only - see STATUS.md. 30s made the panel unusable.
BRIGHT_NORMAL = 255
BRIGHT_OFF = 0

# Brightness shown partway through the unlock hold, as feedback
HOLD_FEEDBACK = [(2.0, 8), (4.0, 25), (5.5, 60)]   # progressing, not finished


def set_brightness(value):
    """Write through brightness.txt so brightness_bridge.sh applies it.

    Deliberately not writing to /sys directly: the bridge already owns that,
    and two writers to one backlight is how you get flicker nobody can explain.
    """
    try:
        with open(BRIGHTNESS_FILE, "w") as f:
            f.write(str(int(value)))
    except OSError as exc:
        print(f"brightness write failed: {exc}", flush=True)


def write_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            f.write(state)
    except OSError:
        pass


def read_command():
    """Read and immediately clear any command left by Home Assistant."""
    try:
        if not os.path.exists(CMD_FILE):
            return None
        with open(CMD_FILE) as f:
            cmd = f.read().strip().lower()
        if cmd:
            with open(CMD_FILE, "w") as f:
                f.write("")
        return cmd or None
    except OSError:
        return None


def find_touchscreen():
    """Pick the touchscreen out of all input devices.

    Matched by capability rather than by name or event number, because event
    numbers move around between boots - the same class of problem as the
    ttyACM shuffle and the ALSA card index.
    """
    for path in list_devices():
        try:
            dev = InputDevice(path)
        except OSError:
            continue
        caps = dev.capabilities()
        if ecodes.EV_ABS not in caps or ecodes.EV_KEY not in caps:
            continue
        abs_codes = {code for code, _ in caps[ecodes.EV_ABS]}
        key_codes = set(caps[ecodes.EV_KEY])
        touch_axes = {ecodes.ABS_MT_POSITION_X, ecodes.ABS_X} & abs_codes
        if touch_axes and ecodes.BTN_TOUCH in key_codes:
            print(f"touchscreen: {dev.name} at {path}", flush=True)
            return dev
    return None


def main():
    dev = find_touchscreen()
    if dev is None:
        sys.exit("No touchscreen found. Is the panel connected?")

    locked = False
    grabbed = False
    last_activity = time.time()
    touch_start = None
    feedback_done = set()

    set_brightness(BRIGHT_NORMAL)
    write_state("unlocked")
    print("running - unlocked", flush=True)

    while True:
        # ---- commands from Home Assistant -------------------------------
        cmd = read_command()
        if cmd == "lock" and not locked:
            locked, touch_start, feedback_done = True, None, set()
            set_brightness(BRIGHT_OFF)
            if not grabbed:
                try:
                    dev.grab(); grabbed = True
                except OSError as exc:
                    print(f"grab failed: {exc}", flush=True)
            write_state("locked")
            print("locked (command)", flush=True)
        elif cmd == "unlock" and locked:
            locked = False
            if grabbed:
                try:
                    dev.ungrab()
                except OSError:
                    pass
                grabbed = False
            set_brightness(BRIGHT_NORMAL)
            last_activity = time.time()
            write_state("unlocked")
            print("unlocked (command)", flush=True)

        # ---- input ------------------------------------------------------
        r, _, _ = select.select([dev.fd], [], [], 0.2)
        if r:
            try:
                for event in dev.read():
                    if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                        if event.value == 1:                       # finger down
                            touch_start = time.time()
                            feedback_done = set()
                        else:                                      # finger up
                            touch_start = None
                            if locked:
                                set_brightness(BRIGHT_OFF)
                                feedback_done = set()
                    last_activity = time.time()
            except OSError:
                pass

        now = time.time()

        # ---- hold to unlock ---------------------------------------------
        if locked and touch_start is not None:
            held = now - touch_start
            for threshold, level in HOLD_FEEDBACK:
                if held >= threshold and threshold not in feedback_done:
                    set_brightness(level)
                    feedback_done.add(threshold)
            if held >= HOLD_TO_UNLOCK_SECONDS:
                locked = False
                if grabbed:
                    try:
                        dev.ungrab()
                    except OSError:
                        pass
                    grabbed = False
                set_brightness(BRIGHT_NORMAL)
                touch_start = None
                feedback_done = set()
                last_activity = now
                write_state("unlocked")
                print("unlocked (held)", flush=True)

        # ---- idle auto-lock ---------------------------------------------
        if not locked and IDLE_LOCK_SECONDS > 0:
            if now - last_activity > IDLE_LOCK_SECONDS:
                locked = True
                set_brightness(BRIGHT_OFF)
                if not grabbed:
                    try:
                        dev.grab(); grabbed = True
                    except OSError as exc:
                        print(f"grab failed: {exc}", flush=True)
                write_state("locked")
                print("locked (idle)", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
