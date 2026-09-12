# Decision log

Each phase of the architecture, what changed, and what forced the change.

The distinction matters: most of these were **forced** by a limit or a failure
that made the previous design untenable. Only the initial build was a free
choice. Where a change was made by preference, it says so.

Network addressing, hostnames and remote-access identifiers are deliberately
absent from this document.

---

## V1 — Initial build

| | |
|---|---|
| **What changed** | Home Assistant deployed in Docker with `network_mode: host` and `privileged: true`. Lightweight Wayland compositor booting Chromium in kiosk mode on a 10.1" capacitive DSI panel. Three PZEM-004T v3 energy meters over Modbus RTU. A Flask REST API on the host exposing GPIO to the containerised Home Assistant. Live text-to-speech through MPD to a USB speaker. Eight camera streams via WebRTC. Remote access through a reverse proxy with a dynamic-DNS domain and a public TLS certificate. |
| **What forced it** | Nothing — this was the baseline design, chosen by **preference**. Home Assistant in a container was chosen so the host OS stays reusable and the application is disposable; the REST API exists because a containerised Home Assistant cannot reach host GPIO directly. |

---

## V1a — Display orientation

| | |
|---|---|
| **What changed** | Screen rotation moved into the Wayland compositor's own configuration. Touch input corrected separately with a udev rule applying a `LIBINPUT_CALIBRATION_MATRIX` matched to the digitiser's USB vendor and product ID. |
| **What forced it** | **Forced.** The panel was mounted in portrait; the compositor boots landscape. The conventional fixes — `xrandr` and firmware config entries — are X11 mechanisms and had no effect under Wayland. Worse, rotating the *image* did not rotate the *digitiser*: a touch at the top-left registered at the bottom-left. Two separate coordinate systems, needing two separate fixes. |

---

## V1b — CCTV rendering

| | |
|---|---|
| **What changed** | Eight concurrently rendered WebRTC streams replaced with a tap-to-live design: still images on the grid, with a live stream established only for the camera being viewed, and torn down by an `IntersectionObserver` the moment it leaves the viewport. |
| **What forced it** | **Forced.** Eight simultaneous 4 MP streams produced continuous stuttering, scroll lag and sustained high CPU on a low-power single-board computer. Software transcoding was rejected for the same reason. The camera encoders were also switched from H.265 to H.264, because the browser on this platform cannot negotiate H.265 over WebRTC — it worked on phones and laptops, which is what made the fault look like a configuration error rather than a codec limitation. |

---

## V2 — Safety and correctness pass

| | |
|---|---|
| **What changed** | Per-pump limits replacing three shared helpers. Maximum-runtime rewritten as a polled evaluation instead of a state trigger. Dry-run protection extended from one pump to all three, with a mains guard and a stale-sensor guard. A two-layer pump interlock. Mains-presence detection derived from meter voltages. Recorder retention limits and batched commits. The relay API rebound from all interfaces to loopback. |
| **What forced it** | **Forced, and by several independent faults.** One pump exceeding its limit shut down all three — a pump that had started thirty seconds earlier was killed because another had run for ninety minutes. The runtime limit used a template inside a `for:` clause; trigger templates render once when the automation loads, so the limit slider in the UI did nothing until a restart — it had been decorative since it was built. Two of the three pumps had no dry-run protection at all, despite documentation describing one. And the relay API — documented as "securely isolated, listening strictly on localhost" — was in fact bound to every interface, with no authentication, on a machine that also exposed an endpoint shelling out to `sudo`. |

---

## V3 — Alerts and attack surface

| | |
|---|---|
| **What changed** | Live text-to-speech replaced with pre-rendered audio clips mixed with a chime at build time. Reverse proxy, dynamic-DNS domain and public tunnel all removed; remote access reduced to a private mesh VPN with no inbound ports. Third-party voice assistant integration removed. One pump renamed for clarity. |
| **What forced it** | **Forced.** Every alert had been producing a popup and no sound for months. The cause was not audio at all: the media player entity had been renamed by an integration migration, and every call in the scripts addressed the old name. Home Assistant logged this as a *warning*, returned success to the caller, and discarded the request. Separately, an audit found three independent routes into the system from outside, one of which — a public tunnel — had been enabled and forgotten, exposing the login page to the entire internet with no account required to reach it. The reverse proxy existed solely to support the voice assistant integration, which had already been removed, so the exposure was purchasing nothing. |

---

## V4 — Making failure loud

| | |
|---|---|
| **What changed** | Relay switches given real state feedback (`command_state`) polled from the hardware rather than assumed from the last command. Telemetry-fault binary sensors. Explicit start and stop confirmation. Range clamps and monotonic guards on meter readings. A host-side watchdog with bounded scope, reporting by email. Backup verification. Utility meters and periodic reports. Escalation to critical push notifications for a defined set of conditions. |
| **What forced it** | **Forced.** Relays were reporting ON while the pins driving them were dead. A pump could be started with no telemetry at all, leaving every downstream protection blind. The backup script was printing "Backup complete!" while uploading nothing and deleting the local copy. The common thread — a component reporting success it had not achieved — is what this phase was built to eliminate. |

---

## V5 — Electrical root cause and reliability

| | |
|---|---|
| **What changed** | Relay board logic supply moved from 5 V to 3.3 V; the link bridging logic and coil supplies removed; coil supply given its own source with grounds kept separate. Three relay channels moved to undamaged GPIO. A firmware-level directive setting all relay pins to their safe state before the kernel starts. Storage medium cloned and the clone boot-tested. Container DNS pinned explicitly. Development web server replaced with a production WSGI server. Screen lock reimplemented to actually block input. Distinct non-speech tones for three alert classes. A log-summary sensor translating the application log into plain language. |
| **What forced it** | **Forced.** Three GPIO pins had been destroyed. With the relay board fully disconnected they still failed to follow commands, which eliminated the board, the wiring and the software and localised the fault to the pins themselves. Root cause: the board's logic supply at 5 V put a continuous reverse current into any GPIO held high — small, permanent, and precisely at the injection limit. Separately, the container's resolver configuration had been silently emptied when the VPN client rewrote the host's, breaking every outbound notification for weeks while the machine around it browsed the web normally. That one was found by the log-summary sensor on its first run, in a log file that had been recording the failure the entire time. |
