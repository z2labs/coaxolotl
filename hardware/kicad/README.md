# KiCad project

Generated schematic. **Edit `tools/build.py`, then regenerate:**

```bash
python3 hardware/kicad/tools/build.py      # writes *.kicad_sch + coaxolotl.kicad_sym + .kicad_pro
python3 hardware/kicad/tools/check.py      # netlist vs. intent, shorts/splits, undriven power, style
kicad-cli sch export pdf -o hardware/kicad/coaxolotl-schematic.pdf hardware/kicad/coaxolotl.kicad_sch
```

## Style (Olimex reference, AI PCB Dev schematic guide)

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
| 4 | RF | placeholder: only the control inputs with reset-safe pull-downs |
| 5 | Panel | placeholder: lamp-chain inputs, OE_N pull-up |

## Checks and limits

- The file format is KiCad 7, the newest version installable in the generator environment. KiCad 8, 9 and 10 open it and upgrade it on save.
- KiCad 7's CLI has no ERC. `check.py` exports the netlist and proves, net by net, that the drawn wires give exactly the intended connectivity. **On KiCad 10, also run `kicad-cli sch erc`.**
