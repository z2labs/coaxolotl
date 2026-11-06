# PC software

Everything on the PC side is Python for now. It talks to the box through the
RTL-SDR the box hangs on: the bias-T keys the commands, the tuner reads the
replies.

| File | What |
|---|---|
| `host/coaxolotl_gui.py` | control panel: connect, send, poll, telemetry, CSV log |
| `host/coaxolotl.py` | command line: `send`, `panel`, `listen`, `decode`, `bands` |
| `host/link.py` | `RtlLink` / `SimLink`: key a frame and read the reply, for your own scripts |
| `host/backchannel.py` | reply packet and demodulator |

The SDR++ module in `sdrpp/` does the same from inside SDR++. Use one or the
other: a dongle can only be opened by one program.

## Install (Windows)

1. **Python 3.10 or newer** from python.org. Tick "Add python.exe to PATH".
   tkinter comes with it.
2. **USB driver.** Plug in the RTL-SDR, run Zadig, pick "Bulk-In, Interface 0"
   and install WinUSB (the usual RTL-SDR set-up; skip it if SDR# or SDR++
   already work).
3. **librtlsdr with bias-T support.** Take `rtlsdr.dll` and `libusb-1.0.dll`
   from the rtl-sdr-blog release (Windows x64) and put them next to
   `coaxolotl_gui.py` or on the PATH. The older osmocom DLLs have no
   `rtlsdr_set_bias_tee()`.
4. **Python packages:**

   ```
   pip install -r host/requirements.txt
   ```

5. Test without the box first: `python host/coaxolotl_gui.py --sim` needs the
   firmware simulator (`make -C firmware sim`, i.e. gcc). On Windows without a
   compiler, skip this and connect to the dongle directly.

Linux: `pip install -r host/requirements.txt`, the distribution's `librtlsdr`
from rtl-sdr-blog (or a build of it), and `python3-tk`.

## Control panel

```
python host/coaxolotl_gui.py                   # RTL-SDR #0
python host/coaxolotl_gui.py -d 1 --log box.csv --connect
python host/coaxolotl_gui.py --sim             # the firmware simulator instead of a box
```

1. **Connect** opens the dongle and tunes it to the beacon (36.75 MHz,
   2.4 MS/s). It stays there: there is nothing to receive on this dongle while
   the panel runs.
2. **Bias-T** powers the box. On the first power-up the box answers with
   HELLO once its lamp test is over; press **Listen** right after switching
   the bias on to catch it.
3. Pick a branch, LNA and Quiet, then **Send**. The frame takes about 2.5 s,
   the reply about 1.4 s more. The Box section then shows:
   * ACK or NAK with the reason (SYNC, CHK, GAP, RUNT, TMO, DROP);
   * the branch and RF path the box actually set (red if it differs from what
     you sent), AUTO when the branch forbids the LNA, the FM notch;
   * VCOAX, the whole coax current (I_tot), the LNA current and the supercap
     voltage, green inside their windows; I_tot turns amber 20 mA before
     and red at the 170 mA budget;
   * the sticky flags TRIP, LOW, BUDGET, DROP, GLT, RST, and HOLD (supercap
     charged);
   * where the beacon was heard and the MCU oscillator error that implies.
4. **Poll** re-sends the current state (harmless, always answered),
   **Clear** sends it with CLR (clears the sticky flags, re-arms a tripped
   LNA), **Panel** full / bands / off sets the lamp mode (kept in the box's
   flash). **Poll every** N s keeps an eye on the box; with `--log` every
   reply goes to a CSV file.

"no reply" with an on/off ratio below about 15 dB means nothing keyed on
the beacon frequency: check the tap (R31–R33, C47 on the control board) and
the gain. A NAK means the box heard the frame but rejected it; GAP or SYNC
NAKs point to keying jitter (the panel shows it per frame) or a slow bias
fall time (`docs/hardware.md`, fall-time budget).

The control board alone (no RF board on J6) has no load on the LNA supply:
it reports about 0 mA and lights I-LO. That is expected.

## Calibration

Each reading can be trimmed against a meter; the box keeps the factors in
flash. Poll first, then measure the same quantity with a meter, pick the
channel (vcoax, vscap, ilna, itot), type the meter reading and press
**Apply**. The panel sends 5 frames (about 15 s) and shows the new reading.
**Reset to 1.0** forgets that channel's factor.

```
python host/coaxolotl.py cal vcoax 4.312 --band 2m --lna   # V for vcoax/vscap, mA for ilna/itot
python host/coaxolotl.py cal itot --reset
```

Measure I_TOT with the meter in series with the coax centre (or the bias-T
supply), VCOAX at TP6's divider input (the coax side of R3).

## Scripts

```python
from coaxolotl import Frame, ConfigFrame
from link import RtlLink

with RtlLink(index=0, gain=20) as box:
    box.bias(True)
    t = box.transact(Frame(bank=6, lna=True))
    if t.reply:
        print(t.reply.summary(), f"jitter {t.jitter_ms:.1f} ms")
    else:
        print("no reply:", t.heard.note)
    box.transact(ConfigFrame(panel=1))           # lamps: bank row only
    box.calibrate("vcoax", 4.312, t.reply.vcoax_v)   # meter vs. box
```

`SimLink()` has the same methods and runs the firmware simulator instead,
keeping the box state between calls. `host/test_link.py` uses it.
