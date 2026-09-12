#!/usr/bin/env python3
"""
Autoflow watchdog
=================

Runs every 5 minutes from a systemd timer. Checks a fixed list of known
failure modes, repairs the ones with a known-safe fix, and writes what it did
to a file Home Assistant reads and emails.

    sudo systemctl enable --now autoflow-watchdog.timer

DESIGN RULES - these are the point of the whole thing

1.  IT NEVER TOUCHES A PUMP. Not start, not stop, not the relays. A process
    with authority over a mains motor connected to water is not worth the
    convenience of unattended repair.

2.  IT ONLY FIXES WHAT IT UNDERSTANDS. Every check below corresponds to a
    failure this system has actually had. There is no generic "something looks
    wrong, restart everything" branch, because that converts a visible fault
    into an invisible one.

3.  IT GIVES UP. Three repairs per check per hour, then it stops trying and
    only reports. A watchdog that restarts a service every five minutes
    forever is worse than no watchdog: the thing appears to work while being
    permanently broken.

4.  IT REPORTS THROUGH HOME ASSISTANT, NOT AROUND IT. Findings go to a JSON
    file; HA reads it and sends the mail. If HA is the thing that died, the
    message is simply delivered once it is back. That avoids a second copy of
    the Gmail credentials living on the Pi.

WHAT IT DELIBERATELY DOES NOT DO

  * restart relay_api while it is running and healthy - that service holds the
    GPIO state, and restarting it drops every relay, which would stop a pump
    mid-fill
  * clear disk space - reports only, because deleting the wrong thing
    unattended is worse than a full disk
  * anything about a welded relay or a stuck float - those need a human
"""

import glob
import json
import os
import subprocess
import time

STATE = "/var/lib/autoflow-watchdog/state.json"
REPORT = "/home/USER/homeassistant/config/watchdog_report.json"
HA_URL = "http://127.0.0.1:8123/"
MAX_FIXES_PER_HOUR = 3
USER = os.environ.get("AUTOFLOW_USER", "USER")


def sh(cmd, timeout=25):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip()
    except Exception as exc:
        return 1, str(exc)


def running(pattern):
    return sh(["pgrep", "-f", pattern])[0] == 0


def unit_active(unit):
    return sh(["systemctl", "is-active", "--quiet", unit])[0] == 0


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(s, f)


def may_fix(state, key):
    """Rate limit: at most MAX_FIXES_PER_HOUR attempts per check."""
    now = time.time()
    hits = [t for t in state.get(key, []) if now - t < 3600]
    state[key] = hits
    return len(hits) < MAX_FIXES_PER_HOUR


def record_fix(state, key):
    state.setdefault(key, []).append(time.time())


# ---------------------------------------------------------------- checks

def check_home_assistant(state, acted, failed):
    """The one thing Home Assistant cannot report about itself."""
    code, _ = sh(["curl", "-fsS", "-m", "10", "-o", "/dev/null", HA_URL])
    if code == 0:
        return
    if not may_fix(state, "ha"):
        failed.append("Home Assistant not responding - already restarted "
                      f"{MAX_FIXES_PER_HOUR} times this hour, not trying again")
        return
    record_fix(state, "ha")
    sh(["docker", "restart", "homeassistant"], timeout=90)
    acted.append("Home Assistant was not answering on port 8123 - container restarted")


