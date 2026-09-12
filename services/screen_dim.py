#!/usr/bin/env python3
"""
Autoflow kiosk backlight manager
================================

Owns the panel backlight. Nothing else writes it.

WHY ONE OWNER
-------------
Two things wanted to set brightness: this daemon (dim when idle) and Home
Assistant (Off / Dim / Full / Auto). With both writing, the screen fights
itself - Home Assistant reapplying a level every few minutes wakes a panel this
had just dimmed, and neither side looks wrong on its own.

So Home Assistant no longer touches the backlight. It writes the level it
*wants when the screen is awake* into bright_normal.txt, and this decides what
actually reaches the panel:

    awake  ->  the level Home Assistant asked for
    idle   ->  0

That keeps "how bright should it be" - a policy, Home Assistant's job - apart
from "is anyone looking at it", which is a fact and this daemon's job.

FILES  (all in the config dir, visible to both the host and the container)
-------------------------------------------------------------------------
    brightness.txt      what brightness_bridge.sh applies - written ONLY here
    bright_normal.txt   desired awake level 0-255, written by Home Assistant
    idle_dim.txt        "on" / "off" - enable or disable idle dimming
    wake.txt            write anything here to force a wake (used by alerts)

REQUIRES
--------
    sudo apt install -y python3-evdev
"""

import os
import select
import sys
import time

try:
    from evdev import InputDevice, ecodes, list_devices
except ImportError:
    sys.exit("python3-evdev missing.  Run:  sudo apt install -y python3-evdev")


CONFIG_DIR = "/home/USER/homeassistant/config"
BRIGHTNESS_FILE = os.path.join(CONFIG_DIR, "brightness.txt")
NORMAL_FILE = os.path.join(CONFIG_DIR, "bright_normal.txt")
ENABLE_FILE = os.path.join(CONFIG_DIR, "idle_dim.txt")
WAKE_FILE = os.path.join(CONFIG_DIR, "wake.txt")
LOCK_STATE_FILE = os.path.join(CONFIG_DIR, "screenlock_state.txt")

IDLE_SECONDS = 30
BRIGHT_DIM = 0
DEFAULT_NORMAL = 255


def _read_int(path, fallback):
    try:
        with open(path) as f:
            return max(0, min(255, int(float(f.read().strip()))))
    except (OSError, ValueError):
        return fallback


def set_brightness(value):
    """Write through brightness.txt so brightness_bridge.sh applies it.

    Never writes /sys directly - the bridge owns that, and two writers on one
    backlight produces flicker nobody can later explain.
    """
    try:
        with open(BRIGHTNESS_FILE, "w") as f:
            f.write(str(int(value)))
    except OSError as exc:
        print(f"brightness write failed: {exc}", flush=True)


def dimming_enabled():
    """Defaults ON when the file is missing - a missing file must not leave the
    panel lit permanently."""
    try:
        with open(ENABLE_FILE) as f:
            return f.read().strip().lower() != "off"
    except OSError:
        return True


def wake_requested():
    try:
        if not os.path.exists(WAKE_FILE):
            return False
        with open(WAKE_FILE) as f:
            asked = bool(f.read().strip())
        if asked:
            with open(WAKE_FILE, "w") as f:
                f.write("")
        return asked
    except OSError:
        return False


def find_touchscreen():
    """Match by capability, not event number - /dev/input/event* numbering
    changes between boots, the same class of problem as the ttyACM shuffle."""
    for path in list_devices():
        try:
            dev = InputDevice(path)
        except OSError:
            continue
        caps = dev.capabilities()
        if ecodes.EV_ABS not in caps or ecodes.EV_KEY not in caps:
            continue
        abs_codes = {code for code, _ in caps[ecodes.EV_ABS]}
        if ({ecodes.ABS_MT_POSITION_X, ecodes.ABS_X} & abs_codes
                and ecodes.BTN_TOUCH in set(caps[ecodes.EV_KEY])):
            print(f"touchscreen: {dev.name} at {path}", flush=True)
            return dev
    return None


def screen_locked():
    """True when screen_lock.py has the panel locked.

    Missing or unreadable file means unlocked. A lock that cannot be read
    should not be able to leave the screen dark with no way back.
    """
    try:
        with open(LOCK_STATE_FILE) as f:
            return f.read().strip().lower() == "locked"
    except OSError:
        return False


def main():
    dev = find_touchscreen()
    if dev is None:
        sys.exit("No touchscreen found. Is the panel connected?")

    dimmed = False
    applied = None
    last_touch = time.time()
    print(f"backlight manager running - idle {IDLE_SECONDS}s", flush=True)

    while True:
        touched = False
        r, _, _ = select.select([dev.fd], [], [], 0.4)
        if r:
            try:
                for _ in dev.read():
                    pass
            except OSError:
                pass
            touched = True

        if wake_requested():
            touched = True

        if touched:
            last_touch = time.time()
            if dimmed:
                dimmed = False
                print("woke", flush=True)

        normal = _read_int(NORMAL_FILE, DEFAULT_NORMAL)

        if dimming_enabled():
            if not dimmed and time.time() - last_touch > IDLE_SECONDS:
                dimmed = True
                print("dimmed", flush=True)
        elif dimmed:
            dimmed = False
            last_touch = time.time()
            print("dimming disabled - restored", flush=True)

        # Single decision point, single write. Only touches the panel when the
        # target actually changes, so this loop is cheap despite running 2-3
        # times a second.
        # While the screen is locked, screen_lock.py owns the backlight -
        # it blanks the panel and ramps it up as you hold to unlock. This
        # loop must not write brightness.txt at the same time, or the two
        # fight and a locked panel lights itself back up within half a
        # second.
        #
        # `applied` is deliberately reset so that the first pass after
        # unlocking writes unconditionally, rather than assuming the value
        # left behind by the lock.
        if screen_locked():
            applied = None
            time.sleep(0.4)
            continue

        target = BRIGHT_DIM if dimmed else normal
        if target != applied:
            set_brightness(target)
            applied = target


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
