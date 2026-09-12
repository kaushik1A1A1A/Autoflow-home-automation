# Architecture

## Overview

A single-board computer runs Home Assistant in a container and acts as the
supervisor for three water pumps. Everything that must survive a Home Assistant
restart, or that needs privileges Home Assistant does not have inside a
container, runs on the host as a small independent service.

```
                    ┌───────────────────────────────┐
                    │   Touch panel (kiosk mode)     │
                    │   Wayland compositor +         │
                    │   browser, fullscreen          │
                    └───────────────┬────────────────┘
                                    │ localhost
┌───────────────────────────────────▼────────────────────────────────┐
│  Home Assistant (container, host networking)                       │
│    automations · template sensors · utility meters · dashboards    │
└───┬─────────────┬───────────────┬──────────────┬───────────────────┘
    │ HTTP        │ Modbus RTU    │ files        │ RTSP / WebRTC
    │ loopback    │ (3 × TTL      │ (bridge      │
    │             │  UART)        │  pattern)    │
┌───▼──────┐  ┌───▼──────────┐ ┌──▼───────────┐ ┌▼──────────────────┐
│ relay    │  │ 3 × energy   │ │ host daemons │ │ stream proxy →    │
│ API      │  │ meters       │ │ brightness   │ │ recorder (own     │
│ (host)   │  │              │ │ dimmer, lock │ │ isolated subnet)  │
└───┬──────┘  └──────────────┘ │ shutdown     │ └───────────────────┘
    │ GPIO                     │ watchdog     │
┌───▼──────────────┐           └──────────────┘
│ 8-ch relay board │
│ (opto-isolated)  │
└───┬──────────────┘
    │ mains switching
┌───▼──────────────┐
│ 3 pumps          │
└──────────────────┘
```

## Why a REST API for GPIO

Home Assistant runs in a container. Reaching host GPIO from inside it means
either granting the container broad device privileges and hoping the device
nodes stay stable, or putting a small service on the host and talking to it over
loopback. The second is chosen here.

The API binds to `127.0.0.1` only. It has no authentication, and one endpoint
shells out to `sudo`, so it must never be reachable from the network — Home
Assistant runs with host networking on the same machine, so loopback is
sufficient. An earlier version bound to all interfaces; see
[troubleshooting.md](troubleshooting.md).

It is served by a production WSGI server rather than the framework's
development server. The development server handles one request at a time and
has no timeouts; if it wedges while a pump is running, Home Assistant cannot
stop that pump and every downstream protection is issuing commands into a
socket nobody is reading.

## The file-bridge pattern

Home Assistant's `shell_command` executes **inside** the container, so it cannot
change the display backlight, lock the touchscreen, or shut the machine down.

Rather than granting the container more privilege, small host daemons poll files
inside the config directory, which is bind-mounted into the container. Home
Assistant writes a file; the host acts on it.

| File | Written by | Read by | Effect |
|---|---|---|---|
| `bright_normal.txt` | Home Assistant | dimmer daemon | backlight level when awake |
| `brightness.txt` | dimmer daemon | bridge script | value applied to the panel |
| `idle_dim.txt` | Home Assistant | dimmer daemon | idle dimming on/off |
| `wake.txt` | Home Assistant | dimmer daemon | force wake for an alert |
| `screenlock_cmd.txt` | Home Assistant | lock daemon | lock / unlock |
| `screenlock_state.txt` | lock daemon | Home Assistant | current lock state |
| `shutdown_request.txt` | Home Assistant | shutdown daemon | clean power down |
| `watchdog_report.json` | watchdog | Home Assistant | what it repaired or failed to |

Exactly one writer owns each file. Where two components could both drive the
backlight, ownership alternates explicitly: while the screen is locked the lock
daemon owns the panel and the dimmer stands down entirely, rather than the two
negotiating.

The shutdown daemon deletes any pending request on startup. Without that, a
power cut between the request being written and the shutdown completing would
leave the file in place, and the machine would power itself off on every
subsequent boot for a reason nobody would guess.

## Telemetry

Three energy meters report voltage, current, power and cumulative energy over
Modbus RTU. Each is a **point-to-point TTL UART link** — three separate serial
devices, not a multi-drop RS-485 bus.

The meters take their voltage feed from *before* the relays, so they remain
powered and keep answering whether or not a pump is running. The current
transformers clip onto the conductor *after* the relays, so current is measured
only while that pump runs.

The serial devices are exposed to the container by bind-mounting `/dev`
wholesale rather than individual `by-id` symlinks. Those symlinks are recreated
by udev when the USB adapter re-enumerates, which silently leaves the container
holding a stale mount over an empty directory.

## Storage

Home Assistant's recorder writes to SQLite on the same flash card as the OS,
with batched commits and a short retention window. Long-term statistics are kept
separately by utility meters, which are unaffected by the purge.

## Remote access

A private mesh VPN. No inbound ports are published, and no reverse proxy or
public certificate is involved. An earlier design used a public reverse proxy
with a dynamic-DNS name; it existed only to support a voice-assistant
integration that has since been removed.
