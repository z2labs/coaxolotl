# KiCad project

Generated schematic. **Edit `tools/build.py`, then regenerate:**

```bash
python3 hardware/kicad/tools/build.py      # writes *.kicad_sch + coaxolotl.kicad_sym
python3 hardware/kicad/tools/check.py      # netlist vs. intent, single-pin nets, undriven power
kicad-cli sch export pdf -o hardware/kicad/coaxolotl-schematic.pdf hardware/kicad/coaxolotl.kicad_sch
```

* Written in the KiCad 7 file format. KiCad 8 and 9 open it, and upgrade it
  when you save.
* KiCad 8/9 users should also run `kicad-cli sch erc`. KiCad 7's CLI has no
  ERC, which is why `check.py` exists.
* Style: every pin has a short stub to a net label or power port. Nets in
  CAPS that cross sheets are global labels.
* Project symbols (EFM8BB21 QSOP-24, LM66100, TPS2553) live in
  `coaxolotl.kicad_sym`. Everything else comes from the stock KiCad libraries.
* Fields marked `VERIFY` (Note field) still need a datasheet check.

| Sheet | Status |
|---|---|
| Power | v0.1 for review |
| MCU | v0.1 for review |
| RF | next |
| Panel | next |
