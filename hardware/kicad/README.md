# KiCad project

Generated schematic. **Edit `tools/build.py`, then regenerate:**

```bash
python3 hardware/kicad/tools/build.py      # writes *.kicad_sch + coaxolotl.kicad_sym + .kicad_pro
python3 hardware/kicad/tools/check.py      # netlist vs. intent, shorts/splits, undriven power, style
python3 hardware/kicad/tools/bom.py        # grouped BOM -> hardware/bom.csv
kicad-cli sch export pdf -o hardware/kicad/coaxolotl-schematic.pdf hardware/kicad/coaxolotl.kicad_sch
```

## Style (AI PCB Dev schematic guide)

- Text uses the Arial TrueType face (KiCad 7+ falls back to its own font if Arial is missing).
- One function per A3 sheet. Blocks are dashed frames with bold italic titles.
- Power and ground appear **only as power symbols**: rails point up, GND points down, and every part gets its own GND. The project rails (VCOAX, VMAIN, VHOLD, VLED, LNA_VCC) are real power symbols in `coaxolotl.kicad_sym`.
- Inside a block, parts connect with **wires**. Between sheets they connect through **hierarchical labels** and sheet pins. No global labels.
- Signal flow runs left to right. Wires are orthogonal, text is horizontal, and junctions are added automatically. A 4-way junction is a hard error in the generator.
- **Value carries the critical parameters**, e.g. `10k/1%`, `100nF/16V/10%`, `LM66100DCKR(SC70-6)`.
- **Design intent** is written on the sheet in blue: formulas, current limits, ride-through times, VERIFY items.
- **Placement metadata** is stored in fields on every part:
  - `Zone`: POWER / ANALOG / DIGITAL / RF;
  - `Place`: satellite of `REF.PIN`, or `edge`.
- Net classes live in `.kicad_pro`. The POWER class is 0.4 mm, at most 1.25 × the smallest pad it lands on (SC-70). Clearances come from the fab `.kicad_dru`.

## Sheets

| # | Sheet | Status |
|---|---|---|
| 1 | Block diagram + hierarchy | v0.2 |
| 2 | Power | v0.2 for review |
| 3 | MCU | v0.2 for review |
| 4 | RF front end: protection, LNA/bypass SPDTs, preselector, FM notch (DNP), LNA | v0.2 for review |
| 5 | Filter bank: 2× PE42582, F0–F7, bias-T, coax | v0.2 for review; F1–F7 are placeholders |
| 6 | Panel: 6× 74HC595, 48 lamps, 2k2 arrays, OE_N pull-up | v0.2 for review |

## Checks and limits

- The file format is KiCad 7, the newest version installable in the generator environment. KiCad 8, 9 and 10 open it and upgrade it on save.
- KiCad 7's CLI has no ERC. `check.py` exports the netlist and proves, net by net, that the drawn wires give exactly the intended connectivity. **On KiCad 10, also run `kicad-cli sch erc`.**

## PCB (KiCad 10, on Windows)

Generated as well: placement, routing and post-processing are scripts, run with KiCad's own Python.

```powershell
$py = "C:\Program Files\KiCad\10.0\bin\python.exe"
& $py hardware\kicad\tools\pcb_build.py      # outline, stack-up, placement, GND vias, zones -> coaxolotl_placed.kicad_pcb
& $py hardware\kicad\tools\route.py          # srouter passes + repair -> coaxolotl.kicad_pcb
powershell -ExecutionPolicy Bypass -File hardware\kicad\tools\postroute.ps1   # tidy, stitch, silk, DRC (-From <step>)
```

`route.py` needs srouter (`SROUTER`, default the `srouter-feat` build) and `postroute.ps1` its tools folder
(`SROUTER_TOOLS`, default `srouter-jlc\tools`).

**Board.** 100 × 102 mm, 4 layers (JLC04161H-7628), every part on the top.

| y (mm) | |
|---|---|
| 0–30 | lamp strip: 2 × 24 lamps under their names, 6 × 74HC595 (each under the 4 columns it drives), 12 arrays, J5 |
| 30–32 | routed slot; left and right tabs with 0.6 mm mouse-bite holes. The 7 strip links cross the right tab, J5 pin k straight to J4 pin k |
| 32–102 | main board: ANT SMA left, RF axis at y = 64, filter bank in the middle, RTL-SDR SMA right, power and hold-up at the bottom, MCU top right |

Snap the strip off at the slot and it still works on a 7-wire cable from J5 to J4 (same pin order).

**Layers.** F.Cu: RF (50 Ω CPWG, 0.35 mm, 0.25 mm pour gap), parts, GND pour. In1.Cu: solid GND, the RF
reference; nothing is routed on it. In2.Cu: power and control, GND fill. B.Cu: control, GND pour.

**RF layout.** Each RF path is laid along a line: series parts in line, shunt parts hanging off one side, each with
its own GND via. The filter bank is 8 nested lanes between U8 and U11 (the LS strapping makes the switch pair
planar): F4/F3 innermost next to the control corridor, F7/F0 outermost. The LNA path is a loop under U6–U7,
the FM-notch switches U9/U10 sit at both ends of the R22 link. Chain links are pre-routed pad to pad; the router
only joins the switch pins to the lanes, at 0.25 mm (the QFN pin width), and `route.py` widens every RF segment to
0.35 mm where the clearance allows.

**Routing passes** (srouter, `-style oct`): RF on F.Cu only, then power, then the lamp strip with the MCU's lamp
bus and the debug header, then the rest; a repair pass picks up connections KiCad sees open. GND is never
routed: pours, the In1 plane, one via per GND pad (placed by `pcb_build.py`), stitching afterwards.

**JLC DFM.** `postroute.ps1` ends with srouter's `jlc_dfm_check.py`. Its `via-pad` FAIL on the exposed pads (U6–U11,
U13: 0.45/0.2 mm vias inside the thermal pad) is accepted by design: small open vias in an exposed pad do not wick the
joint dry (Zoltan's production practice), and the RF switches and the LNA need the paddle grounded right under the
die. No POFV. Every other via must keep 0.15 mm from SMD pads (`via_off_pad`).
