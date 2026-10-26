#!/usr/bin/env python3
"""Generate the Coaxolotl KiCad project (Olimex-style, hand-placed and wired).

    python3 hardware/kicad/tools/build.py
    python3 hardware/kicad/tools/check.py

Style (AI PCB Dev schematic guide, Olimex reference):
  * one function per A3 sheet, dashed blocks with bold italic titles
  * power/ground only as power symbols (rails up, GND down), one GND per part
  * wires inside a block, hierarchical labels between sheets, no global labels
  * left-to-right signal flow, orthogonal wires, horizontal text
  * Value carries the critical parameters (10k/1%, 100nF/16V/10%, IC(package))
  * design intent written on the sheet in blue
Edit this file, not the generated .kicad_sch files.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import schgen as g  # noqa: E402
from draw import DSheet  # noqa: E402
from schgen import Part, Sym  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent
PROJECT = "coaxolotl"
REV = "0.2"

R0603 = "Resistor_SMD:R_0603_1608Metric"
R1206 = "Resistor_SMD:R_1206_3216Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"
SOD323 = "Diode_SMD:D_SOD-323"
LED0603 = "LED_SMD:LED_0603_1608Metric"
FB0603 = "Inductor_SMD:L_0603_1608Metric"
TP = "TestPoint:TestPoint_Pad_D1.0mm"

RAILS = ["VCOAX", "VMAIN", "VHOLD", "VLED", "LNA_VCC"]

# ---------------------------------------------------------------------------
# project symbol library


def _eff():
    return [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]]]


def _pin(ptype, x, y, ang, name, num):
    return [Sym("pin"), Sym(ptype), Sym("line"), [Sym("at"), x, y, ang], [Sym("length"), 2.54],
            [Sym("name"), name, _eff()], [Sym("number"), num, _eff()]]


def ic_symbol(name, ref, footprint, datasheet, desc, left, right, width=15.24, pin_types=None,
              top_pins=(), bottom_pins=()):
    """KLC-style box: inputs left, outputs right, supply top, ground bottom."""
    pin_types = pin_types or {}
    n = max(len(left), len(right), 1)
    top = round(((n - 1) * 2.54 / 2) / 1.27) * 1.27
    half = width / 2
    ybot = top - n * 2.54
    body = [Sym("symbol"), f"{name}_0_1",
            [Sym("rectangle"), [Sym("start"), -half, top + 2.54], [Sym("end"), half, ybot],
             [Sym("stroke"), [Sym("width"), 0.254], [Sym("type"), Sym("default")]],
             [Sym("fill"), [Sym("type"), Sym("background")]]]]
    pins = [Sym("symbol"), f"{name}_1_1"]
    for side, lst in ((-1, left), (1, right)):
        for i, p in enumerate(lst):
            if p is None:
                continue
            num, pname = p
            pins.append(_pin(pin_types.get(num, "bidirectional"), side * (half + 2.54), top - i * 2.54,
                             0 if side < 0 else 180, pname, num))
    for edge, lst in ((1, top_pins), (-1, bottom_pins)):
        k = len(lst)
        for i, (num, pname) in enumerate(lst):
            x = round(((i - (k - 1) / 2) * 5.08) / 1.27) * 1.27
            y = (top + 5.08) if edge > 0 else (ybot - 2.54)
            pins.append(_pin(pin_types.get(num, "bidirectional"), x, y, 270 if edge > 0 else 90, pname, num))

    def prop(k, v, y, hide=False):
        eff = _eff()
        if hide:
            eff.append(Sym("hide"))
        return [Sym("property"), k, v, [Sym("at"), 0, y, 0], eff]

    return [Sym("symbol"), name, [Sym("in_bom"), Sym("yes")], [Sym("on_board"), Sym("yes")],
            prop("Reference", ref, top + 3.81), prop("Value", name, ybot - 1.27),
            prop("Footprint", footprint, 0, True), prop("Datasheet", datasheet, 0, True),
            prop("Description", desc, 0, True), body, pins]


def rail_symbol(lib, net):
    """Copy of power:+3V3 renamed to a rail; pin name = net, so it works in KiCad 7 and 10."""
    src = lib.get("power:+3V3")

    def ren(node):
        if isinstance(node, list):
            out = [ren(e) for e in node]
            if len(out) > 1 and out[0] == "symbol" and isinstance(out[1], str):
                out[1] = out[1].replace("power:+3V3", net).replace("+3V3", net)
            if len(out) > 2 and out[0] == "property" and out[1] == "Value":
                out[2] = net
            if len(out) > 2 and out[0] == "property" and out[1] == "Description":
                out[2] = f"Power rail {net}"
            if len(out) > 1 and out[0] == "name" and out[1] == "+3V3":
                out[1] = net
            return out
        return node
    return ren(src)


def build_library(path: pathlib.Path):
    base = g.Library()
    syms = [
        ic_symbol(
            "EFM8BB21F16G-C-QSOP24", "U", "Package_SO:QSOP-24_3.9x8.7mm_P0.635mm",
            "https://www.silabs.com/documents/public/data-sheets/efm8bb2-datasheet.pdf",
            "EFM8 Busy Bee 8051 MCU, 16 kB flash. Pins grouped: inputs left, outputs right",
            left=[("7", "RSTb/C2CK"), ("8", "P3.0/C2D")] + [None] * 7 + [("23", "P0.5")] + [None] * 5 +
                 [("1", "P0.3/ADC")] + [None] * 5 + [("2", "P0.2/ADC")] + [None] * 5 +
                 [("4", "P0.0/PMATCH"), ("3", "P0.1/ADC")] + [None] * 5 + [("24", "P0.4/TX")],
            right=[("22", "P0.6"), ("21", "P0.7"), ("20", "P1.0"), ("19", "P1.1"), ("18", "P1.2"),
                   ("17", "P1.3"), None, ("12", "P2.0"), ("11", "P2.1"), ("10", "P2.2"), ("9", "P2.3"),
                   None, ("16", "P1.4"), ("15", "P1.5"), ("14", "P1.6"), ("13", "P1.7")],
            width=30.48, top_pins=[("6", "VDD")], bottom_pins=[("5", "GND")],
            pin_types={"6": "power_in", "5": "power_in", "7": "input"}),
        ic_symbol(
            "LM66100DCK", "U", "Package_TO_SOT_SMD:SOT-363_SC-70-6",
            "https://www.ti.com/lit/ds/symlink/lm66100.pdf",
            "Ideal diode 1.5-5.5 V 1.5 A; CE tied to VOUT = reverse blocking",
            left=[("1", "VIN"), ("4", "NC")], right=[("6", "VOUT"), ("3", "CE")],
            bottom_pins=[("2", "GND"), ("5", "~{ST}")],
            pin_types={"1": "power_in", "6": "power_out", "2": "power_in", "3": "input",
                       "5": "open_collector", "4": "no_connect"}),
        ic_symbol(
            "TPS2553DBV", "U", "Package_TO_SOT_SMD:SOT-23-6",
            "https://www.ti.com/lit/ds/symlink/tps2553.pdf",
            "Current-limited load switch, ILIM 15-232 kOhm, EN active high, 8 ms FAULT deglitch",
            left=[("1", "IN"), ("3", "EN")], right=[("6", "OUT"), ("5", "ILIM"), ("4", "~{FAULT}")],
            bottom_pins=[("2", "GND")],
            pin_types={"1": "power_in", "6": "power_out", "2": "power_in", "3": "input",
                       "4": "open_collector", "5": "passive"}),
        ic_symbol(
            "INA180A2", "U", "Package_TO_SOT_SMD:SOT-23-5",
            "https://www.ti.com/lit/ds/symlink/ina180.pdf",
            "Current sense amplifier 50 V/V; sense inputs on top for Kelvin routing from the shunt",
            left=[None, ("5", "V+"), None], right=[None, ("1", "OUT"), None],
            top_pins=[("3", "IN+"), ("4", "IN-")], bottom_pins=[("2", "GND")],
            pin_types={"5": "power_in", "2": "power_in", "1": "output", "3": "input", "4": "input"}),
    ]
    syms += [rail_symbol(base, r) for r in RAILS]
    lib = [Sym("kicad_symbol_lib"), [Sym("version"), 20220914], [Sym("generator"), Sym("coaxolotl_build")]]
    path.write_text(g.dump(lib + syms) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# part helpers (Value = critical parameters, Olimex style)


def R(ref, val, x, y, a, b, rot=0, fp=R0603, txt=None, **f):
    return Part(ref, "Device:R", val, x, y, rot, fp, {"1": a, "2": b}, dict(f), txt=txt)


def C(ref, val, x, y, a, b, rot=0, fp=C0603, pol=False, txt=None, **f):
    return Part(ref, "Device:C_Polarized" if pol else "Device:C", val, x, y, rot, fp, {"1": a, "2": b},
                dict(f), txt=txt)


def D(ref, val, x, y, anode, cathode, rot=180, fp=SOD323, lib="Device:D_Schottky", txt=None, **f):
    return Part(ref, lib, val, x, y, rot, fp, {"2": anode, "1": cathode}, dict(f), txt=txt)


def sheet(lib, name, file, comment):
    return DSheet(lib, PROJECT, name, file, rev=REV, comment=comment)


# ---------------------------------------------------------------------------
# sheet 2: POWER


def sheet_power(lib) -> DSheet:
    s = sheet(lib, "Power", "power.kicad_sch",
              "VCOAX tap, ideal diode, LNA supply switch + current monitor, lamp rail, hold-up, 3V3")

    # A ---- VCOAX TAP -------------------------------------------------------
    s.zone = "POWER"
    s.frame(15.24, 38.1, 119.38, 50.8, "VCOAX tap")
    s.add(D("D1", "SMF5.0A(SOD123F)", 33.02, 55.88, "GND", "VCOAX", rot=270, fp="Diode_SMD:D_SOD-123F",
            lib="Device:D_Zener", Place="at J_COAX (RF sheet)", txt=(36.83, 54.61)))
    s.add(C("C1", "100nF/16V/10%", 58.42, 55.88, "VCOAX", "GND"))
    s.add(R("R1", "680R/1%", 76.2, 55.88, "VCOAX", "GND", Note="sets the OOK falling edge"))
    s.add(R("R2", "3k3/5%", 91.44, 55.88, "VCOAX", "PWR_LED_A"))
    s.add(Part("D2", "Device:LED", "LED/GREEN(0603)", 91.44, 68.58, 90, LED0603,
               {"2": "PWR_LED_A", "1": "GND"}, {"Note": "PWR lamp, works without MCU"}, txt=(94.74, 67.31)))
    s.zone = "ANALOG"
    s.add(R("R3", "10k/1%", 111.76, 55.88, "VCOAX", "SENSE"))
    s.add(R("R4", "22k/1%", 111.76, 68.58, "SENSE", "GND"))
    s.zone = "POWER"
    s.rail_pin("D1", "1", "VCOAX")
    s.w((33.02, 50.8), (22.86, 50.8))
    s.flag(22.86, 50.8, "VCOAX")
    s.gnd("D1", "2")
    for ref in ("C1", "R1"):
        s.rail_pin(ref, "1", "VCOAX")
        s.gnd(ref, "2")
    s.rail_pin("R2", "1", "VCOAX")
    s.w(s.pt("R2", "2"), s.pt("D2", "2"))
    s.gnd("D2", "1")
    s.rail_pin("R3", "1", "VCOAX")
    s.w(s.pt("R3", "2"), s.pt("R4", "1"))
    s.gnd("R4", "2")
    s.w((111.76, 62.23), (124.46, 62.23))
    s.hlabel("SENSE", 124.46, 62.23, "R", "output")
    s.intent("SENSE = 0.6875 x VCOAX (3.0 V at 4.4 V)", 17.78, 81.28)
    s.intent("t_fall = 1.1 x R_eff x C_total: R1 sets the OOK falling edge", 17.78, 84.46)

    # B ---- IDEAL DIODE -------------------------------------------------------
    s.frame(139.7, 38.1, 55.88, 50.8, "Ideal diode")
    s.add(Part("U1", "coaxolotl:LM66100DCK", "LM66100DCKR(SC70-6)", 161.29, 59.69, 0,
               "Package_TO_SOT_SMD:SOT-363_SC-70-6",
               {"1": "VCOAX", "4": None, "6": "VMAIN", "3": "VMAIN", "2": "GND", "5": "GND"},
               txt=(153.67, 50.8)))
    s.add(C("C3", "10uF/10V/20%", 180.34, 62.23, "VMAIN", "GND", fp=C0805, Place="U1.6"))
    s.rail_pin("U1", "1", "VCOAX")
    s.nc("U1", "4")
    s.w(s.pt("U1", "6"), (187.96, 58.42), (187.96, 55.88))
    s.rail("VMAIN", 187.96, 55.88)
    s.pw("U1", "3", (175.26, None), (None, 58.42))
    s.gnd("U1", "2")
    s.gnd("U1", "5")
    s.gnd("C3", "2")
    s.intent("CE = VOUT: comparator blocks", 142.24, 81.28)
    s.intent("reverse current in OOK gaps", 142.24, 84.46)

    # C ---- LNA SUPPLY ----------------------------------------------------------
    s.frame(200.66, 38.1, 139.7, 68.58, "LNA supply + current monitor")
    s.add(Part("U2", "coaxolotl:TPS2553DBV", "TPS2553DBVR(SOT23-6)", 238.76, 58.42, 0,
               "Package_TO_SOT_SMD:SOT-23-6",
               {"1": "VMAIN", "3": "LNA_EN", "6": "LNA_SW", "5": "ILIM", "4": "FAULT_N", "2": "GND"},
               txt=(231.14, 48.26)))
    s.add(C("C4", "1uF/16V/10%", 215.9, 59.69, "VMAIN", "GND", Place="U2.1"))
    s.add(R("R14", "10k/5%", 218.44, 74.93, "LNA_EN", "GND", Note="LNA off while MCU in reset"))
    s.add(R("R5", "150k/1%", 251.46, 64.77, "ILIM", "GND", Place="U2.5",
            Note="VERIFY vs TPS2553 Eq.1: IOS(min) > 130 mA"))
    s.add(C("C5", "1uF/16V/10%", 264.16, 59.69, "LNA_SW", "GND", Place="U2.6"))
    s.add(R("R7", "0R39/1%/0.25W", 281.94, 55.88, "LNA_SW", "LNA_VCC", rot=90, fp=R1206,
            MPN="ERJ-8BQFR39V", txt=(281.94, 49.53, None)))
    s.zone = "ANALOG"
    s.add(Part("U3", "coaxolotl:INA180A2", "INA180A2IDBVR(SOT23-5)", 281.94, 78.74, 0,
               "Package_TO_SOT_SMD:SOT-23-5",
               {"3": "LNA_SW", "4": "LNA_VCC", "5": "+3V3", "1": "ILNA", "2": "GND"},
               txt=(287.02, 89.92)))
    s.add(C("C6", "100nF/16V/10%", 264.16, 82.55, "+3V3", "GND", Place="U3.5"))
    s.zone = "POWER"
    s.w(s.pt("U2", "1"), (223.52, 55.88), (215.9, 55.88))
    s.w((223.52, 55.88), (223.52, 53.34))
    s.rail("VMAIN", 223.52, 53.34)
    s.gnd("C4", "2")
    s.pw("U2", "3", (226.06, None), (None, 71.12), (210.82, None))
    s.hlabel("LNA_EN", 210.82, 71.12, "L", "input")
    s.gnd("R14", "2")
    s.pw("U2", "5", (251.46, None), (None, 60.96))
    s.gnd("R5", "2")
    s.pw("U2", "4", (None, 96.52), (325.12, None))
    s.hlabel("FAULT_N", 325.12, 96.52, "R", "output")
    s.gnd("U2", "2")
    s.w(s.pt("U2", "6"), s.pt("R7", "1"))
    s.gnd("C5", "2")
    s.w(s.pt("R7", "2"), (297.18, 55.88), (297.18, 53.34))
    s.rail("LNA_VCC", 297.18, 53.34)
    s.pw("U3", "3", (None, 66.04), (274.32, None), (None, 55.88))
    s.pw("U3", "4", (None, 66.04), (289.56, None), (None, 55.88))
    s.rail_pin("U3", "5", "+3V3")
    s.w((269.24, 78.74), (264.16, 78.74))
    s.gnd("C6", "2")
    s.gnd("U3", "2")
    s.pw("U3", "1", (325.12, None))
    s.hlabel("ILNA", 325.12, 78.74, "R", "output")
    s.intent("Ilim = f(R5), TPS2553 Eq.1: VERIFY IOS(min) > 130 mA", 203.2, 101.6)
    s.intent("ILNA = 0.39R x 50 V/V = 19.5 mV/mA (100 mA -> 1.95 V); Kelvin taps at R7 pads", 203.2, 104.78)

    # D ---- LAMP RAIL -------------------------------------------------------------
    s.frame(345.44, 38.1, 58.42, 50.8, "Lamp rail")
    s.add(Part("FB1", "Device:FerriteBead_Small", "FB0603/600R/1.3A", 363.22, 55.88, 90, FB0603,
               {"1": "VMAIN", "2": "VLED"}, {"MPN": "BLM18KG601SN1D"}))
    s.add(C("C8", "47uF/10V/20%", 375.92, 59.69, "VLED", "GND", fp=C1206))
    s.w(s.pt("FB1", "1"), (353.06, 55.88), (353.06, 53.34))
    s.rail("VMAIN", 353.06, 53.34)
    s.w(s.pt("FB1", "2"), (391.16, 55.88), (391.16, 53.34))
    s.rail("VLED", 391.16, 53.34)
    s.gnd("C8", "2")
    s.intent("48 lamps x 1 mA max on VMAIN:", 347.98, 78.74)
    s.intent("dark in OOK gaps, no hold-up drain", 347.98, 81.92)

    # E ---- HOLD-UP ---------------------------------------------------------------
    s.frame(15.24, 119.38, 121.92, 76.2, "Hold-up (diode-OR)")
    s.add(R("R9", "100R/5%", 35.56, 132.08, "VMAIN", "HU_FAST", rot=90))
    s.add(D("D3", "BAT54WS(SOD323)", 50.8, 132.08, "HU_FAST", "VHOLD"))
    s.add(R("R10", "220R/5%", 35.56, 152.4, "VMAIN", "SC_CHG", rot=90))
    s.add(D("D4", "BAT54WS(SOD323)", 50.8, 152.4, "SC_CHG", "VSCAP"))
    s.add(D("D5", "BAT54WS(SOD323)", 93.98, 152.4, "VSCAP", "VHOLD", txt=(93.98, 146.05, None)))
    s.add(C("C9", "330uF/6.3V/20%", 114.3, 135.89, "VHOLD", "GND", pol=True,
            fp="Capacitor_Tantalum_SMD:CP_EIA-7343-31_Kemet-D", MPN="T520D337M006ATE025"))
    s.add(C("C10", "0.1F/5.5V", 76.2, 156.21, "VSCAP", "GND", pol=True,
            fp="Capacitor_THT:CP_Radial_D13.0mm_P5.00mm", MPN="KR-5R5V104-R",
            Note="VERIFY footprint vs chosen coin supercap"))
    s.zone = "ANALOG"
    s.add(R("R11", "1M/1%", 60.96, 156.21, "VSCAP", "SCAP_ADC"))
    s.add(R("R12", "1M/1%", 60.96, 168.91, "SCAP_ADC", "GND"))
    s.zone = "POWER"
    s.rail("VMAIN", 22.86, 129.54)
    s.w((22.86, 129.54), (22.86, 152.4), s.pt("R10", "1"))
    s.w((22.86, 132.08), s.pt("R9", "1"))
    s.w(s.pt("R9", "2"), s.pt("D3", "2"))
    s.w(s.pt("R10", "2"), s.pt("D4", "2"))
    s.w(s.pt("D4", "1"), s.pt("D5", "2"))
    s.w(s.pt("D5", "1"), (101.6, 152.4), (101.6, 132.08))
    s.w(s.pt("D3", "1"), (124.46, 132.08), (124.46, 129.54))
    s.rail("VHOLD", 124.46, 129.54)
    s.flag(106.68, 132.08, "VHOLD")
    s.gnd("C9", "2")
    s.gnd("C10", "2")
    s.w(s.pt("R11", "2"), s.pt("R12", "1"))
    s.gnd("R12", "2")
    s.w((60.96, 162.56), (43.18, 162.56))
    s.hlabel("SCAP_ADC", 43.18, 162.56, "L", "output")
    s.intent("fast path R9/D3: VHOLD up in ~30 ms (100R x 330uF) -> MCU boots", 17.78, 184.15)
    s.intent("slow path R10/D4: tau ~22 s (220R x 0.1F); ride-through ~4 min at 0.6 mA", 17.78, 187.33)
    s.intent("SCAP_ADC = VSCAP / 2 (HOLD lamp at >= 3.6 V)", 17.78, 190.5)

    # F ---- 3V3 -----------------------------------------------------------------
    s.frame(142.24, 119.38, 101.6, 50.8, "3V3 regulator")
    s.add(Part("U4", "Regulator_Linear:MCP1700x-330xxTT", "MCP1700T-3302E/TT(SOT23)", 185.42, 132.08, 0,
               "Package_TO_SOT_SMD:SOT-23", {"3": "VHOLD", "2": "+3V3", "1": "GND"},
               txt=(185.42, 121.92, None)))
    s.add(C("C12", "1uF/16V/10%", 167.64, 135.89, "VHOLD", "GND", Place="U4.3"))
    s.add(C("C13", "1uF/16V/10%", 203.2, 135.89, "+3V3", "GND", Place="U4.2"))
    s.w(s.pt("U4", "3"), (157.48, 132.08), (157.48, 129.54))
    s.rail("VHOLD", 157.48, 129.54)
    s.w(s.pt("U4", "2"), (218.44, 132.08), (218.44, 129.54))
    s.rail("+3V3", 218.44, 129.54)
    s.gnd("U4", "1")
    s.gnd("C12", "2")
    s.gnd("C13", "2")
    s.flag(233.68, 129.54, "GND")
    s.w((233.68, 129.54), (233.68, 134.62))
    s.rail("GND", 233.68, 134.62)
    s.intent("MCP1700: Iq 1.6 uA, dropout 178 mV;", 144.78, 160.02)
    s.intent("VO needs >= 1 uF ceramic", 144.78, 163.2)
    return s


# ---------------------------------------------------------------------------
# sheet 3: MCU


def sheet_mcu(lib) -> DSheet:
    s = sheet(lib, "MCU", "mcu.kicad_sch", "EFM8BB21F16G, C2 debug, ADC input conditioning")
    s.zone = "DIGITAL"
    s.frame(76.2, 53.34, 167.64, 142.24, "MCU")
    s.add(Part("U5", "coaxolotl:EFM8BB21F16G-C-QSOP24", "EFM8BB21F16G-C(QSOP24)", 165.1, 127.0, 0,
               "Package_SO:QSOP-24_3.9x8.7mm_P0.635mm",
               {"6": "+3V3", "5": "GND", "7": "RST_N", "8": "C2D", "23": "FAULT_N", "1": "ILNA_ADC",
                "2": "SCAP_ADC", "4": "SENSE", "3": "SENSE", "24": "TP_TX",
                "22": "LNA_EN", "21": "LNA_PATH", "20": "SW_V1", "19": "SW_V2", "18": "SW_V3", "17": "FMN",
                "12": "LED_SER", "11": "LED_SCK", "10": "LED_RCK", "9": "LED_OE_N",
                "16": None, "15": None, "14": None, "13": None},
               txt=(168.91, 176.53)))
    s.add(C("C15", "100nF/16V/10%", 172.72, 69.85, "+3V3", "GND", Place="U5.6 (closest)"))
    s.add(C("C14", "1uF/16V/10%", 193.04, 69.85, "+3V3", "GND", Place="U5.6"))
    s.w(s.pt("U5", "6"), (165.1, 63.5))
    s.rail("+3V3", 165.1, 63.5)
    s.w((165.1, 66.04), (193.04, 66.04))
    s.gnd("C15", "2")
    s.gnd("C14", "2")
    s.gnd("U5", "5")
    s.add(Part("J1", "Connector_Generic:Conn_01x04", "C2_DEBUG", 101.6, 86.36, 180,
               "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
               {"1": "GND", "2": "C2D", "3": "RST_N", "4": "+3V3"}, {"Place": "edge"}))
    s.add(R("R13", "10k/5%", 139.7, 76.2, "+3V3", "RST_N", Place="U5.7"))
    s.w(s.pt("J1", "3"), s.pt("U5", "7"))
    s.w(s.pt("J1", "2"), s.pt("U5", "8"))
    s.rail_pin("J1", "4", "+3V3")
    s.gnd("J1", "1")
    s.rail_pin("R13", "1", "+3V3")
    s.w(s.pt("R13", "2"), (139.7, 83.82))
    s.add(R("R6", "100k/5%", 129.54, 99.06, "+3V3", "FAULT_N", Place="U5.23"))
    s.pw("U5", "23", (99.06, None))
    s.hlabel("FAULT_N", 99.06, 106.68, "L", "input")
    s.rail_pin("R6", "1", "+3V3")
    s.w(s.pt("R6", "2"), (129.54, 106.68))
    s.zone = "ANALOG"
    s.add(R("R8", "1k/1%", 124.46, 121.92, "ILNA", "ILNA_ADC", rot=90, Place="U5.1"))
    s.add(C("C7", "100nF/16V/10%", 137.16, 125.73, "ILNA_ADC", "GND", Place="U5.1"))
    s.add(C("C11", "100nF/16V/10%", 137.16, 140.97, "SCAP_ADC", "GND", Place="U5.2"))
    s.add(C("C2", "10nF/50V/10%", 134.62, 156.21, "SENSE", "GND", Place="U5.4"))
    s.add(Part("TP6", "Connector:TestPoint", "TP/SENSE", 111.76, 152.4, 0, TP, {"1": "SENSE"}))
    s.zone = "DIGITAL"
    s.add(Part("TP1", "Connector:TestPoint", "TP/TX", 124.46, 170.18, 0, TP, {"1": "TP_TX"}))
    s.w(s.pt("R8", "2"), s.pt("U5", "1"))
    s.pw("R8", "1", (99.06, None))
    s.hlabel("ILNA", 99.06, 121.92, "L", "input")
    s.gnd("C7", "2")
    s.pw("U5", "2", (99.06, None))
    s.hlabel("SCAP_ADC", 99.06, 137.16, "L", "input")
    s.gnd("C11", "2")
    s.pw("U5", "4", (142.24, None))
    s.pw("U5", "3", (142.24, None), (None, 152.4))
    s.w((142.24, 152.4), (99.06, 152.4))
    s.hlabel("SENSE", 99.06, 152.4, "L", "input")
    s.gnd("C2", "2")
    s.w(s.pt("U5", "24"), s.pt("TP1", "1"))
    for num, net in [("22", "LNA_EN"), ("21", "LNA_PATH"), ("20", "SW_V1"), ("19", "SW_V2"),
                     ("18", "SW_V3"), ("17", "FMN"), ("12", "LED_SER"), ("11", "LED_SCK"),
                     ("10", "LED_RCK"), ("9", "LED_OE_N")]:
        end = s.pw("U5", num, (215.9, None))
        s.hlabel(net, *end, "R", "output")
    for num in ("16", "15", "14", "13"):
        s.nc("U5", num)
    s.intent("J1: 1=GND 2=C2D 3=C2CK 4=3V3", 86.36, 73.66)
    s.intent("Pinout follows placement: QSOP pins 1-12 (ADC, debug, lamp chain P2.x) face power/panel,",
             78.74, 184.15)
    s.intent("pins 13-24 (switch control, LNA_EN, FAULT_N) face the RF chain: no line crosses the MCU.",
             78.74, 187.33)
    s.intent("SYSCLK = HFOSC0/8 = 3.0625 MHz, Snooze when idle (no clock, no spurs). ILNA RC: fc = 1.6 kHz",
             78.74, 190.5)
    return s


# ---------------------------------------------------------------------------
# sheets 4/5: placeholders with the receiving side of the control signals


def sheet_rf(lib) -> DSheet:
    s = sheet(lib, "RF", "rf.kicad_sch", "RF chain - placeholder: control inputs only (next review)")
    s.zone = "RF"
    s.frame(25.4, 45.72, 111.76, 121.92, "Switch control inputs")
    for i, net in enumerate(["LNA_PATH", "SW_V1", "SW_V2", "SW_V3", "FMN"]):
        yy = 60.96 + i * 20.32
        s.add(R(f"R{15 + i}", "10k/5%", 76.2, yy + 3.81, net, "GND", Note="reset-safe default"))
        s.w((40.64, yy), s.pt(f"R{15 + i}", "1"))
        s.hlabel(net, 40.64, yy, "L", "input")
        s.gnd(f"R{15 + i}", "2")
    s.intent("Pull-downs at the receiving switches: bypass, F0 = THRU, FM notch out while the MCU is in reset.",
             27.94, 162.56)
    s.intent("NEXT REVIEW: antenna protection, LNA/bypass PE42520, preselector, FM notch, 2x PE42582, "
             "8 filters, broadband bias-T (drives VCOAX).", 27.94, 180.34)
    return s


def sheet_panel(lib) -> DSheet:
    s = sheet(lib, "Panel", "panel.kicad_sch", "48-lamp front panel - placeholder (next review)")
    s.zone = "DIGITAL"
    s.frame(25.4, 45.72, 111.76, 50.8, "Lamp chain inputs")
    for i, net in enumerate(["LED_SER", "LED_SCK", "LED_RCK"]):
        s.hlabel(net, 40.64, 60.96 + i * 5.08, "L", "input")
    s.add(R("R20", "10k/5%", 76.2, 73.66, "+3V3", "LED_OE_N", Note="lamps dark until first write"))
    s.w((40.64, 77.47), s.pt("R20", "2"))
    s.hlabel("LED_OE_N", 40.64, 77.47, "L", "input")
    s.rail_pin("R20", "1", "+3V3")
    s.intent("NEXT REVIEW: 6x 74HC595 (sinking), 48 lamps on VLED, 2.2k arrays.", 27.94, 91.44)
    return s


# ---------------------------------------------------------------------------
# sheet 1: block diagram + hierarchy


RFC, PWC, DGC, PNC, OFF = (205, 225, 255), (255, 228, 196), (210, 240, 210), (255, 245, 180), (232, 232, 232)
BLUE = (0, 0, 194)


def draw_block_diagram(r: DSheet):
    r.heading("Coaxolotl - block diagram", 20.32, 30.48)
    bw, bh = 26.0, 14.0
    xs = [18, 51, 84, 117, 150, 183, 216, 249, 282, 315, 348]
    ym, yu = 60.0, 38.0
    rf = [("ANTENNA", "20 kHz-2 GHz", OFF), ("PROTECTION", "GDT, limiter", RFC), ("SPDT", "PE42520", RFC),
          None, None, None, ("SPDT", "PE42520", RFC), ("SP8T", "PE42582", RFC),
          ("8 FILTERS", "F0 THRU..F7", RFC), ("SP8T", "PE42582", RFC), ("BIAS-T", "20 kHz-2 GHz", RFC)]
    for i, b in enumerate(rf):
        if b:
            r.block(xs[i], ym, bw, bh, b[0], [b[1]], b[2])
    for i, (t, l) in enumerate([("PRESELECTOR", "HPF 25M, LPF 2.2G"), ("FM NOTCH", "88-108 MHz, opt."),
                                ("LNA", "~100 mA")]):
        r.block(xs[3 + i], yu, bw, bh, t, [l], RFC)
    c = ym + bh / 2
    cu = yu + bh / 2
    for a, b in [(0, 1), (1, 2), (6, 7), (7, 8), (8, 9), (9, 10)]:
        r.arrow([(xs[a] + bw, c), (xs[b], c)])
    r.arrow([(xs[2] + bw / 2, ym), (xs[2] + bw / 2, cu), (xs[3], cu)])
    r.arrow([(xs[3] + bw, cu), (xs[4], cu)])
    r.arrow([(xs[4] + bw, cu), (xs[5], cu)])
    r.arrow([(xs[5] + bw, cu), (xs[6] + bw / 2, cu), (xs[6] + bw / 2, ym)])
    r.arrow([(xs[2] + bw, c), (xs[6], c)])
    r.ctext("bypass (F1-F4 force it: AUTO)", 128, c - 1.8, 1.15)
    r.arrow([(xs[10] + bw, c), (404, c)])
    r.ctext("COAX", 390, c - 4.5, 1.6, bold=True)
    r.ctext("RF -> RTL-SDR V3", 390, c + 3.0, 1.1)
    r.ctext("<- DC 4.5 V + OOK", 390, c + 5.5, 1.1)
    for i, tag in [(2, "LNA_PATH"), (6, "LNA_PATH"), (7, "SW_V1..3"), (9, "SW_V1..3")]:
        r.ctext(tag, xs[i] + bw / 2, ym + bh + 2.5, 1.1)
    r.ctext("FMN", xs[4] + bw / 2, yu - 1.5, 1.1)

    yp = 98.0
    r.block(348, yp, bw, bh, "VCOAX TAP", ["SENSE, 680R, PWR"], PWC)
    r.block(315, yp, bw, bh, "IDEAL DIODE", ["LM66100"], PWC)
    r.block(282, yp, bw, bh, "LAMP RAIL", ["FB + 47uF"], PWC)
    r.block(183, yp, bw, bh, "LNA SUPPLY", ["TPS2553+INA180"], PWC)
    r.block(150, yp, bw, bh, "HOLD-UP", ["330uF || 0.1F"], PWC)
    r.block(117, yp, bw, bh, "3V3 LDO", ["MCP1700"], PWC)
    r.arrow([(361, ym + bh), (361, yp)])
    r.ctext("VCOAX", 368, 86, 1.1)
    r.arrow([(348, yp + bh / 2), (341, yp + bh / 2)])
    yb = 91.0
    r.poly([(328, yp), (328, yb), (163, yb)], 0.5)
    r.ctext("VMAIN", 255, yb - 1.5, 1.2, bold=True)
    for x in (295, 189, 163):
        r.arrow([(x, yb), (x, yp)])
    r.arrow([(150, yp + bh / 2), (143, yp + bh / 2)])
    r.ctext("VHOLD", 146.5, yp + bh / 2 - 1.6, 1.0)
    r.arrow([(203, yp), (203, yu + bh)], color=(200, 90, 0))
    r.ctext("LNA_VCC", 210, 88, 1.1)

    yc = 128.0
    r.block(183, yc, 60, bh + 4, "MCU  EFM8BB21",
            ["decodes OOK on SENSE, Snooze when idle", "monitors SENSE, SCAP_ADC, ILNA, FAULT_N"], DGC)
    r.block(282, yc, bw + 8, bh + 4, "FRONT PANEL", ["6x 74HC595, 48 lamps"], PNC)
    r.arrow([(295, yp + bh), (295, yc)])
    r.ctext("VLED", 300, 120, 1.1)
    r.arrow([(243, yc + 9), (282, yc + 9)], dash=True, color=BLUE)
    r.ctext("LED bus", 262, yc + 7.2, 1.1)
    r.arrow([(130, yp + bh), (130, yc + 9), (183, yc + 9)])
    r.ctext("+3V3", 140, yc + 7.2, 1.1)
    yctl = 82.0
    r.poly([(240, yc), (240, yctl)], 0.3, dash=True, color=BLUE)
    r.poly([(97, yctl), (341, yctl)], 0.3, dash=True, color=BLUE)
    for x in (97, 229, 262, 328):
        r.arrow([(x, yctl), (x, ym + bh)], dash=True, color=BLUE)
    r.arrow([(163, yctl), (163, yu + bh)], dash=True, color=BLUE)
    r.arrow([(200, yctl), (200, yp)], dash=True, color=BLUE)
    r.ctext("control (blue, dashed): LNA_PATH, SW_V1..3, FMN, LNA_EN", 120, yctl + 2.4, 1.1)

    lx, ly = 18, 126
    for i, (col, t) in enumerate([(RFC, "RF signal path (sheet 4)"), (PWC, "power (sheet 2)"),
                                  (DGC, "MCU (sheet 3)"), (PNC, "panel (sheet 5)")]):
        r.block(lx, ly + i * 6, 6, 4, "", [], col)
        r.ctext(t, lx + 8, ly + i * 6 + 2.6, 1.2, left=True)


def all_sheets(lib):
    return [sheet_power(lib), sheet_mcu(lib), sheet_rf(lib), sheet_panel(lib)]


def main():
    sym_path = OUT / "coaxolotl.kicad_sym"
    build_library(sym_path)
    lib = g.Library(extra=[sym_path])

    sheets = all_sheets(lib)
    pages = {sh.file: str(i + 2) for i, sh in enumerate(sheets)}
    root = sheet(lib, "Coaxolotl", f"{PROJECT}.kicad_sch",
                 "Bias-T powered, OOK switched LNA + 8-way filter bank")
    draw_block_diagram(root)

    root.heading("Sheets", 20.32, 168.91)
    power, mcu, rf, panel = sheets
    left = ["SENSE", "SCAP_ADC", "ILNA", "FAULT_N", "LNA_EN"]
    to_rf = ["LNA_PATH", "SW_V1", "SW_V2", "SW_V3", "FMN"]
    to_panel = ["LED_SER", "LED_SCK", "LED_RCK", "LED_OE_N"]
    ys = [180.34 + 5.08 * i for i in range(5)]
    yl = [215.9 + 5.08 * i for i in range(4)]
    xp, xm, xr = 30.48, 139.7, 248.92
    w = 50.8
    power.sheet_pins = [(n, power.hier[n], xp + w, y, "R") for n, y in zip(left, ys)]
    mcu.sheet_pins = ([(n, mcu.hier[n], xm, y, "L") for n, y in zip(left, ys)] +
                      [(n, mcu.hier[n], xm + w, y, "R") for n, y in zip(to_rf, ys)] +
                      [(n, mcu.hier[n], xm + w, y, "R") for n, y in zip(to_panel, yl)])
    rf.sheet_pins = [(n, rf.hier[n], xr, y, "L") for n, y in zip(to_rf, ys)]
    panel.sheet_pins = [(n, panel.hier[n], xr, y, "L") for n, y in zip(to_panel, yl)]
    root.subsheet(power, xp, 175.26, w, 30.48)
    root.subsheet(mcu, xm, 175.26, w, 60.96)
    root.subsheet(rf, xr, 175.26, w, 27.94)
    root.subsheet(panel, xr, 210.82, w, 25.4)
    for y in ys:
        root.w((xp + w, y), (xm, y))
        root.w((xm + w, y), (xr, y))
    for y in yl:
        root.w((xm + w, y), (xr, y))
    root.intent("Power and ground cross sheets only as power symbols (VCOAX, VMAIN, VHOLD, VLED, LNA_VCC, "
                "+3V3, GND).", 20.32, 240.03)
    root.intent("Signals cross sheets as hierarchical labels through the sheet pins above. "
                "Firmware pin map: firmware/src/hal_efm8bb2.c", 20.32, 243.21)

    errs = []
    for sh in sheets + [root]:
        errs += [f"{sh.title}: {e}" for e in sh.finish()]

    root_path = "/" + root.uuid
    (OUT / f"{PROJECT}.kicad_sch").write_text(root.render(root.uuid, root_path, PROJECT, "1", pages))
    for sh in sheets:
        (OUT / sh.file).write_text(sh.render(root.uuid, f"{root_path}/{sh.sheet_uuid}", PROJECT,
                                             pages[sh.file], pages))
    (OUT / "sym-lib-table").write_text(
        '(sym_lib_table\n  (lib (name "coaxolotl")(type "KiCad")(uri "${KIPRJMOD}/coaxolotl.kicad_sym")'
        '(options "")(descr "Coaxolotl project symbols"))\n)\n')
    write_project(root)
    n = sum(len(sh.parts) for sh in sheets)
    print(f"wrote {len(sheets) + 1} sheets, {n} parts -> {OUT}")
    for e in errs:
        print("STYLE", e)
    return sheets, errs


def write_project(root):
    """Net classes. Width <= 1.25 x the smallest pad the net lands on (pre-route check):
    POWER lands on SC-70 (0.4 mm pads) -> 0.4 mm track. Clearances come from the fab .kicad_dru."""
    def cls(name, width):
        return {"name": name, "clearance": 0.2, "track_width": width, "via_diameter": 0.6, "via_drill": 0.3,
                "wire_width": 6, "bus_width": 12, "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)",
                "schematic_color": "rgba(0, 0, 0, 0.000)", "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
                "diff_pair_width": 0.2, "microvia_diameter": 0.3, "microvia_drill": 0.1}
    pro = {
        "meta": {"filename": f"{PROJECT}.kicad_pro", "version": 1},
        "sheets": [[root.uuid, "Root"]],
        "net_settings": {
            "classes": [cls("Default", 0.2), cls("POWER", 0.4)],
            "meta": {"version": 3},
            "netclass_patterns": [{"netclass": "POWER", "pattern": p} for p in
                                  RAILS + ["LNA_SW", "+3V3", "HU_FAST", "SC_CHG", "VSCAP"]],
        },
    }
    (OUT / f"{PROJECT}.kicad_pro").write_text(json.dumps(pro, indent=2) + "\n")


if __name__ == "__main__":
    main()
