# Link protocol: bias-T gap-width OOK

The only wire between the shack and the box is the coax. The RTL-SDR bias-T
powers the box, and the host sends commands by switching that bias off for
short gaps. A short gap is a 0 and a long gap is a 1. The box rides through
each gap on its hold-up capacitor.

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

## Config frame (sync 0 1 0)

Settings that are not part of the RF state use the same 15-bit frame with
the sync inverted:

```
 k:   0 1 2 | 3   4   5 6 7 8 | 9 .. 14
      0 1 0 | PM1 PM0 0 0 0 0 | inverse of k=3..8
```

| `PM1 PM0` | Panel mode | Lamps |
|---|---|---|
| 00 | full | all 48 lamps, as in `docs/panel.md` |
| 01 | bands | only the bank row F0–F7 |
| 10 | off | all 48 dark (OE_N high); only the hard-wired PWR lamp |
| 11 | reserved | frame accepted, mode unchanged |

* A config frame never touches the switches, the LNA or the sticky lamps.
* The box stores the mode in flash, so it survives a power cycle. The
  lamp test at power-up follows the stored mode: 8 lamps in bands, none in
  off. The chaser runs only in full.
* The four reserved bits must be 0. They are kept for later settings, for
  example lamp brightness via OE_N.
* Short-long-short is told apart from long-short-long by the same 1.5×
  ratio test, and the 0/1 threshold comes out the same way. Old hosts that
  only send state frames keep working.

Host: `coaxolotl.py panel full|bands|off`, or `ConfigFrame(panel=…)` in
your own code.


Because the check is the inverse of the payload, every valid frame has
exactly 6 ones in P+C. The FRAME lamp row therefore never lights more than
8 lamps.

A frame takes about 2.3–2.9 s. After a frame (good or bad) the decoder
needs ≥ 300 ms of steady bias before it accepts the next one. Bits that
follow an error are therefore ignored instead of producing a second error.

## Errors (one lamp each)

| Lamp | Cause |
|---|---|
| SYNC | first three gaps are neither long-short-long nor short-long-short (≥ 1.5×) |
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

In your own streaming code, keep your `RtlSdr` handle and key through it:

```python
from coaxolotl import Frame, play
play(lambda on: sdr.set_bias_tee(on), Frame(bank=6, lna=True).edges())
```

The player runs on an absolute `perf_counter` schedule (sleep plus a 2 ms
spin), so lateness does not add up over the frame. If the reported jitter is
above 6 ms, slow the link down with `--timing 40,90,120,400`. The firmware
adapts to it without changes.

## Not in v0.1

* There is no back-channel. The box cannot report state to the host. One
  option for later is a modulated LNA current that the host reads back as
  RTL-SDR AGC or bias current.
* There is no addressing, so there is one box per coax.
* The host does not repeat frames by default. The 6-bit inverse check plus
  the sync check reject corrupted frames instead of mis-applying them. Use
  `--repeat 2` on a noisy link.