def check_kiosk(state, acted, failed):
    """Chromium died, or never launched. Seen 18-Aug: labwc was up, the
    autostart loop was still waiting for HA, and the panel sat black.

    The autostart script only ever launches the browser once, so nothing
    relaunches it if it crashes later."""
    if not running("labwc"):
        failed.append("Compositor (labwc) is not running - needs a look, "
                      "not something to restart blindly")
        return
    if running("chromium"):
        return
    if not may_fix(state, "kiosk"):
        failed.append("Kiosk browser keeps dying - gave up after "
                      f"{MAX_FIXES_PER_HOUR} relaunches this hour")
        return

    uid = sh(["id", "-u", USER])[1] or "1000"
    rt = f"/run/user/{uid}"
    socks = [os.path.basename(p) for p in glob.glob(f"{rt}/wayland-*")
             if not p.endswith(".lock")]
    if not socks:
        failed.append("Kiosk browser is not running and no Wayland socket was "
                      "found - cannot relaunch it")
        return

    record_fix(state, "kiosk")
    subprocess.Popen(
        ["runuser", "-u", USER, "--", "env",
         f"XDG_RUNTIME_DIR={rt}", f"WAYLAND_DISPLAY={socks[0]}",
         "chromium", "--password-store=basic", "--kiosk",
         "--noerrdialogs", "--disable-infobars", HA_URL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    acted.append("Kiosk browser was not running - relaunched it")


def check_unit(state, acted, failed, unit, label):
    if unit_active(unit):
        return
    if not may_fix(state, unit):
        failed.append(f"{label} keeps stopping - gave up after "
                      f"{MAX_FIXES_PER_HOUR} restarts this hour")
        return
    record_fix(state, unit)
    code, _ = sh(["systemctl", "restart", unit])
    if code == 0:
        acted.append(f"{label} had stopped - restarted it")
    else:
        failed.append(f"{label} had stopped and would not restart")


def check_disk(state, acted, failed):
    """Report only. Deleting things unattended is worse than a full disk."""
    code, out = sh(["df", "--output=pcent,target", "/"])
    if code != 0:
        return
    try:
        pct = int(out.splitlines()[1].strip().split("%")[0])
    except Exception:
        return
    if pct >= 90:
        failed.append(f"Root filesystem is {pct}% full - needs attention, "
                      "the watchdog will not delete anything by itself")


def check_backup(state, acted, failed):
    """Report a failed or stale backup. Never tries to fix one."""
    path = "/home/USER/homeassistant/config/backup_status.json"
    try:
        with open(path) as f:
            s = json.load(f)
    except Exception:
        failed.append("No backup status file - the nightly backup may never "
                      "have run")
        return

    if not s.get("ok", False):
        failed.append("Last backup FAILED: " + str(s.get("error", "unknown")))
        return

    try:
        age_days = (time.time() - os.path.getmtime(path)) / 86400.0
    except Exception:
        return
    if age_days > 3:
        failed.append("Newest backup is %.1f days old - the nightly job has "
                      "stopped running" % age_days)


def check_audio(state, acted, failed):
    """Speaker mixer must stay near full. Below 90% is a silent fault.

    Addressed by card NAME ("Device") rather than index. Card numbering
    shuffles between boots exactly as ttyACM numbering does, and this project
    has already been bitten by that three times.
    """
    out = _amixer_get()
    if out is None:
        return
    if out >= 90:
        return
    if not may_fix(state, "audio"):
        failed.append("Speaker mixer keeps dropping to %d%% - gave up after "
                      "%d resets this hour" % (out, MAX_FIXES_PER_HOUR))
        return
    record_fix(state, "audio")
    sh(["amixer", "-c", "Device", "sset", "Speaker", "100%"])
    sh(["alsactl", "store"])
    after = _amixer_get()
    if after is not None and after >= 90:
        acted.append("Speaker mixer had fallen to %d%% (about -%.1f dB of lost "
                     "output) - reset to 100%%" % (out, (100 - out) * 0.63))
    else:
        failed.append("Speaker mixer is at %d%% and would not reset" % out)


def _amixer_get():
    """Current Speaker volume as a percentage, or None if unreadable."""
    import re as _re
    code, out = sh(["amixer", "-c", "Device", "sget", "Speaker"])
    if code != 0:
        return None
    m = _re.search(r"\[(\d+)%\]", out)
    return int(m.group(1)) if m else None


def check_mic(state, acted, failed):
    """The mic's PLAYBACK path must stay muted.

    This is a monitor loop, not a recording setting: whatever the microphone
    hears goes straight to the speaker. Found at 54% (+16.81 dB) on
    28-Aug-2026, which is why room noise was audible through the alerts.
    """
    import re as _re

    code, out = sh(["amixer", "-c", "Device", "sget", "Mic"])
    if code != 0:
        return

    m = _re.search(r"Playback \d+ \[(\d+)%\].*?\[(on|off)\]", out)
    if not m:
        return

    pct, switch = int(m.group(1)), m.group(2)
    if pct == 0 and switch == "off":
        return

    if not may_fix(state, "mic"):
        failed.append("Mic monitor keeps unmuting (now %d%%, switch %s) - "
                      "gave up after %d resets this hour"
                      % (pct, switch, MAX_FIXES_PER_HOUR))
        return

    record_fix(state, "mic")
    sh(["amixer", "-c", "Device", "sset", "Mic", "0%"])
    sh(["amixer", "-c", "Device", "sset", "Mic", "mute"])
    sh(["amixer", "-c", "Device", "sset", "Mic", "nocap"])
    sh(["alsactl", "store"])

    code, out = sh(["amixer", "-c", "Device", "sget", "Mic"])
    m2 = _re.search(r"Playback \d+ \[(\d+)%\].*?\[(on|off)\]", out or "")
    if m2 and int(m2.group(1)) == 0 and m2.group(2) == "off":
        acted.append("Mic monitor path was live (%d%%, switch %s) - room noise "
                     "was being fed to the speaker. Muted." % (pct, switch))
    else:
        failed.append("Mic monitor path is live and would not mute")


def main():
    state = load_state()
    acted, failed = [], []

    check_home_assistant(state, acted, failed)
    check_kiosk(state, acted, failed)
    # relay_api is only restarted when it has actually STOPPED. A stopped
    # service has already released the GPIO, so the relays are open and no
    # pump can be running through them - restarting is safe. A running but
    # slow relay_api is deliberately left alone.
    check_unit(state, acted, failed, "relayapi", "Relay API")
    check_unit(state, acted, failed, "screendim", "Screen dimmer")
    check_unit(state, acted, failed, "dvralarm", "DVR alarm listener")
    check_unit(state, acted, failed, "mpd", "Audio player")
    check_disk(state, acted, failed)
    check_audio(state, acted, failed)
    check_mic(state, acted, failed)
    check_backup(state, acted, failed)

    save_state(state)

    # Silence means healthy. Only bump the sequence number when there is
    # something worth an email, so Home Assistant has an unambiguous trigger.
    try:
        with open(REPORT) as f:
            prev = json.load(f)
    except Exception:
        prev = {}

    report = {
        "seq": prev.get("seq", 0) + (1 if (acted or failed) else 0),
        "checked": time.strftime("%Y-%m-%d %H:%M:%S"),
        "healthy": not (acted or failed),
        "acted": acted,
        "failed": failed,
    }
    tmp = REPORT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(report, f, indent=2)
    os.replace(tmp, REPORT)

    for line in acted + failed:
        print(line)


if __name__ == "__main__":
    main()
