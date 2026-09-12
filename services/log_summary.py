#!/usr/bin/env python3
"""
Turn Home Assistant's log into something a human will actually read
====================================================================

Called by a command_line sensor. Prints one line of JSON.

WHY

Every fault this project has turned up was sitting in home-assistant.log for
weeks before anyone noticed: the meters not answering, the email service
failing, the alert sounds that were never generated. The information was there.
Nobody reads a log file.

So this counts what happened in the last 24 hours and says it in plain English.
"Kitchen pump's power meter did not answer - 35 times" is something you can act
on. "pymodbus.logging No response received after 3 retries" is not.

Anything it does not recognise is passed through as-is rather than hidden, so
an unfamiliar error still reaches you - just without a friendly name.
"""

import json
import os
import re
from datetime import datetime, timedelta

LOGS = ["/config/home-assistant.log", "/config/home-assistant.log.1"]
HOURS = 24

# Matched in order, first hit wins. Keep the specific ones above the general.
PATTERNS = [
    (r"pzem_kitchen",           "Kitchen pump's power meter did not answer"),
    (r"pzem_bath",              "Bath pump's power meter did not answer"),
    (r"pzem_utility",           "Utility pump's power meter did not answer"),
    (r"could not open port",    "The Pi could not open the meter cable - is it plugged in?"),
    (r"pymodbus|modbus",        "A power meter did not answer"),

    (r"smtp",                   "Could not send an email"),
    (r"mobile_app.*notify|Error sending notification",
                                "Could not send a notification to the phone"),

    (r"ClientConnectorDNSError|Could not contact DNS|Network unreachable|Cannot connect to host",
                                "The Pi could not reach the internet"),
    (r"\[homeassistant\.components\.mpd\]",
                                "The speaker service was unreachable"),

    (r"Login attempt|invalid authentication",
                                "A login failed - usually the kiosk screen after a restart"),
    (r"are missing or not currently avail",
                                "Something referred to a device that was not ready yet"),
    (r"is taking over \d+ seconds",
                                "A sensor was slow to update"),
    (r"Timer got out of sync",  "The Pi was busy and a timer slipped"),
    (r"custom integration",     "A custom add-on loaded (normal, not a fault)"),
]

STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def friendly(line):
    for pat, text in PATTERNS:
        if re.search(pat, line, re.I):
            return text
    # Unrecognised: strip the timestamp and module noise, keep the message.
    cleaned = re.sub(r"^\S+ \S+ ", "", line)
    cleaned = re.sub(r"^(ERROR|WARNING) \([^)]*\) ", "", cleaned)
    return cleaned.strip()[:110]


def main():
    cutoff = datetime.now() - timedelta(hours=HOURS)
    counts, newest, newest_at = {}, None, None
    errors = warnings = 0

    for path in LOGS:
        if not os.path.isfile(path):
            continue
        try:
            fh = open(path, errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                if " ERROR " not in line and " WARNING " not in line:
                    continue
                m = STAMP.match(line)
                if m:
                    try:
                        when = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
                    if when < cutoff:
                        continue
                else:
                    continue

                is_error = " ERROR " in line
                if is_error:
                    errors += 1
                else:
                    warnings += 1

                text = friendly(line)
                counts[text] = counts.get(text, 0) + 1
                if newest_at is None or when > newest_at:
                    newest_at, newest = when, text

    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
    summary = [
        "%s  -  %d time%s" % (t, n, "" if n == 1 else "s")
        for t, n in ranked
    ]

    if not summary:
        headline = "Nothing to report in the last 24 hours"
    elif errors == 0:
        headline = "%d warning%s, no errors" % (warnings, "" if warnings == 1 else "s")
    else:
        headline = "%d error%s and %d warning%s" % (
            errors, "" if errors == 1 else "s",
            warnings, "" if warnings == 1 else "s")

    print(json.dumps({
        "count": errors,
        "warnings": warnings,
        "headline": headline,
        "summary": summary,
        "latest": newest or "",
        "latest_at": newest_at.strftime("%d %b %H:%M") if newest_at else "",
    }))


if __name__ == "__main__":
    main()
