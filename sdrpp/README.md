# Coaxolotl for SDR++

`coaxolotl_source` is an SDR++ source module: an RTL-SDR source that also
remote-controls the Coaxolotl box over the same coax, and reads its replies.

```
 Source: Coaxolotl RTL-SDR
 [RTL-SDR Blog V3 [00000001]      ] [2.4 MHz] [Refresh]
 PPM [ 0 ]   Gain [=====|----] 29.7 dB   [ ] Tuner AGC
 [x] Bias-T (box power)
 Coaxolotl
 Branch [F6 2m/AIR (BPF 108-174 MHz) ]
 [x] LNA  [ ] Quiet  [x] Read reply   [ ] Follow tuning
 [Send] [Poll] [Clear] [Listen]
 Panel [full ]
 Beacon MHz [ 36.750 ]   [x] Show beacon on waterfall
 Link: reply: ACK
 ACK seq 3 (2 s ago)
 box: F6 2m/AIR LNA +FM notch
 Vcoax 4.28 V   I_lna 101 mA   Vscap 3.92 V
 TRIP LOW DROP GLT RST
 beacon 36.7391 MHz (osc -0.03 %), on/off 38 dB, chip 5.04 ms
```

## Why a source module

Only the code that owns the `rtlsdr_dev_t` can switch the bias-T with
millisecond timing and read samples at the beacon frequency. So the module
is a complete RTL-SDR source (device, sample rate, PPM, gain, bias-T) with
the link built in. The rest of SDR++ sees an ordinary source. Select
**Coaxolotl RTL-SDR** instead of **RTL-SDR** in the Source menu.

## What a transaction does

1. **Send** keys a 15-bit frame as bias-T gaps (about 2.5 s, see
   `docs/protocol.md`). The RF from the box blinks during the gaps.
2. With **Read reply** on, the module then tunes the dongle to the beacon
   (36.75 MHz by default), skips 100 ms of stale USB buffers and records
   1.3 s.
3. It tunes back to your frequency and decodes the reply: ACK or NAK with
   the error, the switch state the box actually set, VCOAX, LNA current,
   supercap voltage and the sticky flags.

**Poll** re-sends the current state (harmless, always answered). **Clear**
sends it with CLR: clears DROP/GLT/RST and re-arms a tripped LNA. **Listen**
records 3 s without sending, e.g. for the HELLO after the first power-up.
**Panel** sends a config frame (full / bands / off lamps, stored in the
box's flash). **Follow tuning** picks the narrowest branch for the frequency you tune to,
and sends it once you have stayed there for 1.5 s.

While it listens, the waterfall shows the beacon band (**Show beacon on
waterfall**) or a black band. The frequency scale is not updated for those
1.4 s.

The beacon comes from the MCU's 24.5 MHz RC oscillator (±2 %), so it can
sit up to ±0.75 MHz away from 36.75 MHz. The decoder searches the whole
captured band for the one bin that keys on and off. Use 2.048 MS/s or
more so that window covers the tolerance. The reported `osc` error is
stable per box; if yours is far off, set **Beacon MHz** to the measured
value.

## Build

The module builds inside an SDR++ source tree, like the stock modules.

```bash
git clone https://github.com/AlexandreRouma/SDRPlusPlus
cd SDRPlusPlus
ln -s /path/to/coaxolotl/sdrpp/coaxolotl_source source_modules/coaxolotl_source
echo 'add_subdirectory("source_modules/coaxolotl_source")' >> CMakeLists.txt
mkdir build && cd build
cmake .. && make -j8 coaxolotl_source
```

Copy `build/source_modules/coaxolotl_source/coaxolotl_source.so` into your
SDR++ plugin folder (for example `/usr/lib/sdrpp/plugins`). Then add an
instance in **Module Manager** (type `coaxolotl_source`), or add
`"Coaxolotl Source": { "module": "coaxolotl_source", "enabled": true }` to
`moduleInstances` in `config.json`. Do not run it next to the stock RTL-SDR
source on the same dongle. It needs librtlsdr with
`rtlsdr_set_bias_tee()` (any current rtl-sdr or rtl-sdr-blog build).

## Tests without SDR++

The link code (`src/coaxlink.*`: frames, edge player, reply parser,
demodulator, band plan) has no SDR++ dependency:

```bash
make -C sdrpp test                                   # 33 checks
sdrpp/build/test_coaxlink capture.cf32 2.4e6         # decode a capture
```

`host/test_coaxolotl.py` also feeds a Python-synthesised capture through
this binary, so the C++ and the Python decoder are checked against each
other and against the firmware's packets.

## Status

* ✅ The link code passes its tests and decodes captures with the carrier
  anywhere in ±0.75 MHz, chips ±10 %, a strong steady interferer and a
  per-bin SNR down to about 17 dB. The real link should give about 40 dB.
* ⚠️ `main.cpp` compiles against the SDR++ headers but has not run inside
  SDR++ or against a real box yet.
