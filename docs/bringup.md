# Bring-up checklist

Work through these in order. Each step proves one assumption, so a failure
points to one thing.

## 0. Power only (MCU not programmed)

* Feed the coax from the V3 with the bias-T on, and put a current probe in
  the line.
  * The peak should stay below 160 mA.
  * VCOAX should read about 4.2–4.4 V, and 3V3 should read 3.30 V.
  * PWR lights.
* The supercap reaches 3.6 V in about 50 s.

## 1. Falling-edge time (sets the bleeder)

1. Scope P0.0 (SENSE).
2. Toggle the bias: `rtl_biast -b 0`, then `rtl_biast -b 1`.
3. Measure the time from the USB command to SENSE < 1 V. It must be under
   about 8 ms.
4. If it is slower, lower `R_BLEED` (680 → 470 → 330 Ω). See the table in
   `hardware.md`.
5. The rising edge is fast because the bias-T drives it.

## 2. First light: classic-register build

```
make -C firmware clean && make -C firmware SNOOZE=0
commander flash firmware/build/coaxolotl.hex --device EFM8BB21F16G
```

This build uses only Timer2, Idle mode and port match. It has no Snooze and
no Timer 4.

* Expected: all 48 lamps for 0.7 s → chaser → THRU and BYP light, the LNA
  powers on, and I-OK lights. HOLD blinks until the supercap is charged.
* No lamps at all: check the 595 chain order, OE_N and the LED polarity. The
  outputs **sink**.

## 3. Link

```
python3 host/coaxolotl.py send --bank 6 --lna --backend rtl_biast
```

* You should see the FRAME row fill bit by bit, and the 0/1 lamps flicker.
  Then OK lights, the bank lamp walks to 2m/AIR, and LNA and FMN light.
* Put a logic analyzer on P1.0–P1.3 and P0.7. Check the PE42582 / PE42520
  truth tables against the routing. If the branch order is wrong, edit the
  `port` fields in `bandplan.json` and run `make -C firmware bandplan`.
* For bad timing, the tool prints its own latency and jitter.

## 4. Health monitoring

* Compare the I-OK window against a DMM in series with the LNA. Trim
  `I_UV_PER_MA` in `config.h` if needed.
* Trip test: load the LNA rail with about 22 Ω. TPS2553 limits the current
  and raises FAULT → TRIP lights, and the box drops to BYP. Then send
  `--clr` → the LNA comes back.
* Lower the bias (long cable or a lab supply): below 3.9 V, LOW lights.

## 5. Snooze build (the real one)

```
make -C firmware clean && make -C firmware          # CFG_SNOOZE=1
```

Three register guesses are checked here (see `include/efm8bb2_sfr.h`):

| Symptom | Wrong guess | Fix |
|---|---|---|
| Every frame fails with SYNC or TMO, or none decode | Timer 4 is not ticking at 10 kHz (`TMR4CN0_T4XCLK_LFO`, `LFO0CN`) | Check the T4XCLK code for LFOSC0/8 in the RM |
| RUN never goes dark in idle | `PCON1_SNOOZE` is the wrong bit | Check the PCON1 layout |
| RST lights after about 15 s of idle | Snooze does not wake on the Timer 4 wrap, so the watchdog bites | Enable the Timer 4 interrupt (EIE2) with an empty ISR at the BB2 Timer 4 vector |

When it works:
* In idle, RUN is dark with a 50 ms blip about every 6.5 s (show mode). With
  `--quiet` it stays dark.
* The 3V3 current is about 0.5–0.7 mA, mostly the INA180 and the two
  PE42582.
* Spur check: watch the HF waterfall. The 3.0625 MHz SYSCLK comb
  (3.06 / 6.13 / 9.19 … MHz) must appear only while RUN is lit.

## 6. Ride-through

* Pull the coax for 10 s. The lamps go dark.
* Plug it back. The previous bank and LNA state are still active, and DROP
  lights. A `--clr` frame clears it.
* With the supercap removed, the same test causes a power-on reset, and the
  box comes back in THRU and bypass.
