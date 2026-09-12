# Autoflow

A domestic water pump control and monitoring system, built on a single-board
computer running Home Assistant in a container.

It switches three pumps, meters each one electrically, protects them against
running dry and against running too long, enforces interlocks between them,
drives a wall-mounted touch panel, and raises spoken, on-screen and push alerts
when something goes wrong. A separate recorder provides camera views and motion
events.

This repository is the configuration, the host services and the engineering
notes. It is not a product and not a general-purpose framework — it is one
installation, documented in enough detail to be useful to someone building
something similar.

---

## Why it might be worth reading

The interesting part is not that it switches pumps. It is
[docs/troubleshooting.md](docs/troubleshooting.md) — the faults found after the
system had been running unattended for months, and what they had in common.

Three examples:

- Every audible alert had been silent for months. The media player entity had
  been renamed by an integration migration; Home Assistant logged a *warning*,
  returned success to the caller, and discarded every request.
- The container could not resolve a single hostname for weeks, while the machine
  around it browsed the internet normally. Installing a VPN had rewritten the
  host resolver configuration, and the container inherited an empty one.
- The backup script printed `Backup complete!` while uploading nothing and
  deleting the local copy.

None produced an error. Most of the design decisions in this repository exist to
make failures loud rather than to prevent them.

---

## Architecture at a glance

| Layer | What runs there |
|---|---|
| Container | Home Assistant — automations, template sensors, utility meters, dashboards |
| Host services | Relay REST API (loopback only), display dimmer, screen lock, shutdown bridge, alarm listener, watchdog, log summariser |
| Hardware | 8-channel opto-isolated relay board, 3 × energy meters over Modbus RTU, 6 float switch inputs, 10.1" touch panel, USB audio |
| Interfaces | Touch kiosk, phone app, email reports, private mesh VPN for remote access |

Home Assistant runs in a container and therefore cannot reach host GPIO, the
display backlight, or the power state. Rather than granting it broad privilege,
small single-purpose host services own those, and communication happens through
files in the shared config directory or over loopback HTTP. See
[docs/architecture.md](docs/architecture.md).

## Hardware

- Single-board computer (Raspberry Pi 5 class), running the system in Docker
- 10.1" capacitive DSI touch panel, kiosk mode under a Wayland compositor
- 8-channel relay board, active-LOW, opto-isolated, with its coil supply
  separated from the logic supply — see
  [docs/pinout.md](docs/pinout.md#relay-board-supply--the-important-part)
- 3 × PZEM-004T v3 energy meters, each on its own TTL UART link
- 4-channel USB-to-TTL adapter
- 6 float switches via opto-isolator modules
- USB audio output for spoken alerts and tones
- Network video recorder on an isolated interface

## Repository layout

```
docs/          architecture, safety design, wiring, troubleshooting, decision log
services/      long-running host processes
scripts/       one-shot and boot-time helpers, alert audio generators
homeassistant/ Home Assistant YAML, with all deployment-specific values removed
examples/      templates for the files that are never committed
```

## Running it

This is a working installation's configuration, not a distributable package. To
adapt it you will need to:

1. Copy `examples/secrets.yaml.example` to `secrets.yaml` in your Home Assistant
   config directory and fill in real values.
2. Copy `examples/.env.example` to `.env` beside your `docker-compose.yml`.
3. Work through `examples/config.example.yaml` — every value there is
   installation-specific. Nothing in this repository contains a real address,
   credential or identifier.
4. **Measure your own dry-run current thresholds.** The placeholders will not
   protect anything. The method is in
   [docs/safety-design.md](docs/safety-design.md#4-dry-run-detection-and-the-transition-that-defeats-it).
5. Set the boot-time GPIO safe state before connecting a relay board — see
   [docs/pinout.md](docs/pinout.md#boot-time-safe-state).

Entity IDs referring to notification targets are placeholders
(`notify.email_report_target`, `notify.mobile_app_your_phone`) and must be
renamed to match your own.

## Known limitations

Stated deliberately, because they affect how much weight the safety logic can
carry:

- **Dry-run thresholds are estimated, not measured against a dry pump.** They are
  set at roughly 70% of each pump's measured wet running current. That is a
  proxy: a centrifugal pump running dry typically draws well below that, so the
  threshold should trip — but "should" is doing work there. Measuring an actual
  dry event, briefly and under supervision, would replace the estimate with a
  number.
- **Single channel, not a certified safety system.** No safety PLC, no redundant
  sensing path, no SIL or PL rating, no proof-test interval. The protections
  reduce likelihood and duration of a fault; they are not a guarantee. Stated at
  greater length in [docs/safety-design.md](docs/safety-design.md#scope-and-limitations--read-this-first).
- **The database lives on the same flash card as the operating system.** Mitigated
  with batched commits, a short retention window and exclusions for
  high-churn cosmetic sensors — but mitigated, not eliminated. Flash wear remains
  the long-term failure mode, and the card is cloned rather than trusted.

## Licence

MIT — see [LICENSE](LICENSE).
