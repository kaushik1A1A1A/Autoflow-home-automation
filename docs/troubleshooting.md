# Troubleshooting log

Faults found, their root causes, and the fixes. The ones worth your time are in
the second section: they were found *after* the system had been running
unattended, and all three share a shape.

---

## Found during construction

### Current reading of 82,706 A

**Symptom.** Voltage and frequency read correctly; current and power were
absurd.

**Cause.** The 32-bit Modbus values span two 16-bit registers, and the meter
transmits them least-significant word first. Home Assistant decodes
most-significant first by default, so every 32-bit quantity was being assembled
backwards.

**Fix.** `swap: word` on every 32-bit sensor. Adding it also surfaced a second
error: `count: 2` alongside `data_type: uint32` is rejected, because a 32-bit
type already implies two registers. Removing the redundant declaration resolved
it.

**Worth noting:** 16-bit values (voltage, frequency) were unaffected, which is
why the fault looked like it was specific to certain sensors rather than to a
decoding rule.

### Touch input rotated independently of the display

**Symptom.** After rotating the panel to the mounted orientation, a touch at the
top-left registered at the bottom-left.

**Cause.** Two separate coordinate systems. Rotating the *image* in the
compositor does nothing to the *digitiser*, which continues reporting in its
native orientation.

**Fix.** A udev rule matching the digitiser's USB vendor and product ID and
applying a `LIBINPUT_CALIBRATION_MATRIX` that swaps the axes and inverts one.

**The time sink:** the first attempts used `xrandr` and firmware display
settings. Both are X11 mechanisms and have no effect under a Wayland
compositor — they fail silently rather than erroring, which is what made it look
like the rotation "hadn't taken".

### All serial devices vanish inside the container

**Symptom.** Every meter went unavailable simultaneously. The devices were
present and healthy on the host.

**Cause.** The container was given individual `/dev/serial/by-id/...` symlinks.
Those symlinks are destroyed and recreated by udev when the USB adapter
re-enumerates. The container keeps the old inode: a stale bind mount over an
empty directory. Nothing errors; the paths simply cease to exist inside the
container while looking fine outside it.

**Fix.** Bind `/dev` wholesale into the container and reference the symlinks
through that, so the container follows the host's current state rather than a
snapshot taken at start time.

### WebRTC works on phones and laptops but not on the panel

**Symptom.** Camera streams played everywhere except the device they were built
for, with a codec negotiation error.

**Cause.** The browser on this platform cannot negotiate H.265 over WebRTC.
Phones and laptops can, which is what made it look like a configuration problem
with one client rather than a platform limitation.

**Fix.** The cameras were reconfigured to encode H.264. Software transcoding was
rejected — it would have consumed most of the available CPU to work around a
setting that could simply be changed at the source.

### Eight simultaneous streams overwhelmed the device

**Symptom.** Continuous stuttering, scroll lag and sustained high CPU on the
camera view.

**Cause.** Eight concurrently negotiated WebRTC sessions at full resolution.

**Fix.** A tap-to-live design: still frames on the grid, a live session
established only for the camera in view, and torn down by an `IntersectionObserver`
the moment it leaves the viewport.

---

## Found after the system was live and running unattended

These three are the useful ones. In each case the system reported success while
doing nothing, which is a harder failure to find than a crash.

### Every audible alert had been silent for months

**Symptom.** Alerts produced an on-screen popup and no sound. Assumed for a long
time to be an audio or container-networking problem, and a great deal of effort
had gone into that assumption.

**Cause.** Not audio at all. The media player entity had been **renamed by an
integration migration** — the integration moved to config-entry setup, which
names entities after the integration title rather than its domain. Every call in
the scripts still addressed the old name.

Home Assistant logged this as a **warning**, not an error:

```
Referenced entities media_player.<old_name> are missing or not currently available
```

...returned success to the caller, and discarded the request. The service call
showed a green tick.

**Fix.** Two lines.

**How it was found — the method matters more than the answer.** The chain was
tested from the bottom up, one link at a time: fetch the audio file over HTTP
(served correctly), list the sound devices (hardware present), check the player
daemon (running), check its configured output (correct), then play the exact URL
by hand from the command line — **and it played.** That single test eliminated
every layer below Home Assistant at once, leaving only the last link.

### The container could not resolve any hostname

**Symptom.** Outbound email, push notifications and two integrations had all
been failing for weeks. The machine itself had working internet throughout.

**Cause.** The container's `/etc/resolv.conf` contained comments and **no
nameserver lines at all**. The VPN client rewrites the *host's* resolver
configuration to point at its own resolver; the container runtime generates the
container's copy from the host file and propagated nothing usable.

So this broke at the moment the VPN was installed — for an unrelated reason,
weeks earlier — and nothing connected the two events.

**Fix.** Pin the resolvers explicitly in the compose file, including a public
fallback so name resolution does not depend on the VPN being up.

**How it was found.** A log-summary sensor, written about twenty minutes earlier
for unrelated reasons, grouped the last 24 hours of errors into plain language on
its first run. The failure had been recorded in the log file continuously the
whole time.

### The backup script reported success while uploading nothing

**Symptom.** None. That is the point.

**Cause.** A misconfigured cloud bucket meant the upload never happened. The
script suppressed the error, deleted the local copy anyway, and printed
`Backup complete!`.

**Fix.** Rewritten: fail on error rather than continuing, do not silence stderr,
verify the archive is readable *before* pruning the previous generation, and
write a status file that the supervisor checks. A backup that has not been
verified is not a backup.

### Three GPIO pins destroyed

Covered in full in [decision-log.md](decision-log.md#v5--electrical-root-cause-and-reliability).
Briefly: the relay board's logic supply was fed at 5 V while the GPIO drive at
3.3 V, putting a continuous small reverse current into every pin held high. The
three channels being switched regularly died over months; the five mostly idle
ones survived. Diagnosed by driving the pins with the board **entirely
disconnected**, which eliminated board, wiring and software in a single test.

### A mixer setting that silently halved the output

The audio output level was found at 62% — roughly −24 dB — after having been set
to full. The store-and-restore mechanism had not persisted it. Nothing failed;
everything was simply quieter than intended, indefinitely.

Now enforced by the watchdog rather than set and hoped for: if it reads low it is
reset, and the event is reported.

The same check later found a microphone monitor path at **+16.8 dB**, feeding
ambient room audio back out of the speaker — which had been mistaken for the
speaker picking up sound acoustically.

---

## The pattern

Every fault in the second section is the same shape: **something referenced by
name or number was renamed, emptied or reset underneath a component that kept
reporting success.**

- serial device numbering changed after a re-plug
- the audio card index moved after a reboot
- device symlinks vanished inside the container
- an entity was renamed by an integration migration
- the resolver configuration was emptied by an unrelated installation
- a mixer level reset itself
- a relay reported ON while its driving pin was dead

None of these produced an error. Most of the engineering effort since has gone
into making failures loud rather than preventing them: state read back from
hardware instead of assumed, telemetry health as an explicit sensor, start and
stop confirmation, verified backups, a supervising watchdog, and a log summary
that says what went wrong in a sentence rather than leaving it in a file nobody
opens.

The question worth asking of any new component is not "does this work?" but
**"how would I know if it stopped?"**
