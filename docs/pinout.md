# GPIO and wiring reference

Header pin numbering follows the standard 40-pin layout. Confirm the physical
orientation against `pinout` (ships with `python3-gpiozero`) on the actual board
before moving a connection — which row carries the odd numbers is not something
to take from a document.

---

## Relay channels

The board is **active-LOW**: driving a pin to 0 V energises the relay.

| Relay | GPIO | Header pin | Function |
|---|---|---|---|
| 1 | 17 | 11 | Pump 1 |
| 2 | 27 | 13 | Pump 2 |
| 3 | 22 | 15 | Pump 3 |
| 4 | 23 | 16 | Enclosure fan |
| 5 | 4 | 7 | spare |
| 6 | 25 | 22 | spare |
| 7 | 18 | 12 | spare |
| 8 | 21 | 40 | spare |
| — | — | 39 | Ground (relay logic) |

Relays 5, 7 and 8 were moved from GPIO 24, 5 and 6 after those pins were
destroyed — see [troubleshooting.md](troubleshooting.md).

GPIO 7 and 8 would have been better choices for an active-LOW board, since they
idle high and would be safe before software runs. They were rejected because SPI
is enabled on this installation and claims both as chip selects. The boot-time
safe state is handled in firmware instead, which covers all eight channels
rather than two.

## Float switch inputs

Opto-isolated, pulled to ground when asserted.

| GPIO | Header pin | Function |
|---|---|---|
| 12 | 32 | Upper tank — full |
| 13 | 33 | Upper tank — empty |
| 16 | 36 | Lower tank — full |
| 20 | 38 | Lower tank — empty |
| 19 | 35 | Sump 1 — full *(wired, not yet used)* |
| 26 | 37 | Sump 2 — full *(wired, not yet used)* |
| — | 6 | Ground (float opto-isolators) |

## Pins that must not be reused

| GPIO | Header pin | Reason |
|---|---|---|
| 24 | 18 | **Damaged.** Driven low, reads high, with nothing connected. |
| 5 | 29 | **Damaged.** Same. |
| 6 | 31 | **Damaged.** Same. |

Verified with the relay board entirely disconnected, against a known-good pin on
the same header as a control.

---

## Relay board supply — the important part

This is the wiring detail that destroyed three GPIO pins, and the one worth
copying if you build something similar.

These boards have a jumper bridging the logic supply and the coil supply, and
they are commonly wired with the logic supply taken from the controller's 5 V
rail. Both defaults are wrong when driving from 3.3 V logic:

- With the logic supply at 5 V and a GPIO driven **high** to 3.3 V, roughly 1.7 V
  remains across the optocoupler LED and its resistor. Current flows **backwards
  into the GPIO pin** — around half a milliamp, continuously, for as long as that
  relay is off. That sits at the injection limit for the pin. It is never enough
  to fail today and exactly enough to destroy a pin over months.
- With the jumper fitted, the coil supply and logic supply share a rail and a
  ground, so the optocouplers are bypassed for their main purpose. Coil switching
  transients reach the controller.

The channels being switched regularly failed. The idle ones survived, which is
what made the fault look like a software problem with specific relays.

**Correct wiring:**

| Board terminal | Connect to |
|---|---|
| `Vcc` (logic side) | Controller **3.3 V** |
| `GND` (input header) | Controller ground |
| `IN1`–`IN8` | GPIO |
| `JDVcc` | **Separate** 5 V supply, positive |
| `Gnd` (3-pin header) | Separate supply, negative |
| jumper | **removed** |

Keep the two grounds separate. That is what makes the isolation real rather than
decorative.

**One consequence to test for:** at 3.3 V the optocoupler LEDs receive roughly
2 mA instead of 3.8 mA. That is ample on most boards of this type and marginal on
a few. Test every channel after rewiring rather than assuming.

## Boot-time safe state

```
gpio=17,27,22,23,4,25,18,21=op,dh
```

Placed in the bootloader configuration, this sets every relay pin to output-high
before the kernel starts. On an active-LOW board, high means off.

Without it there is a window between power-on and the control service starting
during which pins sit at their default state — a pull-down on most of this
range, which reads as *energised*.

## Meter wiring

- Voltage terminals connect **before** the relay, so the meters stay powered and
  keep answering regardless of pump state.
- Current transformers clip onto the conductor **after** the relay, so current is
  measured only while that pump runs.
- Each meter is a point-to-point TTL UART link to its own serial port. This is
  **not** an RS-485 multi-drop bus, and the three links are electrically
  independent.
- The USB-to-TTL adapter's logic level must match the meters'. Check the jumper.
- Each meter's Modbus slave address is stored in the meter itself and must match
  the port it is physically wired to. Re-plugging adapters renumbers the serial
  devices; if the addresses and ports fall out of step, readings appear on the
  wrong pump before they disappear entirely.

> Mains voltages are present on the relay contacts and on the meter voltage
> terminals. Conductor sizing, protective devices, isolation and earthing are
> installation-specific and are not covered by this repository.
