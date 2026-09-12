#!/usr/bin/env python3
"""
Autoflow - Hikvision Alarm Host listener
========================================

Listens on TCP 7200 for the DVR's alarm push and fires a Home Assistant
webhook. That is the whole job.

WHY THIS IS SO SIMPLE

The DVR's alarm-host payload is a proprietary binary struct. It was captured
and partially decoded on 18-Aug-2026:

    byte 0        = 11 + 24 * N   (N = number of alarm records)
    total length  = 275 + 524 * N
    bytes 7-10    = DVR IP, little-endian
    offset 0x20F  = timestamp, big-endian: YYYY(2) M D H M S
    each record   = 400 bytes, and RECORDS ARE IDENTICAL to each other

That last point is the important one. A message reporting four simultaneous
alarms contains four byte-for-byte identical records - so the payload does NOT
say which camera fired. Decoding further would not have produced the answer.

*** THE ASSUMPTION THIS RESTS ON ***

The AcuSense vehicle rule is configured on the STREET VIEW camera ONLY.
Therefore any alarm arriving here is Street View, by elimination rather than by
reading it out of the packet.

IF YOU EVER ADD A DETECTION RULE TO ANOTHER CAMERA, THIS BECOMES WRONG - every
alert will still say Street View and nothing will complain. Change CAMERA below
and revisit, or go back to the ISAPI alert stream, which does name the channel.

INSTALL
    sudo cp dvr_alarm.py /home/USER/dvr_alarm.py
    sudo systemctl enable --now dvralarm
"""

import os
import datetime
import json
import socket
import socketserver
import sys
import urllib.request

LISTEN_HOST = os.environ.get("DVR_LISTEN_HOST", "")      # the Pi's address on the DVR's network
LISTEN_PORT = 7200

# Home Assistant runs with network_mode: host on this machine.
WEBHOOK = ("http://127.0.0.1:8123/api/webhook/"
           + os.environ.get("DVR_WEBHOOK_ID", ""))

# See the assumption above before changing this.
CAMERA = os.environ.get("DVR_CAMERA", "camera_1")

# The DVR re-sends every few seconds while a vehicle sits in frame. The real
# cooldown lives in the Home Assistant automation where it is visible and
# adjustable; this only stops us hammering the webhook in between.
MIN_GAP_SECONDS = 5

_last_sent = 0.0


def decode(data):
    """Pull out what the payload does reliably contain."""
    info = {"bytes": len(data), "records": None, "dvr_time": None}
    if len(data) >= 1:
        n = (data[0] - 11) / 24
        if n == int(n) and n > 0:
            info["records"] = int(n)
    # Timestamp position depends on the record count, so locate it by the
    # 0x07EA (2026) year marker rather than a fixed offset that will rot.
    for i in range(len(data) - 7):
        if data[i] == 0x07 and 0x00 < data[i + 2] <= 12 and 0 < data[i + 3] <= 31:
            y = (data[i] << 8) | data[i + 1]
            if 2020 <= y <= 2099 and data[i + 4] < 24 and data[i + 5] < 60:
                info["dvr_time"] = "%04d-%02d-%02d %02d:%02d:%02d" % (
                    y, data[i + 2], data[i + 3],
                    data[i + 4], data[i + 5], data[i + 6])
                break
    return info


def fire(info):
    global _last_sent
    now = datetime.datetime.now().timestamp()
    if now - _last_sent < MIN_GAP_SECONDS:
        print("  (within %ds of the last one - not sent)" % MIN_GAP_SECONDS,
              flush=True)
        return
    _last_sent = now

    body = json.dumps({
        "camera": CAMERA,
        "records": info["records"],
        "dvr_time": info["dvr_time"],
    }).encode()
    req = urllib.request.Request(
        WEBHOOK, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            print("  webhook -> HTTP %s" % r.status, flush=True)
    except Exception as exc:
        # Never die on a failed webhook. A listener that exits because Home
        # Assistant was restarting is worse than one that misses an event.
        print("  webhook FAILED: %s" % exc, flush=True)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        chunks = []
        try:
            while True:
                b = self.request.recv(8192)
                if not b:
                    break
                chunks.append(b)
        except (socket.timeout, OSError):
            pass

        data = b"".join(chunks)
        if not data:
            return

        info = decode(data)
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        print("%s  alarm: %d bytes, %s record(s), DVR clock %s"
              % (stamp, info["bytes"], info["records"], info["dvr_time"]),
              flush=True)
        fire(info)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print("Autoflow DVR alarm listener on %s:%d -> %s"
          % (LISTEN_HOST, LISTEN_PORT, CAMERA), flush=True)
    try:
        # Wait for the address rather than dying without it.
        #
        # EADDRNOTAVAIL means eth0 is not up yet - the DVR is unplugged, or the
        # switch is still booting. That is not a fault, it is a "not yet", and
        # exiting turns it into a systemd restart loop that spams the log and
        # triggers watchdog alerts for something nobody needs to fix.
        #
        # Anything else - port in use, permission denied - is a real problem
        # that waiting will not solve, so those still exit loudly.
        import errno
        import time

        waiting = False
        while True:
            try:
                Server((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()
                break
            except OSError as exc:
                if exc.errno != errno.EADDRNOTAVAIL:
                    raise
                if not waiting:
                    # Logged once, not every 30 seconds. A message repeated
                    # endlessly is one nobody reads.
                    print("%s is not up yet - waiting for it."
                          % LISTEN_HOST, flush=True)
                    waiting = True
                time.sleep(30)
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        sys.exit("Could not listen on %s:%d - %s" % (LISTEN_HOST, LISTEN_PORT, exc))
