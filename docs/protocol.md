# Link protocol: bias-T gap-width OOK, clock-beacon replies

The only wire between the shack and the box is the coax. The RTL-SDR bias-T
powers the box, and the host sends commands by switching that bias off for
short gaps. A short gap is a 0 and a long gap is a 1. The box rides through
each gap on its hold-up capacitor.

The box answers on the same coax: its MCU keys a clock onto the RF line,
and the dongle receives it like any other signal. See
[Back-channel](#back-channel-box--host).

## Physical layer

| Symbol | Bias | Nominal | Accepted by firmware |
|---|---|---|---|
| idle before frame | on | ≥ 400 ms | ≥ 300 ms (`T_IDLE_MIN`) |
| bit 0 | off | 30 ms | 5 … 130 ms, below the adaptive threshold |
| bit 1 | off | 70 ms | 5 … 130 ms, above the adaptive threshold |
| between bits | on | 100 ms | 30 … 300 ms (`T_ON_MIN` / `T_ON_MAX`) |
| crackle (glitch) | off | — | < 5 ms: ignored, but lights **GLT** |
| bad gap | off | — | 130 … 300 ms: frame error **GAP** |
| dropout | off | — | ≥ 300 ms: bias lost, the box rides through on the supercap |

The firmware does not hard-code the 0/1 threshold. It takes it from the
sync bits `1 0 1`:

```
thr = (gap[0] + gap[2] + 2 * gap[1]) / 4      (halfway between long and short)
```

Sync is accepted only if both long gaps are at least 1.5× the short one.
This cancels:

* MCU clock error. LFOSC0 is ±6 %, and the decoder is tested at ±10 %.
* Slow bias discharge. The box only sees a falling edge once the coax and
  the RTL side have discharged through the bleeder, so every measured gap is
  shorter than the commanded one. It is tested up to 10 ms.
* Host jitter. It is tested at ±3 ms per edge.

The first 3 bits are classified against a nominal 50 ms threshold so the
lamps can show them as they arrive. They are re-labelled `101` once sync
passes.

## Frame (15 bits, MSB first)

```
 k:   0 1 2 | 3  4  5  6   7     8   | 9 .. 14
      1 0 1 | B2 B1 B0 LNA QUIET CLR | inverse of k=3..8
      sync  | payload P (6 bit)      | check C = ~P
```

| Field | Meaning |
|---|---|
| `B2..B0` | filter branch 0–7 (`bandplan.json`) |
| `LNA` | request the LNA. Ignored, and the **AUTO** lamp lights, on branches with `lna_ok = false` |
| `QUIET` | 1 = no animations, no RUN lamp and no heartbeat. This gives the fewest MCU wake-ups and the least EMI |
| `CLR` | clear the sticky lamps (DROP, GLT, RST) and re-arm a tripped LNA |

Because the check is the inverse of the payload, every valid frame has
exactly 6 ones in P+C. The FRAME lamp row therefore never lights more than
8 lamps.

A frame takes about 2.3–2.9 s. After a frame (good or bad) the decoder
needs ≥ 300 ms of steady bias before it accepts the next one. Bits that
follow an error are therefore ignored instead of producing a second error.

## Errors (one lamp each)

| Lamp | Cause |
|---|---|
| SYNC | first three gaps are not long-short-long (≥ 1.5×) |
| CHK | check bits are not the inverse of the payload |
| GAP | a gap of 130–300 ms |
| RUNT | bias came back for < 30 ms (a spike inside a gap) |
| TMO | bias on > 300 ms in the middle of a frame (host stalled) |
| DROP (sticky) | bias lost ≥ 300 ms inside or outside a frame |

## Host side

`host/coaxolotl.py` has three backends:

| Backend | Use |
|---|---|
| `pyrtlsdr` (default) | Opens the dongle and toggles `set_bias_tee()`. The dongle must not be held by another program. |
| `rtl_biast` | Runs the rtl-sdr-blog `rtl_biast -b 0/1` once per edge. Its constant start-up latency cancels out; only the jitter matters, and the tool reports it. |
| `dry-run` | Prints `t_ms level` lines. Pipe them into `firmware/build/sim`. |

Add `--listen` (pyrtlsdr only) to read the reply after the frame.
`listen` records without sending, and `decode FILE` reads an `rtl_sdr`
capture (`.cu8`) or a `.cf32` file. The SDR++ module in `sdrpp/` does all
of this from the GUI.

In your own streaming code, keep your `RtlSdr` handle and key through it:

```python
from coaxolotl import Frame, play
play(lambda on: sdr.set_bias_tee(on), Frame(bank=6, lna=True).edges())
```

The player runs on an absolute `perf_counter` schedule (sleep plus a 2 ms
spin), so lateness does not add up over the frame. If the reported jitter is
above 6 ms, slow the link down with `--timing 40,90,120,400`. The firmware
adapts to it without changes.

## Back-channel (box → host)

After every frame, good or bad, the box sends a reply. It also sends one
HELLO when it has finished booting.

### When

The reply starts when the decoder is ready for the next frame: after
`T_IDLE_MIN` = 300 ms of steady bias following the host's last gap. For a
good frame that is 300 ms after its last rising edge. For a rejected frame
the decoder ignores the rest of it, so the NAK also comes 300 ms after the
host's last gap. A frame that starts meanwhile drops the pending reply, and
a bias edge during a reply cuts it short. The host always has priority.

The host therefore keys its frame, retunes the dongle and listens for about
1.4 s. The next frame needs ≥ 400 ms of idle anyway, so a reply never
collides with a well-behaved host.

### Physical layer

| | |
|---|---|
| Source | PCA0 CEX0 on P0.4 in frequency-output mode, SYSCLK = HFOSC0 24.5 MHz, CPH = 1 → 12.25 MHz square wave |
| Received at | 3rd harmonic, **36.75 MHz** nominal. The 5th, 61.25 MHz, is about 4 dB weaker. HFOSC0 is ±2 %, so ±0.75 MHz |
| Coupling | 3.3 Vpp → 4.7 kΩ / 51 Ω pad → 1 kΩ + 100 pF → coax side of the output switch, after the filter bank. It works on every branch and in bypass. About −65 dBm at 36.75 MHz (**VERIFY** on the bench) |
| Modulation | on-off keying: PCA counter running = carrier, stopped = none |
| Chip | 5 ms (`BCN_CHIP_MS`), timed by Timer 4 / LFOSC (±10 %, the host measures it) |
| Coding | Manchester: bit 1 = on, off; bit 0 = off, on. 100 bit/s |
| Duration | 145 chips = 0.725 s |

The clock only runs during a reply. Between replies the MCU Snoozes as
before, so the beacon adds no spurs while you listen.

### Packet

```
 preamble   sync        7 bytes, MSB first                      close
 1×8        0x2D        b0 b1 b2 b3 b4 b5 b6                    1 chip on
```

| Byte | Bits | Meaning |
|---|---|---|
| 0 | `kind[7:6] err[5:3] seq[2:0]` | kind 1 HELLO, 2 ACK, 3 NAK. err = SYNC/CHK/GAP/RUNT/TMO/DROP (1–6) on a NAK. seq = good frames applied, mod 8 |
| 1 | `bank[7:5] lna_req lna_path fmn auto quiet` | what the switches actually do now |
| 2 | `trip low hold drop glt rst lna_en 0` | the health lamps |
| 3 | VCOAX / 20 mV | measured just before the reply (0–5.10 V) |
| 4 | I_LNA / mA | raw INA180 reading, also when the LNA is off (shows a stuck switch) |
| 5 | VSCAP / 20 mV | supercap |
| 6 | CRC-8 | poly 0x07, init 0xFF, not reflected, over bytes 0–5. Check value for `"123456789"`: 0xFB |

A query is just the current state sent again with CLR = 0. That is
idempotent and always answered, so the host never needs a separate
"status" command.

### Receiver

The receiver does not assume the carrier frequency or the chip rate:

1. Spectrogram of the capture with frames of ≤ 0.6 ms (1024 points at
   2.4 MS/s).
2. The beacon bin is the one with the largest 98th/20th-percentile power
   spread, summed over 3 bins. A steady carrier and plain noise have a
   small spread. A spread below 15 dB means "no beacon".
3. Threshold that bin's envelope at the geometric mean of the two levels,
   then take a majority vote over 0.3 chip.
4. The chip length is the median of the single-chip runs. Each run then
   becomes 1 or 2 chips, and anything longer ends a segment.
5. Search the chips for the preamble tail plus sync, Manchester-decode
   7 bytes, and check the CRC.

`host/backchannel.py` and `sdrpp/coaxolotl_source/src/coaxlink.cpp` both
implement this. The tests check them against each other, against the
firmware's packets and against synthetic captures (carrier ±0.75 MHz,
chips ±10 %, a steady interferer, per-bin SNR down to about 17 dB).

## Not in v0.1

* There is no addressing, so there is one box per coax.
* The host does not repeat frames by default. The 6-bit inverse check plus
  the sync check reject corrupted frames instead of mis-applying them. Use
  `--repeat 2` on a noisy link.
