#!/usr/bin/env python3
"""Generate the Coaxolotl KiCad project (hand-placed and wired).

    python3 hardware/kicad/tools/build.py
    python3 hardware/kicad/tools/check.py

Style (AI PCB Dev schematic guide):
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
    """num may be 'a,b,c': stacked pins at one point, only the first visible."""
    out = []
    for i, n in enumerate(str(num).split(",")):
        pin = [Sym("pin"), Sym(ptype if i == 0 else "passive"), Sym("line"), [Sym("at"), x, y, ang],
               [Sym("length"), 2.54]]
        if i:
            pin.append(Sym("hide"))
        pin += [[Sym("name"), name, _eff()], [Sym("number"), n, _eff()]]
        out.append(pin)
    return out


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
            pins.extend(_pin(pin_types.get(num, "bidirectional"), side * (half + 2.54), top - i * 2.54,
                             0 if side < 0 else 180, pname, num))
    for edge, lst in ((1, top_pins), (-1, bottom_pins)):
        k = len(lst)
        for i, (num, pname) in enumerate(lst):
            x = round(((i - (k - 1) / 2) * 5.08) / 1.27) * 1.27
            y = (top + 5.08) if edge > 0 else (ybot - 2.54)
            pins.extend(_pin(pin_types.get(num, "bidirectional"), x, y, 270 if edge > 0 else 90, pname, num))

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
        ic_symbol(
            "PE42582", "U", "Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm",
            "https://www.psemi.com/pdf/datasheets/pe42582ds.pdf",
            "SP8T absorptive 9 kHz-8 GHz, 3.3 V, RF ports at 0 VDC. LS=0: V3V2V1 000=RF1 001=RF5 010=RF3 "
            "011=RF7 100=RF2 101=RF6 110=RF4 111=RF8; V4=1 all off",
            left=[("22", "RFC"), None, ("9", "V1"), ("10", "V2"), ("11", "V3"), ("12", "V4"), ("1", "LS"),
                  ("7", "VSS_EXT")],
            right=[("24", "RF1"), ("2", "RF2"), ("4", "RF3"), ("6", "RF4"), ("13", "RF5"), ("15", "RF6"),
                   ("17", "RF7"), ("19", "RF8")],
            width=20.32, top_pins=[("8", "VDD")], bottom_pins=[("3,5,14,16,18,20,21,23,25", "GND")],
            pin_types={"8": "power_in", "3,5,14,16,18,20,21,23,25": "power_in", "9": "input", "10": "input",
                       "11": "input", "12": "input", "1": "input", "7": "passive"}),
        ic_symbol(
            "PE42522", "U", "coaxolotl:PE42522_LGA-29_4x4mm",
            "https://www.psemi.com/pdf/datasheets/pe42522ds.pdf",
            "SPDT absorptive 9 kHz-26.5 GHz, 3.3 V, RF ports at 0 VDC. V1=0: RFC-RF1, V1=1: RFC-RF2. "
            "VSS_EXT (29) tied to GND = internal negative generator",
            left=[("12", "RFC"), None, ("28", "V1")], right=[("22", "RF1"), None, ("2", "RF2")],
            width=20.32, top_pins=[("24", "VDD")],
            bottom_pins=[("1,3,4,5,6,7,8,9,10,11,13,14,15,16,17,18,19,20,21,23,25,26,27,29,30", "GND")],
            pin_types={"24": "power_in", "28": "input",
                       "1,3,4,5,6,7,8,9,10,11,13,14,15,16,17,18,19,20,21,23,25,26,27,29,30": "power_in"}),
        ic_symbol(
            "LTCC_LPF", "U", "coaxolotl:MiniCircuits_FV1206",
            "https://www.minicircuits.com/pdfs/LFCN-2250+.pdf",
            "Mini-Circuits LFCN LTCC low-pass filter (FV1206). VERIFY pinout: 1 IN, 3 OUT, 2/4 GND",
            left=[("1", "IN")], right=[("3", "OUT")], bottom_pins=[("2,4", "GND")],
            pin_types={"1": "passive", "3": "passive", "2,4": "power_in"}),
        ic_symbol(
            "QPL9547", "U", "coaxolotl:Qorvo_DFN-8-1EP_2x2mm_P0.5mm",
            "https://www.qorvo.com/products/p/QPL9547",
            "Qorvo 0.1-6 GHz ultra-low-noise LNA, 3.3-5 V, 65 mA (adjustable), DFN-8 2x2. "
            "VDD through RF_OUT; pins 3, 4, 5, 8 NC (no symbol pin)",
            left=[("2", "RF_IN"), None, None, None, ("6", "SHDN")], right=[("7", "RF_OUT")], width=20.32,
            top_pins=[("1", "VBIAS")], bottom_pins=[("9", "GND")],
            pin_types={"2": "input", "7": "output", "6": "input", "1": "input", "9": "power_in"}),
        ic_symbol(
            "RF_FILTER", "FL", "coaxolotl:FILTER_TBD",
            "~", "Placeholder for an LC filter network (designed in the filter review)",
            left=[("1", "IN")], right=[("2", "OUT")], bottom_pins=[("3", "GND")],
            pin_types={"1": "passive", "2": "passive", "3": "power_in"}),
    ]
    syms += [rail_symbol(base, r) for r in RAILS]
    lib = [Sym("kicad_symbol_lib"), [Sym("version"), 20220914], [Sym("generator"), Sym("coaxolotl_build")]]
    path.write_text(g.dump(lib + syms) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# part helpers (Value = critical parameters)


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


# ---------------------------------------------------------------------------
# sheet 4: RF FRONT END


def _sw_supply(s, ref, cap_ref, right=True, up=17.78):
    """VDD straight up to +3V3, 100 nF beside the riser (keeps the body edges free)."""
    x, y = s.pt(ref, s.vdd[ref])
    s.w((x, y), (x, y - up))
    s.rail("+3V3", x, y - up)
    cxc = x + (7.62 if right else -7.62)
    s.w((x, y - up + 2.54), (cxc, y - up + 2.54))
    s.add(C(cap_ref, "100nF/16V/10%", cxc, y - up + 6.35, "+3V3", "GND", Place=f"{ref}.{s.vdd[ref]}"))
    s.gnd(cap_ref, "2")


PE42522_GND = "1,3,4,5,6,7,8,9,10,11,13,14,15,16,17,18,19,20,21,23,25,26,27,29,30".split(",")


def _pe42522(s, ref, x, y, rfc, v1, rf1, rf2, **kw):
    s.add(Part(ref, "coaxolotl:PE42522", kw.pop("value", "PE42522B(LGA29)"), x, y, 0,
               "coaxolotl:PE42522_LGA-29_4x4mm",
               {"12": rfc, "28": v1, "22": rf1, "2": rf2, "24": "+3V3", **{n: "GND" for n in PE42522_GND}},
               **kw))
    s.vdd[ref] = "24"
    s.gnd(ref, "1")


def sheet_rf_fe(lib) -> DSheet:
    s = sheet(lib, "RF front end", "rf_frontend.kicad_sch",
              "Antenna protection, LNA/bypass select, preselector, FM notch option, LNA")
    s.vdd = {}
    s.zone = "RF"
    y0 = 77.47                       # bypass line (U6/U7 RF1 row)
    yl = 121.92                      # LNA chain
    yn = 160.02                      # FM notch switches (body centre)
    yf, yp = 200.66, 218.44          # control lines FMN, LNA_PATH

    # protection -------------------------------------------------------------
    s.frame(15.24, 45.72, 68.58, 58.42, "Antenna input + protection")
    s.add(Part("J2", "Connector:Conn_Coaxial", "SMA/EDGE(ANT)", 25.4, y0, 0,
               "Connector_Coaxial:SMA_Amphenol_132289_EdgeMount", {"1": "ANT", "2": "GND"},
               {"Place": "edge"}, mirror=True, txt=(25.4, 66.04, None)))
    s.add(Part("GDT1", "Device:GDT_2Pin", "GDT/90V", 40.64, 82.55, 0, "coaxolotl:GDT_SMD_VERIFY",
               {"3": "ANT", "1": "GND"}, {"MPN": "2027-09-SM-RPLF", "Place": "J2, shortest GND"},
               txt=(40.64, 96.52, None)))
    s.add(Part("D6", "Diode:BAV99", "BAV99W(SOT323)", 58.42, 82.55, 180, "Package_TO_SOT_SMD:SOT-323_SC-70",
               {"3": "ANT", "1": "GND", "2": "GND"}, {"Note": "clamp +-0.7 V, ~1.5 pF"}, txt=(58.42, 96.52, None)))
    s.add(R("R23", "100k/5%", 76.2, 81.28, "ANT", "GND", Note="static bleed (no choke: keeps 20 kHz)",
            txt=(76.2, 96.52, None)))
    for r, n in (("J2", "2"), ("GDT1", "1"), ("D6", "1"), ("D6", "2"), ("R23", "2")):
        s.gnd(r, n)
    s.intent("GDT 90 V, BAV99 low-C clamp, 100k bleed", 17.78, 102.87)

    # LNA / bypass in --------------------------------------------------------
    s.frame(88.9, 45.72, 27.94, 58.42, "LNA/BYP IN")
    _pe42522(s, "U6", 104.14, 80.01, "ANT", "LNA_PATH", "BYP_RF", "PRE_A", txt=(104.14, 97.79, None))
    _sw_supply(s, "U6", "C36")
    s.w(s.pt("J2", "1"), s.pt("U6", "12"))
    s.pw("U6", "28", (86.36, None), (None, yp))

    # preselector ------------------------------------------------------------
    s.frame(114.3, 109.22, 101.6, 30.48, "Preselector (LNA path only)")
    xc = [129.54, 154.94, 180.34]
    for ref, val, x, a, b in (("C24", "200pF/50V/C0G", xc[0], "PRE_A", "PRE_B"),
                              ("C25", "68pF/50V/C0G", xc[1], "PRE_B", "PRE_C"),
                              ("C26", "200pF/50V/C0G", xc[2], "PRE_C", "PRE_D")):
        s.add(C(ref, val, x, yl, a, b, rot=90, txt=(x, yl - 6.35, None)))
    for ref, x, n in (("L1", 142.24, "PRE_B"), ("L2", 167.64, "PRE_C")):
        s.add(Part(ref, "Device:L", "220nH/5%", x, yl + 5.08, 0, "Inductor_SMD:L_0603_1608Metric",
                   {"1": n, "2": "GND"}, txt=(x + 2.54, yl + 3.81)))
        s.w((x, yl), s.pt(ref, "1"))
        s.gnd(ref, "2")
    s.add(Part("U12", "coaxolotl:LTCC_LPF", "LFCN-2250+(FV1206)", 203.2, yl, 0, "coaxolotl:MiniCircuits_FV1206",
               {"1": "PRE_D", "3": "FMN_IN", "2": "GND", "4": "GND"}, txt=(203.2, yl - 7.62, None)))
    s.pw("U6", "2", (119.38, None), (None, yl), s.pt("C24", "1"))
    s.w(s.pt("C24", "2"), s.pt("C25", "1"))
    s.w(s.pt("C25", "2"), s.pt("C26", "1"))
    s.w(s.pt("C26", "2"), s.pt("U12", "1"))
    s.gnd("U12", "2")
    s.intent("HPF: 5th-order Butterworth T, fc ~25 MHz", 121.92, 113.03)
    s.intent("LPF: LTCC, fc ~2.2 GHz", 166.37, 113.03)

    # FM notch option --------------------------------------------------------
    s.frame(215.9, 144.78, 88.9, 43.18, "FM notch (DNP option)")
    s.add(R("R22", "0R/0603", 226.06, yl, "FMN_IN", "FMN_OUT", rot=90, Note="fit when the notch is not fitted",
            txt=(226.06, yl - 6.35, None)))
    s.w(s.pt("U12", "3"), s.pt("R22", "1"))
    _pe42522(s, "U9", 231.14, yn, "FMN_IN", "FMN", "FMN_THRU", "FMN_TANK_A", value="NA(PE42522B)",
             dnp=True, txt=(231.14, 177.8, None))
    _pe42522(s, "U10", 281.94, yn, "FMN_OUT", "FMN", "FMN_THRU", "FMN_TANK_B", value="NA(PE42522B)",
             dnp=True, mirror=True, txt=(281.94, 177.8, None))
    for ref in ("U9", "U10"):
        x, y = s.pt(ref, "24")
        s.w((x, y), (x, y - 2.54))
        s.rail("+3V3", x, y - 2.54)
    s.add(Part("L3", "Device:L", "NA(56nH/2%)", 256.54, yn + 2.54, 90, "Inductor_SMD:L_0603_1608Metric",
               {"1": "FMN_TANK_A", "2": "FMN_TANK_B"}, dnp=True, txt=(256.54, 175.26, None)))
    s.add(C("C27", "NA(47pF/C0G)", 256.54, yn + 10.16, "FMN_TANK_A", "FMN_TANK_B", rot=90, dnp=True,
            txt=(256.54, 180.34, None)))
    s.w((213.36, yl), (213.36, yn - 2.54), s.pt("U9", "12"))
    s.w(s.pt("U9", "22"), s.pt("U10", "22"))
    s.w(s.pt("U9", "2"), s.pt("L3", "1"), )
    s.w(s.pt("L3", "1"), s.pt("C27", "1"))
    s.w(s.pt("L3", "2"), s.pt("U10", "2"))
    s.w(s.pt("L3", "2"), s.pt("C27", "2"))
    s.pw("U10", "12", (299.72, None), (None, yl))
    s.pw("U9", "28", (215.9, None), (None, yf))
    s.pw("U10", "28", (297.18, None), (None, yf))
    s.intent("tank 56 nH || 47 pF: f0 = 98 MHz", 218.44, 186.69)

    # LNA --------------------------------------------------------------------
    s.frame(306.07, 81.28, 54.61, 71.12, "LNA")
    s.add(C("C28", "100nF/16V/X7R", 309.88, yl, "FMN_OUT", "LNA_RFIN", rot=90, txt=(309.88, yl - 6.35, None)))
    s.w(s.pt("R22", "2"), s.pt("C28", "1"))
    s.add(Part("U13", "coaxolotl:QPL9547", "QPL9547TR7(DFN8-2x2)", 327.66, yl + 5.08, 0,
               "coaxolotl:Qorvo_DFN-8-1EP_2x2mm_P0.5mm",
               {"2": "LNA_RFIN", "7": "LNA_RFOUT", "1": "LNA_VBIAS", "6": "LNA_SHDN", "9": "GND"},
               {"Note": "VERIFY Rbias / SHDN polarity / RF_OUT choke against the datasheet"},
               txt=(331.47, 139.7)))
    s.w(s.pt("C28", "2"), s.pt("U13", "2"))
    s.gnd("U13", "9")
    s.add(R("R25", "0R/0603", 322.58, 113.03, "LNA_BIAS", "LNA_VBIAS", Note="Rbias: sets IDD (65 mA default)",
            txt=(320.04, 111.76, "right")))
    s.pw("U13", "1", (None, 116.84), s.pt("R25", "2"))
    s.w(s.pt("R25", "1"), (322.58, 104.14), (340.36, 104.14))
    s.add(R("R26", "10k/5%", 312.42, 135.89, "LNA_SHDN", "GND", Note="SHDN low = on (VERIFY polarity)",
            txt=(309.88, 137.16, "right")))
    s.pw("U13", "6", (312.42, None), s.pt("R26", "1"))
    s.gnd("R26", "2")
    xb = 340.36
    s.add(Part("L4", "Device:L", "1uH/BROADBAND", xb, yl - 7.62, 0, "Inductor_SMD:L_0805_2012Metric",
               {"1": "LNA_BIAS", "2": "LNA_RFOUT"}, {"Note": "VERIFY SRF > 2 GHz or conical"},
               txt=(342.9, yl - 6.35)))
    s.add(Part("FB2", "Device:FerriteBead_Small", "FB0603/600R/1.3A", xb, yl - 27.94, 0, FB0603,
               {"1": "LNA_VCC", "2": "LNA_BIAS"}, txt=(xb - 2.54, yl - 29.21, "right")))
    s.add(C("C30", "10uF/10V/20%", xb + 7.62, yl - 19.05, "LNA_BIAS", "GND", fp=C0805))
    s.add(C("C31", "100nF/16V/X7R", 350.52, yl, "LNA_RFOUT", "LNA_RF_SW", rot=90, txt=(350.52, yl + 3.81, None)))
    s.w(s.pt("U13", "7"), s.pt("C31", "1"))
    s.w(s.pt("L4", "2"), (xb, yl))
    s.w(s.pt("L4", "1"), s.pt("FB2", "2"))
    s.w((xb, s.pt("C30", "1")[1] - 2.54), (xb + 7.62, s.pt("C30", "1")[1] - 2.54), s.pt("C30", "1"))
    s.gnd("C30", "2")
    s.w(s.pt("FB2", "1"), (xb, s.pt("FB2", "1")[1] - 2.54))
    s.rail("LNA_VCC", xb, s.pt("FB2", "1")[1] - 2.54)
    s.intent("VDD via RF_OUT choke, IDD 65 mA (QPL9547)", 308.61, 151.13)

    # LNA / bypass out -------------------------------------------------------
    s.frame(365.76, 45.72, 30.48, 58.42, "LNA/BYP OUT")
    _pe42522(s, "U7", 381.0, 80.01, "RF_BANK", "LNA_PATH", "BYP_RF", "LNA_RF_SW", mirror=True,
             txt=(381.0, 97.79, None))
    _sw_supply(s, "U7", "C37", right=False)
    s.w(s.pt("U6", "22"), s.pt("U7", "22"))
    s.pw("C31", "2", (363.22, None), (None, s.pt("U7", "2")[1]), s.pt("U7", "2"))
    s.pw("U7", "12", (401.32, None))
    s.hlabel("RF_BANK", 401.32, y0, "R", "bidirectional")
    s.pw("U7", "28", (398.78, None), (None, yp))
    s.ctext("bypass: F1-F4 (HF/LF) always, LNA off or tripped", 243.84, y0 - 1.27, 1.27)

    # control ----------------------------------------------------------------
    s.frame(15.24, 193.04, 388.62, 35.56, "Control inputs (reset-safe pull-downs)")
    s.add(R("R15", "10k/5%", 50.8, yp + 3.81, "LNA_PATH", "GND", Note="bypass while MCU in reset"))
    s.add(R("R19", "10k/5%", 203.2, yf + 3.81, "FMN", "GND", Note="notch out while MCU in reset"))
    s.w((35.56, yp), (398.78, yp))
    s.w((190.5, yf), (297.18, yf))
    s.hlabel("LNA_PATH", 35.56, yp, "L", "input")
    s.hlabel("FMN", 190.5, yf, "L", "input")
    s.gnd("R15", "2")
    s.gnd("R19", "2")
    s.intent("LNA_PATH: 0 = RF1 = bypass, 1 = RF2 = LNA.   FMN: 0 = RF1 = through, 1 = RF2 = notch.", 228.6, 206.38)
    s.intent("PE42522 RF ports sit at 0 VDC: no blocking caps except around the LNA.", 228.6, 209.55)
    return s


# ---------------------------------------------------------------------------
# sheet 5: FILTER BANK + BIAS-T

PE42582_GND = "3,5,14,16,18,20,21,23,25".split(",")
BANK = [("F0 THRU", None), ("F1 LPF 550 kHz", "LF"), ("F2 BPF 1.6-4 MHz", "160/80"),
        ("F3 BPF 4-12 MHz", "60-30"), ("F4 BPF 12-30 MHz", "20-10"), ("F5 BPF 30-88 MHz", "6/4m"),
        ("F6 BPF 108-174 MHz", "2m/AIR"), ("F7 HPF 380 MHz", "70/23cm")]


def sheet_bank(lib) -> DSheet:
    s = sheet(lib, "Filter bank", "filterbank.kicad_sch", "2x PE42582 SP8T, 8 filter branches, bias-T, coax")
    s.zone = "RF"
    rfp = ["24", "2", "4", "6", "13", "15", "17", "19"]          # RF1..RF8
    s.frame(38.1, 38.1, 182.88, 200.66, "Filter bank")
    for ref, x, mir, com in (("U8", 88.9, False, "RF_BANK"), ("U11", 190.5, True, "RF_COAX_SW")):
        nets = {"22": com, "9": "SW_V1", "10": "SW_V2", "11": "SW_V3", "12": "GND", "1": "GND", "7": "GND",
                "8": "+3V3", **{n: "GND" for n in PE42582_GND}}
        nets.update({p: f"{ref}_RF{k + 1}" for k, p in enumerate(rfp)})
        s.add(Part(ref, "coaxolotl:PE42582", "PE42582A(QFN24)", x, 76.2, 0,
                   "Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm", nets, mirror=mir,
                   txt=(x, 99.06, None)))
    # fan-out to the branches (lower pins turn earlier: no crossings)
    for k, p in enumerate(rfp):
        yr = 67.31 + 20.32 * k
        xa = 104.14 + (7 - k) * 2.54
        xb = 157.48 + k * 2.54
        a, b = f"U8_RF{k + 1}", f"U11_RF{k + 1}"
        name, _ = BANK[k]
        if k == 0:
            s.add(R("R21", "0R/0603", 139.7, yr, a, b, rot=90, Note="F0 = straight through",
                    txt=(139.7, yr - 5.08, None)))
            s.w(s.pt("U8", p), s.pt("R21", "1"))
            s.w(s.pt("R21", "2"), s.pt("U11", p))
        else:
            ref = f"FL{k}"
            s.add(Part(ref, "coaxolotl:RF_FILTER", name, 139.7, yr, 0, "coaxolotl:FILTER_TBD",
                       {"1": a, "2": b, "3": "GND"}, {"Note": "LC network: filter review"},
                       txt=(130.81, yr - 6.35)))
            s.pw("U8", p, (xa, None), (None, yr), s.pt(ref, "1"))
            s.pw(ref, "2", (xb, None), (None, s.pt("U11", p)[1]), s.pt("U11", p))
            s.gnd(ref, "3")
    for ref, sgn in (("U8", -1), ("U11", 1)):
        x = s.byref[ref].x
        side = x + sgn * 12.7
        for pin, net in (("9", "SW_V1"), ("10", "SW_V2"), ("11", "SW_V3")):
            end = s.pw(ref, pin, (side + sgn * (12.7 if sgn < 0 else 7.62), None))
            s.hlabel(net, *end, "L" if sgn < 0 else "R", "input")
        tie = side + sgn * 2.54
        s.pw(ref, "12", (tie, None))
        s.pw(ref, "1", (tie, None))
        s.pw(ref, "7", (tie, None))
        s.w((tie, s.pt(ref, "12")[1]), (tie, s.pt(ref, "7")[1] + 2.54))
        s.rail("GND", tie, s.pt(ref, "7")[1] + 2.54)
        s.gnd(ref, "3")
        s.w(s.pt(ref, "8"), (x, 45.72))
        s.rail("+3V3", x, 45.72)
        cx = x + (7.62 if sgn < 0 else -7.62)
        s.w((x, 48.26), (cx, 48.26))
        s.add(C(f"C{38 if ref == 'U8' else 39}", "100nF/16V/10%", cx, 52.07, "+3V3", "GND",
                Place=f"{ref}.8"))
        s.gnd(f"C{38 if ref == 'U8' else 39}", "2")
    s.pw("U8", "22", (63.5, None))
    s.hlabel("RF_BANK", 63.5, 67.31, "L", "bidirectional")
    s.intent("Branch k on RFk+1 of both switches. LS = 0 code (V3 V2 V1) per RF port: "
             "RF1 000, RF2 100, RF3 010, RF4 110, RF5 001, RF6 101, RF7 011, RF8 111", 40.64, 228.6)
    s.intent("bandplan.json 'port' -> code mapping is done in tools/gen_bandplan.py. "
             "V4 = LS = VSS_EXT = GND (all-off unused, internal negative generator).", 40.64, 231.77)
    s.intent("Reset: V = 000 -> RF1 = F0 THRU (straight through).", 40.64, 234.95)

    # bias-T + coax ---------------------------------------------------------
    s.frame(223.52, 25.4, 78.74, 81.28, "Bias-T + coax to RTL-SDR")
    xc, y1 = 231.14, 67.31
    s.add(C("C33", "2.2uF/16V/X7R", xc, y1, "RF_COAX_SW", "COAX", rot=90, fp=C0805, txt=(xc, y1 - 7.62, None)))
    s.add(C("C34", "1nF/50V/C0G", xc, y1 + 7.62, "RF_COAX_SW", "COAX", rot=90, txt=(xc, y1 + 20.32, None)))
    s.add(C("C35", "22pF/50V/C0G", xc, y1 + 15.24, "RF_COAX_SW", "COAX", rot=90, txt=(xc, y1 + 25.4, None)))
    s.add(Part("L5", "Device:L", "100nH/0603", 254.0, 63.5, 0, "Inductor_SMD:L_0603_1608Metric",
               {"1": "BT_1", "2": "COAX"}, txt=(251.46, 62.23, "right")))
    s.add(Part("L6", "Device:L", "10uH/0805", 254.0, 52.07, 0, "Inductor_SMD:L_0805_2012Metric",
               {"1": "BT_2", "2": "BT_1"}, txt=(251.46, 50.8, "right")))
    s.add(R("R24", "220R/5%", 261.62, 52.07, "BT_2", "BT_1", Note="damps the L6 self-resonance"))
    s.add(Part("L7", "Device:L", "1mH/0.2A", 254.0, 40.64, 0, "Inductor_SMD:L_Bourns_SRR1260",
               {"1": "VCOAX", "2": "BT_2"}, {"MPN": "SRR1260-102M"}, txt=(251.46, 39.37, "right")))
    s.add(Part("J3", "Connector:Conn_Coaxial", "SMA/EDGE(RTL-SDR)", 279.4, y1, 0,
               "Connector_Coaxial:SMA_Amphenol_132289_EdgeMount", {"1": "COAX", "2": "GND"},
               {"Place": "edge"}, txt=(281.94, 58.42)))
    s.pw("U11", "22", s.pt("C33", "1"))
    s.w(s.pt("C33", "2"), s.pt("J3", "1"))
    s.w(s.pt("C33", "1"), s.pt("C34", "1"), s.pt("C35", "1"))
    s.w(s.pt("C33", "2"), s.pt("C34", "2"), s.pt("C35", "2"))
    s.w(s.pt("L5", "2"), (254.0, y1))
    s.w(s.pt("L5", "1"), s.pt("L6", "2"))
    s.w(s.pt("L6", "1"), s.pt("L7", "2"))
    s.w((254.0, 55.88), (261.62, 55.88))
    s.w((254.0, 48.26), (261.62, 48.26))
    s.w(s.pt("L7", "1"), (254.0, 34.29))
    s.rail("VCOAX", 254.0, 34.29)
    s.gnd("J3", "2")
    s.intent("DC block 2.2 uF || 1 nF || 22 pF; DC feed 100 nH -> 10 uH || 220R -> 1 mH:", 226.06, 100.33)
    s.intent("20 kHz-2 GHz. VERIFY S21/S11 with a VNA. D1 (TVS) sits at J3 (power sheet).", 226.06, 103.51)

    # control inputs with pull-downs --------------------------------------------
    s.frame(226.06, 119.38, 76.2, 63.5, "Control inputs")
    for i, net in enumerate(["SW_V1", "SW_V2", "SW_V3"]):
        yy = 134.62 + i * 15.24
        s.add(R(f"R{16 + i}", "10k/5%", 266.7, yy + 3.81, net, "GND", Note="F0 THRU while MCU in reset"))
        s.w((243.84, yy), s.pt(f"R{16 + i}", "1"))
        s.hlabel(net, 243.84, yy, "L", "input")
        s.gnd(f"R{16 + i}", "2")
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
    rf = [("ANTENNA", "20 kHz-2 GHz", OFF), ("PROTECTION", "GDT, limiter", RFC), ("SPDT", "PE42522", RFC),
          None, None, None, ("SPDT", "PE42522", RFC), ("SP8T", "PE42582", RFC),
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
    for i, (col, t) in enumerate([(RFC, "RF signal path (sheets 4, 5)"), (PWC, "power (sheet 2)"),
                                  (DGC, "MCU (sheet 3)"), (PNC, "panel (sheet 6)")]):
        r.block(lx, ly + i * 6, 6, 4, "", [], col)
        r.ctext(t, lx + 8, ly + i * 6 + 2.6, 1.2, left=True)


def all_sheets(lib):
    return [sheet_power(lib), sheet_mcu(lib), sheet_rf_fe(lib), sheet_bank(lib), sheet_panel(lib)]


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
    power, mcu, fe, bank, panel = sheets
    left = ["SENSE", "SCAP_ADC", "ILNA", "FAULT_N", "LNA_EN"]
    to_fe = ["LNA_PATH", "FMN"]
    to_bank = ["SW_V1", "SW_V2", "SW_V3"]
    to_panel = ["LED_SER", "LED_SCK", "LED_RCK", "LED_OE_N"]
    ys = [180.34 + 5.08 * i for i in range(5)]
    yf = [180.34, 185.42]
    yb = [200.66 + 5.08 * i for i in range(3)]
    yl = [226.06 + 5.08 * i for i in range(4)]
    xp, xm, xr = 30.48, 139.7, 248.92
    w = 50.8
    power.sheet_pins = [(n, power.hier[n], xp + w, y, "R") for n, y in zip(left, ys)]
    mcu.sheet_pins = ([(n, mcu.hier[n], xm, y, "L") for n, y in zip(left, ys)] +
                      [(n, mcu.hier[n], xm + w, y, "R") for n, y in zip(to_fe, yf)] +
                      [(n, mcu.hier[n], xm + w, y, "R") for n, y in zip(to_bank, yb)] +
                      [(n, mcu.hier[n], xm + w, y, "R") for n, y in zip(to_panel, yl)])
    fe.sheet_pins = ([(n, fe.hier[n], xr, y, "L") for n, y in zip(to_fe, yf)] +
                     [("RF_BANK", fe.hier["RF_BANK"], xr + w, 182.88, "R")])
    bank.sheet_pins = ([(n, bank.hier[n], xr, y, "L") for n, y in zip(to_bank, yb)] +
                       [("RF_BANK", bank.hier["RF_BANK"], xr + w, 205.74, "R")])
    panel.sheet_pins = [(n, panel.hier[n], xr, y, "L") for n, y in zip(to_panel, yl)]
    root.subsheet(power, xp, 175.26, w, 30.48)
    root.subsheet(mcu, xm, 175.26, w, 71.12)
    root.subsheet(fe, xr, 175.26, w, 12.7)
    root.subsheet(bank, xr, 195.58, w, 17.78)
    root.subsheet(panel, xr, 220.98, w, 22.86)
    for y in ys:
        root.w((xp + w, y), (xm, y))
    for y in yf + yb + yl:
        root.w((xm + w, y), (xr, y))
    root.w((xr + w, 182.88), (xr + w + 10.16, 182.88), (xr + w + 10.16, 205.74), (xr + w, 205.74))
    root.intent("Power and ground cross sheets only as power symbols (VCOAX, VMAIN, VHOLD, VLED, LNA_VCC, "
                "+3V3, GND).", 20.32, 251.46)
    root.intent("Signals cross sheets as hierarchical labels through the sheet pins above. "
                "Firmware pin map: firmware/src/hal_efm8bb2.c", 20.32, 254.64)

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
