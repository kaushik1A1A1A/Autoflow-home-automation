from flask import Flask, jsonify
from gpiozero import OutputDevice, Button
import glob
import os
import subprocess
import time

app = Flask(__name__)

# Pin mapping for the 8-Channel Relay Board.
#
# DEAD PINS - DO NOT REUSE: GPIO 24, GPIO 5, GPIO 6.
# Verified 24-Aug-2026 with the relay board unplugged: driven low, all three
# still read high, while GPIO 17 behaved correctly. The pins are damaged, not
# the board and not the code.
#
# Relays 5, 7 and 8 moved to GPIO 4, 18 and 21 (header pins 7, 12 and 40).
# GPIO 7 and 8 would have been the safer choice - they idle high, which suits
# an active-LOW board - but SPI is enabled here and claims both.
PINS = {
    1: 17, # Relay 1 (Pump 1 - Kitchen)   pin 11
    2: 27, # Relay 2 (Pump 2 - Bath)      pin 13
    3: 22, # Relay 3 (Pump 3 - Sump)      pin 15
    4: 23, # Relay 4 (Enclosure Fan)      pin 16
    5: 4,  # Relay 5 (Spare)              pin 7   was GPIO 24 - dead
    6: 25, # Relay 6 (Spare)              pin 22
    7: 18, # Relay 7 (Spare)              pin 12  was GPIO 5  - dead
    8: 21  # Relay 8 (Spare)              pin 40  was GPIO 6  - dead
}

# Ultra-safe Pins for Floats (Guaranteed no kernel conflict)
FLOAT_PINS = {
    'top_full': 12,
    'top_empty': 13,
    'bottom_full': 16,
    'bottom_empty': 20,
    'sump1_full': 19,
    'sump2_full': 26
}

relays = {}
for r_id, pin in PINS.items():
    relays[r_id] = OutputDevice(pin, active_high=False, initial_value=False)

# Initialize Float Switches with internal pull-up enabled
floats = {}
for name, pin in FLOAT_PINS.items():
    floats[name] = Button(pin, pull_up=True, bounce_time=0.1)

@app.route('/relay/<int:relay_id>/<action>')
def control_relay(relay_id, action):
    if relay_id in relays:
        if action == 'on':
            relays[relay_id].on()
            return f"Relay {relay_id} ON", 200
        elif action == 'off':
            relays[relay_id].off()
            return f"Relay {relay_id} OFF", 200
    return "Invalid Relay or Action", 400

def _cpu_percent(sample=0.3):
    """CPU usage across a short sample window.

    /proc/stat gives cumulative jiffies since boot, so a single read tells you
    the average since power-on - useless. Two reads a moment apart give the
    usage during that window, which is what you actually want.
    """
    def snapshot():
        with open('/proc/stat') as f:
            parts = [float(x) for x in f.readline().split()[1:]]
        idle = parts[3] + parts[4]          # idle + iowait
        return sum(parts), idle

    total1, idle1 = snapshot()
    time.sleep(sample)
    total2, idle2 = snapshot()

    d_total = total2 - total1
    d_idle = idle2 - idle1
    if d_total <= 0:
        return 0.0
    return round((1.0 - d_idle / d_total) * 100, 1)


def _mem_percent():
    """Percentage of RAM actually in use.

    Uses MemAvailable rather than MemFree. MemFree excludes cache and buffers,
    which Linux will hand back the instant anything needs them - so MemFree
    makes a healthy machine look like it is out of memory.
    """
    info = {}
    with open('/proc/meminfo') as f:
        for line in f:
            key, _, rest = line.partition(':')
            info[key] = float(rest.strip().split()[0])
    total = info.get('MemTotal', 0)
    available = info.get('MemAvailable', info.get('MemFree', 0))
    if total <= 0:
        return 0.0
    return round((1.0 - available / total) * 100, 1)


def _temp_c():
    """SoC temperature in Celsius.

    This is the reading that made the endpoint necessary. Home Assistant's
    System Monitor integration runs inside the container, which cannot see
    /sys/class/thermal, so it produced no temperature entity at all. Read here
    on the host it is simply available.

    Tries the thermal zones in order and returns the first plausible value.
    """
    for path in sorted(glob.glob('/sys/class/thermal/thermal_zone*/temp')):
        try:
            with open(path) as f:
                milli = float(f.read().strip())
            celsius = milli / 1000.0
            if 0 < celsius < 150:
                return round(celsius, 1)
        except (OSError, ValueError):
            continue
    return None


def _uptime_hours():
    try:
        with open('/proc/uptime') as f:
            return round(float(f.readline().split()[0]) / 3600, 1)
    except (OSError, ValueError):
        return None


@app.route('/system/stats')
def system_stats():
    """Host health for the Home Assistant dashboard and the fan automation."""
    return jsonify({
        'cpu_percent': _cpu_percent(),
        'mem_percent': _mem_percent(),
        'temp_c': _temp_c(),
        'uptime_hours': _uptime_hours(),
    }), 200


