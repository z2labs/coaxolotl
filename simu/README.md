# Filter bank simulation

Two levels, combined in Python (scikit-rf); QucsStudio can read every Touchstone file written here.

1. **Circuit level** (`bank_sim.py`): every branch F0–F7 is built straight from the KiCad netlist,
   each L and C with its real model: the vendor S-parameter file where the maker publishes one,
   otherwise the datasheet RLC model (Q over frequency, SRF, DCR for inductors; ESL, DF/ESR for
   capacitors) from `models/parts.json`. Monte Carlo over the part tolerances, what-if value changes.
2. **Layout level** (openEMS on office-z170, `kicad_sim.py` of the z2 twin): for the VHF/UHF lanes
   (F5–F7, THRU) the copper between the parts - pads, CPWG, shunt GND vias to In1, lane-to-lane
   coupling - as an N-port with a lumped port on every part pad. The parts are then connected into
   that N-port here, so the layout runs once and the parts can be swapped and swept without a new
   field simulation. Below ~30 MHz the layout is electrically tiny and the circuit level is enough.

```bash
python3 simu/bank_sim.py --ideal --tag _ideal     # the design check (ideal L/C)
python3 simu/bank_sim.py --mc 300                 # part models + Monte Carlo over the tolerances
python3 simu/bank_sim.py --set L401=680n --tag _l401   # what-if
python3 simu/bank_sim.py --layout 6=openems/results/f6 --tag _layout   # F6 with the openEMS layout N-port
python3 simu/f6_layout_plot.py                    # F6 parts vs parts + layout, out/f6_layout.png
```

Output in `simu/out/`: `bank*.png` (S21/S11 of all branches, band shaded), `summary*.json`
(worst in-band IL and RL), `F<k>*.s2p`.

## Status

| step | state |
|---|---|
| netlist -> branch circuits, ideal check | done |
| part models from datasheets (`models/parts.json`, Q min, SRF min: pessimistic) | done, first run `out/bank_models.png` |
| vendor s2p (Murata LQW18, `models/s2p/`) | done for the 6 LQW18 parts |
| PE42582 switch S-parameters in the chain | to do (pSemi model) |
| openEMS layout N-port F6 (`openems/results/f6/`, 20 pad ports, 10 MHz-1.5 GHz, copper as PEC sheet, no mask; 20 excitations x 18 min on the GTX 1080) | done: passband IL +0.1 dB, RL 16.0 dB (was 16.6), -3 dB band 92-215 MHz (93-220); stopband above 700 MHz limited to about -45...-60 dB by pad-to-pad coupling (`out/f6_layout.png`) |
| openEMS layout N-port F7 (`openems/results/f7/`, X99, 10 MHz-2.5 GHz, routed board 6cacac4) | preliminary (X99 had the 2026-10-19 kicad_sim: no fine mesh for non-port nets, diagonal tracks, signal vias; re-run with the 2026-10-21 version queued): every lane node carries 0.4-0.9 pF of pad capacitance to In1 (0603 pads over 0.2 mm prepreg); RL drops to 5.8 dB and IL rises to 1.5 dB above 1 GHz (`out/f7_layout.png`). Retune 12p/6.8p/12p, 10n/10n (`f7_retune.py`, `out/f7_retune.png`): 70 cm IL 1.0 dB RL 10.2 dB, ADS-B IL 0.27 dB RL 20.3 dB, 23 cm IL 0.40 dB RL 13.4 dB |
| F7 with In1/In2 cleared under the signal pads (`void_pads.py`, `openems/results/f7void/`) | ran, not conclusive: the ports then reference B.Cu 1.5 mm below and their own ~1 nH path enters the series parts |
| openEMS THRU (U8/U11 pins + R21) | first run invalid: 45 deg tracks, kicad_sim meshes only their bounding box, the coarse mesh lost them (all ports open); re-run with --coarse 0.12 |
| openEMS layout N-port F5 | running, split: office-z170 excitations 1 up, X99 20 down (`--exc`, `Z2_TWIN` = the office-z170 kicad_sim, same 9.84 MCells mesh), merged with `--collect` |
