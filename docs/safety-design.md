# Safety design

## Scope and limitations — read this first

This is **not a certified functional safety system.** Stating that plainly
matters more than anything else in this document.

- No safety PLC. The logic runs on a general-purpose single-board computer under
  a general-purpose operating system.
- **Single channel.** There is no redundant sensing path, no diverse
  implementation, and no cross-checking between independent channels.
- No SIL or PL rating, no hazard analysis to any standard, no proof-test
  interval, and no defined safe failure fraction.
- The protections here reduce the likelihood and duration of a fault. They are
  not a guarantee, and nothing in this repository should be relied on where
  failure would endanger a person.

What it *is*: safety logic built on conventional industrial principles — fail-safe
default states, watchdogs with bounded authority, feedback rather than assumption,
and interlocks enforced in more than one place — applied to domestic equipment
where the realistic worst cases are a burnt-out pump motor, a flooded floor, or a
dry-run seizure.

---

## 1. Fail-safe output state before the operating system starts

The relay board is **active-LOW**: a pin held at 0 V energises the relay.

Most GPIO on this platform come up as inputs with a pull-**down**, which reads as
0 V — meaning that between power-on and the control service starting, a pump
relay could be commanded on with nothing supervising it.

This is solved in firmware, not in software: a bootloader directive sets every
relay pin to output-high before the kernel loads. The safe state is established
by the hardware initialisation path, before any code that could fail to run.

A handful of pins on this platform default to pull-**up** and would have been
safe by accident. Relying on that would have been luck, not design, and it does
not cover all eight channels.

## 2. Maximum runtime by polling, not by edge trigger

A pump that runs past its limit is stopped. The limit is evaluated by a periodic
poll that compares each pump's elapsed running time against the current setting.

The earlier implementation used a state trigger with a templated duration. Trigger
templates are rendered **once, when the automation loads** — so changing the limit
in the interface had no effect until a restart. The control looked live and was
inert.

Polling also covers a case the trigger version missed entirely: a pump already
running when automations reload. An edge-triggered rule has no edge to catch.

Each pump has its own limit and is stopped individually. An earlier version shut
down all three, so a pump that had started thirty seconds ago was killed because
a different pump had reached its limit.

## 3. Feedback rather than assumption

Relay switches report the state read back from the hardware, polled on an
interval — not the state last commanded.

Without this, a relay whose driving pin has failed still shows ON in the
interface, every safety rule that conditions on that switch reasons from a false
premise, and nothing reports a fault. This was not hypothetical: three GPIO pins
failed and the system continued to display them as working.

The read-back template is deliberately conservative. If a poll fails, the sensor
holds its previous value rather than defaulting to OFF — an unreadable state must
not be reported as a definite one.

## 4. Dry-run detection, and the transition that defeats it

A pump with no water draws measurably less current. If current falls below a
per-pump threshold for a set period while that pump is running, it is stopped.

Two guards exist because the naive version produces false trips:

**Mains guard.** The meter is powered from the line it measures. A mains failure
makes current read low or unavailable — indistinguishable from a dry pump by
current alone. The shutdown only proceeds if measured voltage is above a
threshold.

**Stale-sensor guard.** When power returns, the current sensor goes from
`unavailable` straight to `0 A`. Home Assistant treats that as crossing the
threshold downward, so the naive rule fired a critical dry-run alert every time
the power came back. The rule now requires the *previous* reading to have been a
real number, so a transition out of `unavailable` cannot trigger it.

The second is the more interesting failure: the logic was correct, the sensor was
correct, and the fault lived entirely in the transition between them.

## 5. Mains detection from the highest of three meters

Mains presence is derived from the **maximum** of the three meter voltages, not
from any single one.

If one meter is unplugged or its serial channel drops, that meter alone reads
zero. Taking the maximum means a single dead sensor cannot masquerade as a power
cut — all three must go quiet, which is what an actual supply failure looks like.

Asymmetric delays stop it flickering: the supply must appear dead for a sustained
period before the state changes, but recovery is recognised quickly.

## 6. Two-layer pump interlock

Two rules are enforced: never more than two pumps at once, and two specific pumps
may never run together.

Implemented deliberately in two places:

- **As a preventer** — guard conditions on the automatic start paths, so
  automatic operation declines to start and waits.
- **As a backstop** — a rule watching all three switches that stops the *newly
  started* pump, never one already running, whatever caused it to start.

The preventer alone would miss manual switching from the interface. The backstop
alone would start a motor and stop it a second later, which is hard on both the
motor and the switching device. Both are needed, and they fail in different
directions.

## 7. Refusal to start without telemetry

A pump will not start if its meter is not reporting. Without current readings,
dry-run protection is blind — the pump would be running with its principal
protection silently absent.

Telemetry health is judged on two conditions: the sensor must be available, and
its last update must be recent. An entity that stopped updating half an hour ago
still has a plausible-looking value; availability alone does not catch that.

## 8. Mid-run telemetry loss

Losing telemetry *during* a run is a different decision from refusing to start.
Stopping a pump mid-fill has its own costs, and telemetry loss is often
transient.

The chosen policy: **keep running, alert loudly, and cap the run at ten minutes.**
Continuing indefinitely without protection is not acceptable; stopping instantly
on a momentary read failure is not either. The cap bounds the exposure.

This was an explicit trade-off, not a default.

## 9. Watchdog with bounded authority

A host-side watchdog checks the system every few minutes, repairs what it safely
can, and reports what it did by email.

Its limits are the design:

- **It never touches pumps or relays.** Nothing that switches mains is in its
  scope. It restarts stalled services, corrects drifted audio settings, and
  checks that backups are running.
- **Rate-limited** to a small number of repairs per hour. Past that it stops
  trying and reports failure instead — a repair loop that never escalates is
  indistinguishable from a working system.
- **It reports only when it acts or fails.** A watchdog that emails hourly to say
  everything is fine trains you to ignore it.

It runs on the host rather than inside Home Assistant, because the one fault Home
Assistant cannot report is its own death.

## 10. Tank-full stop with a spoken grace period

When the full-level float asserts, the pump is stopped after a short grace period
with a spoken warning, rather than cut instantly. The delay filters float chatter
from surface movement, and the announcement means anyone nearby knows why the
pump stopped.

---

## What happens when things fail

| Failure | Behaviour |
|---|---|
| Mains power lost | Pumps stop with the supply. Mains detection suppresses false dry-run alerts. State is re-read on restart rather than assumed. |
| Network lost | Pumps, interlocks, runtime limits and dry-run protection all continue — they are local. Email and push notifications fail. |
| A meter fails | That pump refuses to start. The other two are unaffected. Mains detection survives because it takes the maximum of three. |
| Control service crashes | systemd restarts it. GPIO return to the firmware-set safe state on reboot. |
| Home Assistant restarts mid-run | Switch state is read back from hardware, not assumed. Runtime limits are polled, so a pump already running is still covered. |
| Display or touchscreen fails | Headless operation is unaffected; the panel is an interface, not a dependency. |
| Editing workstation offline | No effect. It is not in the runtime path. |

## Manual override

Physical wall switches remain in circuit and are not disabled. External starts —
a pump energised outside software control — are detected and reported. There is
no documented software bypass procedure, and adding one would weaken the
interlocks rather than strengthen them.