@app.route('/relay/<int:relay_id>/state')
def relay_state(relay_id):
    """Report the ACTUAL relay state so Home Assistant can stop guessing.

    Without this, HA assumes a switch is in whatever state it last commanded.
    If this service restarts (systemd has Restart=always) the GPIO objects
    reinitialise to off and the pump physically stops, while HA carries on
    showing it as running, counting toward the max run time, and possibly
    raising a dry-run alert about a pump that is not running.

    gpiozero's .value already accounts for active_high=False, so 1 means the
    relay is energised.
    """
    if relay_id not in relays:
        return "unknown", 404
    return ("1" if relays[relay_id].value else "0"), 200


@app.route('/relay/status')
def relay_status_all():
    """All relay states in one call, for debugging."""
    return jsonify({r_id: int(dev.value) for r_id, dev in relays.items()}), 200


@app.route('/float/status')
def float_status():
    status = {name: (1 if btn.is_pressed else 0) for name, btn in floats.items()}
    return jsonify(status), 200

# Legacy routes
@app.route('/pump1/<action>')
def p1(action):
    return control_relay(1, action)
@app.route('/pump2/<action>')
def p2(action):
    return control_relay(2, action)
@app.route('/pump3/<action>')
def p3(action):
    return control_relay(3, action)

@app.route('/internet/<action>', methods=['GET'])
def internet_control(action):
    """DVR internet gate - see /internet/state for what 'on' actually means."""
    if action == 'on':
        _set_forward(1)
        for table, rule in _INTERNET_RULES:
            _rule_ensure(table, rule)
        return "Internet ON", 200
    if action == 'off':
        _set_forward(0)
        for table, rule in _INTERNET_RULES:
            _rule_remove(table, rule)
        return "Internet OFF", 200
    return "Invalid", 400


# The three things that together let the DVR out. All of them must be true for
# the gate to be open; the sysctl alone is enough to close it.
_INTERNET_RULES = [
    ('nat', ['POSTROUTING', '-o', 'wlan0', '-j', 'MASQUERADE']),
    ('filter', ['FORWARD', '-i', 'eth0', '-o', 'wlan0', '-j', 'ACCEPT']),
    ('filter', ['FORWARD', '-i', 'wlan0', '-o', 'eth0',
                '-m', 'state', '--state', 'RELATED,ESTABLISHED', '-j', 'ACCEPT']),
]

_IPT = '/usr/sbin/iptables'


def _ipt(table, op, rule):
    cmd = ['sudo', _IPT]
    if table == 'nat':
        cmd += ['-t', 'nat']
    cmd += [op] + rule
    return subprocess.run(cmd, capture_output=True).returncode


def _rule_present(table, rule):
    return _ipt(table, '-C', rule) == 0


def _rule_ensure(table, rule):
    if not _rule_present(table, rule):
        _ipt(table, '-A', rule)


def _rule_remove(table, rule):
    # Loop: a rule can have been added more than once by the old code, which
    # appended without checking. Delete until none remain.
    for _ in range(10):
        if not _rule_present(table, rule):
            return
        _ipt(table, '-D', rule)


def _set_forward(value):
    subprocess.run(['sudo', '/usr/sbin/sysctl', '-w',
                    'net.ipv4.ip_forward=%d' % value], capture_output=True)


def _get_forward():
    try:
        with open('/proc/sys/net/ipv4/ip_forward') as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


@app.route('/internet/state')
def internet_state():
    """Real state, read from the kernel - not what we last commanded.

    Returns 1 only when routing is enabled AND every rule is in place. A
    half-configured gate reports 0: it does not pass traffic, so calling it
    'on' would be a lie of exactly the kind that hid this fault for hours.
    """
    if _get_forward() != 1:
        return "0", 200
    for table, rule in _INTERNET_RULES:
        if not _rule_present(table, rule):
            return "0", 200
    return "1", 200


if __name__ == '__main__':
    # SECURITY: bind to loopback only.
    #
    # This was 0.0.0.0, which meant ANY device on the network could switch the
    # pumps with a single unauthenticated request - there is no login on this
    # API - and could also call /internet/on, which runs sudo iptables commands.
    #
    # Home Assistant runs with network_mode: host on this same machine, so it
    # reaches 127.0.0.1 exactly as before. Nothing else needs access.
    # Served by waitress, not Flask's development server.
    #
    # The dev server handles one request at a time and warns against
    # production use. This API switches the pumps: if it wedges while one is
    # running, Home Assistant cannot stop it and the safety automations are
    # talking to a socket nobody is reading.
    #
    # threads=8 is generous for three pumps and a stats endpoint, but threads
    # are cheap and a blocked one must never delay a stop command.
    from waitress import serve
    serve(app, host='127.0.0.1', port=5000, threads=8)
