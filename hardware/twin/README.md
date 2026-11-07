# Control board digital twin

Checks the fabricated control board (`hardware/kicad_ctrl/coaxolotl.kicad_pcb`) against
the data sheets and the docs. It never checks against our own schematic. Each layer
injects faults into a copy of the board (`--selftest`), and a check only counts if
it catches its fault.

| Layer | Script | What it checks | Needs |
|---|---|---|---|
| spec | `spec_ctrl.py` | pin tables (LM66100, TPS2553, INA180, MCP1700, 74HC595, EFM8BB52 vendor symbol), the lamp map, the J6 table and about 90 design-intent assertions, each with its source | – |
| 1 | `twin_check.py` | pad → net against the spec: supplies, pull-ups and pull-downs, both current monitors (IN+ on the bus side), the coax DC feed with the shunt last, the beacon tap, the 595 chain, all 48 lamps (anode rail, cathode → 2.2 k → the register output that `hal_panel` drives, colour from `docs/panel.md`), the lamp and J6 silk labels, and the snap-off link | Python 3 |
| 3 | `twin_analog.py` | copper max-flow on the DC path (IPC-2221, 20 K); Kelvin cut test on both shunts; firmware constants (`config.h`, `hal_efm8bb52.c`) recomputed from the fitted values; INA180 range; TPS2553 IOS from RILIM; beacon level; power budget against the V3 bias-T; capacitor ratings | KiCad Python |
| 4 | `twin_mech.py` | 100 × 70 mm outline, parts on top, strip parts above the break slot, J4/J5 aligned, 2.54 mm headers, J3 notch and edge launch, M3 head keep-out | Python 3 |
| 5 | `twin_dfm.py` | the JLC DFM categories (via-to-pad, pad spacing, slots, silk, rings, THT to SMD). Thermal vias inside an exposed pad are listed as WARN | KiCad Python |

Exit codes: 0 clean, 1 FAIL, 2 missing input, 3 a selftest fault was missed.

```
"C:\Program Files\KiCad\10.0\bin\python.exe" hardware\twin\twin_check.py  hardware\kicad_ctrl\coaxolotl.kicad_pcb --selftest -q
"C:\Program Files\KiCad\10.0\bin\python.exe" hardware\twin\twin_analog.py hardware\kicad_ctrl\coaxolotl.kicad_pcb --selftest -q
"C:\Program Files\KiCad\10.0\bin\python.exe" hardware\twin\twin_mech.py   hardware\kicad_ctrl\coaxolotl.kicad_pcb --selftest -q
"C:\Program Files\KiCad\10.0\bin\python.exe" hardware\twin\twin_dfm.py    hardware\kicad_ctrl\coaxolotl.kicad_pcb --selftest -q
```

`postroute.ps1` (with `COAX_VARIANT=ctrl`) runs all four as its last steps.

Layer 2 (mating parts) has nothing to check yet. J6 mates with an RF board that does
not exist yet, and the J4/J5 link is covered by layers 1 and 4.

What the twin cannot settle is printed as `WARN [physical]`. That covers the EFM8BB52
ADC channel numbers (the data sheet could not be downloaded), the LED polarity in the
JLC preview, and the RF board's J6 mate.
