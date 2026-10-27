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
import filters
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
            "PE42520", "U", "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm",
            "https://www.lcsc.com/datasheet/C500478.pdf",
            "SPDT absorptive 9 kHz-13 GHz, 2.3-5.5 V, RF ports at 0 VDC. LS=1: CTRL=0 RFC-RF1, CTRL=1 RFC-RF2 "
            "(datasheet table 5). VSS_EXT (13) to GND = internal negative generator",
            left=[("7", "RFC"), None, ("14", "CTRL")], right=[("2", "RF1"), None, ("11", "RF2")],
            width=20.32, top_pins=[("16", "VDD"), ("15", "LS")],
            bottom_pins=[("1,3,4,5,6,8,9,10,12,13,17", "GND")],
            pin_types={"16": "power_in", "14": "input", "15": "input",
                       "1,3,4,5,6,8,9,10,12,13,17": "power_in"}),
        ic_symbol(
            "PE42520R", "U", "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm",
            "https://www.lcsc.com/datasheet/C500478.pdf",
            "PE42520 drawn for LS = GND: CTRL=0 RFC-RF2, CTRL=1 RFC-RF1 (datasheet table 5). Same part and "
            "pins as PE42520; used where the board needs the RF ports swapped (one-layer ring, no crossing)",
            left=[("7", "RFC"), None, ("14", "CTRL")], right=[("11", "RF2"), None, ("2", "RF1")],
            width=20.32, top_pins=[("16", "VDD")],
            bottom_pins=[("1,3,4,5,6,8,9,10,12,13,17", "GND"), ("15", "LS")],
            pin_types={"16": "power_in", "14": "input", "15": "input",
                       "1,3,4,5,6,8,9,10,12,13,17": "power_in"}),
        ic_symbol(
            "LTCC_LPF", "U", "coaxolotl:MiniCircuits_FV1206",
            "https://www.minicircuits.com/pdfs/LFCN-2250+.pdf",
            "Mini-Circuits LFCN LTCC low-pass filter (FV1206). VERIFY pinout: 1 IN, 3 OUT, 2/4 GND",
            left=[("1", "IN")], right=[("3", "OUT")], bottom_pins=[("2,4", "GND")],
            pin_types={"1": "passive", "3": "passive", "2,4": "power_in"}),
        ic_symbol(
            "QPL9547", "U", "Package_DFN_QFN:DFN-8-1EP_2x2mm_P0.5mm_EP0.8x1.6mm",
            "https://www.lcsc.com/datasheet/C5367093.pdf",
            "Qorvo 0.1-6 GHz ultra-low-noise LNA, 3.3-5 V, DFN-8 2x2. VDD through RF_OUT; "
            "NC pins 4, 5, 8 grounded (stacked on GND, as on SWL434), pin 3 open",
            left=[("2", "RF_IN"), None, None, None, ("6", "SHDN")], right=[("7", "RF_OUT")], width=20.32,
            top_pins=[("1", "VBIAS")], bottom_pins=[("9,4,5,8", "GND")],
            pin_types={"2": "input", "7": "output", "6": "input", "1": "input", "9,4,5,8": "power_in"}),
        ic_symbol(
            "74HC595", "U", "Package_SO:TSSOP-16_4.4x5mm_P0.65mm",
            "https://www.ti.com/lit/ds/symlink/sn74hc595.pdf",
            "8-bit shift register with output latches (pin order arranged for this design: ~SRCLR on top)",
            left=[("14", "SER"), None, ("11", "SRCLK"), ("12", "RCLK"), ("13", "~{OE}")],
            right=[("15", "QA"), ("1", "QB"), ("2", "QC"), ("3", "QD"), ("4", "QE"), ("5", "QF"),
                   ("6", "QG"), ("7", "QH"), None, ("9", "QH'")],
            top_pins=[("16", "VCC"), ("10", "~{SRCLR}")], bottom_pins=[("8", "GND")],
            pin_types={"14": "input", "11": "input", "12": "input", "13": "input", "10": "input",
                       "16": "power_in", "8": "power_in", "9": "output",
                       **{n: "tri_state" for n in ("15", "1", "2", "3", "4", "5", "6", "7")}}),
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
    if s.byref[ref].lib_id.endswith("PE42520"):
        _ls_tie(s, ref, y - up + 12.7)


PE42520_GND = "1,3,4,5,6,8,9,10,12,13,17".split(",")


def _pe42522(s, ref, x, y, rfc, v1, rf1, rf2, inv=False, **kw):
    """PE42520 SPDT. rf1/rf2 = net on the upper/lower RF row of the symbol, which is the port selected by
    CTRL = 0/1. inv=False: LS = VDD, upper row = RF1. inv=True: LS = GND, upper row = RF2 (PE42520R)."""
    ports = {"11": rf1, "2": rf2} if inv else {"2": rf1, "11": rf2}
    s.add(Part(ref, "coaxolotl:PE42520R" if inv else "coaxolotl:PE42520", kw.pop("value", "PE42520C-Z(QFN16)"),
               x, y, 0, "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm",
               {"7": rfc, "14": v1, **ports, "16": "+3V3", "15": "GND" if inv else "+3V3",
                **{n: "GND" for n in PE42520_GND}}, **kw))
    s.vdd[ref] = "16"
    s.gnd(ref, "1")
    if inv:
        s.gnd(ref, "15")


def _ls_tie(s, ref, ytop):
    """LS (top pin) joins the VDD riser at ytop"""
    lx, ly = s.pt(ref, "15")
    vx, _ = s.pt(ref, "16")
    s.w((lx, ly), (lx, ytop), (vx, ytop))


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
    s.add(Part("GDT1", "Device:GDT_2Pin", "4532-090V(1812)", 40.64, 82.55, 0, "coaxolotl:GDT_SMD_1812",
               {"3": "ANT", "1": "GND"}, {"MPN": "4532-090V", "LCSC": "C476204", "Note": "90 V, 0.5 pF", "Place": "J2, shortest GND"},
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
    s.w(s.pt("J2", "1"), s.pt("U6", "7"))
    s.pw("U6", "14", (86.36, None), (None, yp))

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
    s.pw("U6", "11", (119.38, None), (None, yl), s.pt("C24", "1"))
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
    _pe42522(s, "U9", 231.14, yn, "FMN_IN", "FMN", "FMN_THRU", "FMN_TANK_A", value="NA(PE42520C-Z)",
             dnp=True, txt=(231.14, 177.8, None))
    _pe42522(s, "U10", 281.94, yn, "FMN_OUT", "FMN", "FMN_THRU", "FMN_TANK_B", inv=True, value="NA(PE42520C-Z)",
             dnp=True, mirror=True, txt=(281.94, 177.8, None))
    for ref in ("U9", "U10"):
        x, y = s.pt(ref, "16")
        s.w((x, y), (x, y - 5.08))
        s.rail("+3V3", x, y - 5.08)
        if s.byref[ref].lib_id.endswith("PE42520"):
            _ls_tie(s, ref, y - 2.54)
    s.add(Part("L3", "Device:L", "NA(56nH/2%)", 256.54, yn + 2.54, 90, "Inductor_SMD:L_0603_1608Metric",
               {"1": "FMN_TANK_A", "2": "FMN_TANK_B"}, dnp=True, txt=(256.54, 175.26, None)))
    s.add(C("C27", "NA(47pF/C0G)", 256.54, yn + 10.16, "FMN_TANK_A", "FMN_TANK_B", rot=90, dnp=True,
            txt=(256.54, 180.34, None)))
    s.w((213.36, yl), (213.36, yn - 2.54), s.pt("U9", "7"))
    s.w(s.pt("U9", "2"), s.pt("U10", "11"))
    s.w(s.pt("U9", "11"), s.pt("L3", "1"), )
    s.w(s.pt("L3", "1"), s.pt("C27", "1"))
    s.w(s.pt("L3", "2"), s.pt("U10", "2"))
    s.w(s.pt("L3", "2"), s.pt("C27", "2"))
    s.pw("U10", "7", (299.72, None), (None, yl))
    s.pw("U9", "14", (215.9, None), (None, yf))
    s.pw("U10", "14", (297.18, None), (None, yf))
    s.intent("tank 56 nH || 47 pF: f0 = 98 MHz", 218.44, 186.69)

    # LNA (one stage of the SWL434 / Spacewalker LNA) -------------------------
    s.frame(279.4, 81.28, 81.28, 62.23, "LNA")
    s.add(C("C28", "1nF/50V/C0G", 309.88, yl, "FMN_OUT", "LNA_RFIN", rot=90, txt=(309.88, yl + 3.81, None)))
    s.w(s.pt("R22", "2"), s.pt("C28", "1"))
    s.add(Part("U13", "coaxolotl:QPL9547", "QPL9547TR7(DFN8-2x2)", 327.66, yl + 5.08, 0,
               "Package_DFN_QFN:DFN-8-1EP_2x2mm_P0.5mm_EP0.8x1.6mm",
               {"2": "LNA_RFIN", "7": "LNA_RFOUT", "1": "LNA_VBIAS", "6": "GND",
                **{n: "GND" for n in ("9", "4", "5", "8")}},
               {"LCSC": "C5367093", "Note": "EP: 0.25 mm vias under the paddle (datasheet p.8)"},
               txt=(339.09, 132.08)))
    s.w(s.pt("C28", "2"), s.pt("U13", "2"))
    s.gnd("U13", "9")
    s.gnd("U13", "6")
    # supply row (y = yr): LNA_VCC -> FB2 -> 100 pF -> L4 560 nH -> L8 18 nH -> RF_OUT
    yr, xb = 96.52, 340.36
    s.w((299.72, yr), (299.72, 91.44))
    s.rail("LNA_VCC", 299.72, 91.44)
    s.add(C("C40", "100pF/C0G", 299.72, yr + 3.81, "LNA_VCC", "GND", txt=(297.18, yr + 3.81, "right")))
    s.gnd("C40", "2")
    s.add(C("C32", "100pF/C0G", 307.34, yr + 3.81, "LNA_VCC", "GND", txt=(307.34, yr + 16.51, None)))
    s.gnd("C32", "2")
    s.add(R("R25", "3k32/1%", 314.96, yr + 3.81, "LNA_VCC", "LNA_VBIAS", Note="sets IDD = 65 mA (QPL9547 EVB R4)",
            txt=(317.5, yr + 5.08)))
    s.add(Part("FB2", "Device:FerriteBead_Small", "FB0603/600R/1.3A", 325.12, yr, 90, FB0603,
               {"1": "LNA_VCC", "2": "LNA_BIAS"}, txt=(325.12, 87.63, None)))
    s.add(C("C30", "100pF/C0G", 330.2, yr + 3.81, "LNA_BIAS", "GND", txt=(330.2, yr + 16.51, None)))
    s.gnd("C30", "2")
    s.add(Part("L4", "Device:L", "560nH/5%", 335.28, yr, 90, "Inductor_SMD:L_0603_1608Metric",
               {"1": "LNA_BIAS", "2": "LNA_BIAS2"}, {"Note": "low-band choke (SWL434)"},
               txt=(335.28, 90.17, None)))
    s.add(Part("L8", "Device:L", "18nH/2%", xb, yl - 3.81, 0, "Inductor_SMD:L_0603_1608Metric",
               {"1": "LNA_BIAS2", "2": "LNA_RFOUT"}, {"Note": "high-band choke at the pin (QPL9547 EVB L1)"},
               txt=(342.9, yl - 6.35)))
    p_fb1, p_fb2 = s.pt("FB2", "1"), s.pt("FB2", "2")
    if p_fb1[0] > p_fb2[0]:
        p_fb1, p_fb2 = p_fb2, p_fb1
    s.w((299.72, yr), p_fb1)
    s.w(p_fb2, s.pt("L4", "1"))
    s.w(s.pt("L4", "2"), (xb, yr), s.pt("L8", "1"))
    s.w(s.pt("L8", "2"), (xb, yl))
    s.w(s.pt("R25", "2"), (314.96, 116.84), s.pt("U13", "1"))
    s.add(C("C31", "1nF/50V/C0G", 350.52, yl, "LNA_RFOUT", "LNA_RF_SW", rot=90, txt=(350.52, yl + 3.81, None)))
    s.w(s.pt("U13", "7"), s.pt("C31", "1"))
    for i, t in enumerate(["SWL434 stage 1", "+ 18 nH (EVB L1)", "1 nF DC blocks"]):
        s.intent(t, 281.94, 131.45 + 3.18 * i)
    # LNA / bypass out -------------------------------------------------------
    s.frame(365.76, 45.72, 30.48, 58.42, "LNA/BYP OUT")
    _pe42522(s, "U7", 381.0, 80.01, "RF_BANK", "LNA_PATH", "BYP_RF", "LNA_RF_SW", inv=True, mirror=True,
             txt=(381.0, 97.79, None))
    _sw_supply(s, "U7", "C37", right=False)
    s.w(s.pt("U6", "2"), s.pt("U7", "11"))
    s.pw("C31", "2", (363.22, None), (None, s.pt("U7", "2")[1]), s.pt("U7", "2"))
    s.pw("U7", "7", (401.32, None))
    s.hlabel("RF_BANK", 401.32, y0, "R", "bidirectional")
    s.pw("U7", "14", (398.78, None), (None, yp))
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
    s.intent("LNA_PATH 0 = bypass, 1 = LNA; FMN 0 = through. U7/U10 have LS = GND (RF ports swapped: one-layer ring).", 228.6, 206.38)
    s.intent("PE42520 RF ports sit at 0 VDC: no blocking caps except around the LNA.", 228.6, 209.55)
    return s


# ---------------------------------------------------------------------------
# sheet 5: FILTER BANK + BIAS-T

PE42582_GND = "3,5,14,16,18,20,21,23,25".split(",")
FLT_PITCH = 15.24      # one ladder element per slot
ROW0, ROW_PITCH = 60.96, 25.4


def _ladder(s, k, x0, y, a, b):
    """branch k LC ladder from (x0, y) on net a to net b; returns the end x."""
    els = filters.design(k)
    nseries = sum(1 for e in els if e[0] == "S")
    x, node, n_c, n_l, si = x0, a, 0, 0, 0
    for i, (topo, kind, _, val) in enumerate(els):
        if kind == "C":
            n_c += 1
            ref = f"C{k}{n_c:02d}"
        else:
            n_l += 1
            ref = f"L{k}{n_l:02d}"
        value, fp = filters.part_value(kind, val)
        lib = "Device:C" if kind == "C" else "Device:L"
        cx = x + FLT_PITCH / 2
        if topo == "S":
            si += 1
            nxt = b if si == nseries else f"F{k}_N{si}"
            s.add(Part(ref, lib, value, cx, y, 90, fp, {"1": node, "2": nxt}, txt=(cx, y - 6.35, None)))
            s.w((x, y), s.pt(ref, "1"))
            s.w(s.pt(ref, "2"), (x + FLT_PITCH, y))
            node = nxt
        else:
            s.add(Part(ref, lib, value, cx, y + 3.81, 0, fp, {"1": node, "2": "GND"},
                       txt=(cx + 2.54, y + 3.81)))
            s.w((x, y), (x + FLT_PITCH, y))
            s.gnd(ref, "2")
        x += FLT_PITCH
    return x


def sheet_bank(lib) -> DSheet:
    s = sheet(lib, "Filter bank", "filterbank.kicad_sch", "2x PE42582 SP8T, 8 filter branches, bias-T, coax")
    s.zone = "RF"
    rfp = ["24", "2", "4", "6", "13", "15", "17", "19"]          # RF1..RF8
    xu8, xu11 = 40.64, 289.56
    yu = ROW0 + 8.89
    s.frame(12.7, 30.48, 299.72, 222.25, "Filter bank")
    for ref, x, mir, com in (("U8", xu8, False, "RF_BANK"), ("U11", xu11, True, "RF_COAX_SW")):
        nets = {"22": com, "9": "SW_V1", "10": "SW_V2", "11": "SW_V3", "12": "GND", "1": "GND", "7": "GND",
                "8": "+3V3", **{n: "GND" for n in PE42582_GND}}
        nets.update({p: f"{ref}_RF{k + 1}" for k, p in enumerate(rfp)})
        s.add(Part(ref, "coaxolotl:PE42582", "PE42582A(QFN24)", x, yu, 0,
                   "Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm", nets, mirror=mir,
                   txt=(x, yu + 22.86, None)))
    xs, xe = 76.2, 76.2 + 10 * FLT_PITCH
    for k, p in enumerate(rfp):
        yr = ROW0 + ROW_PITCH * k
        xa = 55.88 + (7 - k) * 2.54
        xb = xe + 2.54 + k * 2.54
        a, b = f"U8_RF{k + 1}", f"U11_RF{k + 1}"
        if k == 0:
            s.add(R("R21", "0R/0603", xs + FLT_PITCH / 2, yr, a, b, rot=90, Note="F0 = straight through",
                    txt=(xs + FLT_PITCH / 2, yr - 6.35, None)))
            s.w(s.pt("U8", p), s.pt("R21", "1"))
            s.w(s.pt("R21", "2"), s.pt("U11", p))
            continue
        s.pw("U8", p, (xa, None), (None, yr), (xs, yr))
        end = _ladder(s, k, xs, yr, a, b)
        s.w((end, yr), (xb, yr), (xb, s.pt("U11", p)[1]), s.pt("U11", p))
        s.ctext(filters.BRANCHES[k][0], xs - 2.54, yr - 15.24, 1.27, left=True)
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
        top = s.pt(ref, "8")[1]
        s.w(s.pt(ref, "8"), (x, top - 15.24))
        s.rail("+3V3", x, top - 15.24)
        cx = x + (7.62 if sgn < 0 else -7.62)
        s.w((x, top - 12.7), (cx, top - 12.7))
        s.add(C(f"C{38 if ref == 'U8' else 39}", "100nF/16V/10%", cx, top - 8.89, "+3V3", "GND",
                Place=f"{ref}.8", txt=(cx + (2.54 if sgn < 0 else -2.54), top - 10.16, "left" if sgn < 0 else "right")))
        s.gnd(f"C{38 if ref == 'U8' else 39}", "2")
    s.pw("U8", "22", (22.86, None))
    s.hlabel("RF_BANK", 22.86, ROW0, "L", "bidirectional")
    for i, t in enumerate(["Branch k on RFk+1 of both switches. LS = 0 code (V3 V2 V1):",
                           "RF1 000, RF2 100, RF3 010, RF4 110, RF5 001, RF6 101, RF7 011, RF8 111",
                           "(mapped in tools/gen_bandplan.py). V4 = LS = VSS_EXT = GND.",
                           "Filters: 5th-order Chebyshev 0.1 dB, 50 ohm, E12 values,",
                           "BPF = HPF + LPF (tools/filters.py). Ideal-part response there;",
                           "VERIFY with real L Q / SRF and the layout on a VNA."]):
        s.intent(t, 325.12, 200.66 + 3.18 * i)

    # bias-T + coax ---------------------------------------------------------
    X = 322.58
    s.frame(X, 25.4, 78.74, 81.28, "Bias-T + coax to RTL-SDR")
    xc, y1 = X + 7.62, ROW0
    xl = X + 30.48
    s.add(C("C33", "2.2uF/16V/X7R", xc, y1, "RF_COAX_SW", "COAX", rot=90, fp=C0805, txt=(xc, y1 - 7.62, None)))
    s.add(C("C34", "1nF/50V/C0G", xc, y1 + 7.62, "RF_COAX_SW", "COAX", rot=90, txt=(xc, y1 + 20.32, None)))
    s.add(C("C35", "22pF/50V/C0G", xc, y1 + 15.24, "RF_COAX_SW", "COAX", rot=90, txt=(xc, y1 + 25.4, None)))
    s.add(Part("L5", "Device:L", "100nH/0603", xl, y1 - 3.81, 0, "Inductor_SMD:L_0603_1608Metric",
               {"1": "BT_1", "2": "COAX"}, txt=(xl - 2.54, y1 - 5.08, "right")))
    s.add(Part("L6", "Device:L", "10uH/0805", xl, y1 - 15.24, 0, "Inductor_SMD:L_0805_2012Metric",
               {"1": "BT_2", "2": "BT_1"}, txt=(xl - 2.54, y1 - 16.51, "right")))
    s.add(R("R24", "220R/5%", xl + 7.62, y1 - 15.24, "BT_2", "BT_1", Note="damps the L6 self-resonance"))
    s.add(Part("L7", "Device:L", "1mH/0.2A", xl, y1 - 26.67, 0, "Inductor_SMD:L_Bourns_SRR1260",
               {"1": "VCOAX", "2": "BT_2"}, {"MPN": "SRR1260-102M"}, txt=(xl - 2.54, y1 - 27.94, "right")))
    s.add(Part("J3", "Connector:Conn_Coaxial", "SMA/EDGE(RTL-SDR)", X + 55.88, y1, 0,
               "Connector_Coaxial:SMA_Amphenol_132289_EdgeMount", {"1": "COAX", "2": "GND"},
               {"Place": "edge"}, txt=(X + 58.42, y1 - 8.89)))
    s.pw("U11", "22", s.pt("C33", "1"))
    s.w(s.pt("C33", "2"), s.pt("J3", "1"))
    s.w(s.pt("C33", "1"), s.pt("C34", "1"), s.pt("C35", "1"))
    s.w(s.pt("C33", "2"), s.pt("C34", "2"), s.pt("C35", "2"))
    s.w(s.pt("L5", "2"), (xl, y1))
    s.w(s.pt("L5", "1"), s.pt("L6", "2"))
    s.w(s.pt("L6", "1"), s.pt("L7", "2"))
    s.w((xl, s.pt("L6", "2")[1]), (xl + 7.62, s.pt("L6", "2")[1]))
    s.w((xl, s.pt("L6", "1")[1]), (xl + 7.62, s.pt("L6", "1")[1]))
    s.w(s.pt("L7", "1"), (xl, s.pt("L7", "1")[1] - 2.54))
    s.rail("VCOAX", xl, s.pt("L7", "1")[1] - 2.54)
    s.gnd("J3", "2")
    s.intent("DC block 2.2 uF || 1 nF || 22 pF; DC feed 100 nH -> 10 uH || 220R -> 1 mH:", X + 2.54, 100.33)
    s.intent("20 kHz-2 GHz. VERIFY S21/S11 with a VNA. D1 (TVS) sits at J3 (power sheet).", X + 2.54, 103.51)

    # control inputs with pull-downs --------------------------------------------
    s.frame(X, 119.38, 78.74, 63.5, "Control inputs")
    for i, net in enumerate(["SW_V1", "SW_V2", "SW_V3"]):
        yy = 134.62 + i * 15.24
        s.add(R(f"R{16 + i}", "10k/5%", X + 40.64, yy + 3.81, net, "GND", Note="F0 THRU while MCU in reset"))
        s.w((X + 17.78, yy), s.pt(f"R{16 + i}", "1"))
        s.hlabel(net, X + 17.78, yy, "L", "input")
        s.gnd(f"R{16 + i}", "2")
    return s


LAMP_COL = {"A": "AMBER(0603)", "Y": "YELLOW(0603)", "G": "GREEN(0603)", "R": "RED(0603)"}
# lamp n: (name, colour) - order fixed by firmware/src/panel.h (lamp n on register n/8, output n%8)
LAMPS = ([(f"F{k} {n}", "A") for k, n in enumerate(
            ["THRU", "LF", "160/80", "60-30", "20-10", "6/4m", "2m/AIR", "70/23cm"])] +
         [(f"FR{k} {n}", "A") for k, n in enumerate(
            ["SYNC", "SYNC", "SYNC", "B2", "B1", "B0", "LNA", "QUIET", "CLR",
             "CHK", "CHK", "CHK", "CHK", "CHK", "CHK"])] +
         [("RX", "A"), ("0", "Y"), ("1", "Y"), ("OK", "G"), ("ERR", "R"), ("SYNC", "R"), ("CHK", "R"),
          ("GAP", "R"), ("RUNT", "R"), ("TMO", "R"), ("LNA", "G"), ("BYP", "Y"), ("AUTO", "Y"), ("FMN", "A"),
          ("I-LO", "Y"), ("I-OK", "G"), ("I-HI", "R"), ("TRIP", "R"), ("RUN", "G"), ("HOLD", "G"),
          ("LOW", "R"), ("QUIET", "Y"), ("DROP", "R"), ("GLT", "Y"), ("RST", "R")])
assert len(LAMPS) == 48
REG_TITLE = ["BANK (lamps 0-7)", "FRAME (lamps 8-15)", "FRAME + RX (lamps 16-23)",
             "DECODE (lamps 24-31)", "DECODE + RF (lamps 32-39)", "RF + SYSTEM (lamps 40-47)"]


def _register(s, k, X, Y):
    """one 74HC595 + 8 lamps. Q pins sink: VLED -> LED -> 2k2 -> Q."""
    u, ref = 14 + k, f"U{14 + k}"
    s.frame(X - 35.56, Y - 43.18, 124.46, 73.66, REG_TITLE[k])
    ser = "LED_SER" if k == 0 else f"LED_CH{k}"
    nets = {"14": ser, "11": "LED_SCK", "12": "LED_RCK", "13": "LED_OE_N", "16": "+3V3", "10": "+3V3",
            "8": "GND", "9": f"LED_CH{k + 1}" if k < 5 else None}
    qpins = ["15", "1", "2", "3", "4", "5", "6", "7"]
    nets.update({q: f"LAMP{8 * k + i}_Q" for i, q in enumerate(qpins)})
    s.add(Part(ref, "coaxolotl:74HC595", "74HC595PW(TSSOP16)", X, Y, 0, "Package_SO:TSSOP-16_4.4x5mm_P0.65mm",
               nets, txt=(X - 2.54, Y + 19.05, "right")))
    # inputs
    for pin, net in (("14", ser), ("11", "LED_SCK"), ("12", "LED_RCK"), ("13", "LED_OE_N")):
        end = s.pw(ref, pin, (X - 15.24, None))
        if net.startswith("LED_CH"):
            s.label(net, *end, "L")
        else:
            s.hlabel(net, *end, "L", "input")
    # supply: VCC and ~SRCLR to +3V3, 100 nF at the VCC riser
    vx, vy = s.pt(ref, "16")
    cx_, cy_ = s.pt(ref, "10")
    s.w((vx, vy), (vx, Y - 31.75))
    s.rail("+3V3", vx, Y - 31.75)
    s.w((cx_, cy_), (cx_, Y - 21.59), (vx, Y - 21.59))
    s.w((vx, Y - 29.21), (X - 12.7, Y - 29.21))
    cref = f"C{41 + k}"
    s.add(C(cref, "100nF/16V/10%", X - 12.7, Y - 25.4, "+3V3", "GND", Place=f"{ref}.16",
            txt=(X - 15.24, Y - 26.67, "right")))
    s.gnd(cref, "2")
    s.gnd(ref, "8")
    # chain out
    if k < 5:
        end = s.pw(ref, "9", (X + 12.7, None), (None, Y + 16.51))
        s.label(f"LED_CH{k + 1}", *end, "R")
    else:
        s.nc(ref, "9")
    # resistor packs: pins 1-4 on the left, 8-5 on the right, 2.54 mm pitch = Q pitch
    for h in range(2):
        rn = f"RN{1 + 2 * k + h}"
        qa = s.pt(ref, qpins[4 * h])[1]
        rnets = {}
        for j in range(4):
            n = 8 * k + 4 * h + j
            rnets[str(1 + j)] = f"LAMP{n}_Q"
            rnets[str(8 - j)] = f"LAMP{n}_K"
        tt = (X + 20.32, qa - 5.08, None) if h == 0 else (X + 20.32, qa + 10.16, None)
        s.add(Part(rn, "Device:R_Pack04", "2k2x4/5%(CAY16)", X + 20.32, qa + 5.08, 270,
                   "Resistor_SMD:R_Array_Convex_4x0603", rnets, {"MPN": "CAY16-222J4LF"}, txt=tt))
        for j in range(4):
            s.w(s.pt(ref, qpins[4 * h + j]), s.pt(rn, str(1 + j)))
    # lamps
    xl = X + 46.99
    xv = xl + 6.35
    rows = [Y - 2.54 + (i - 3.5) * 7.62 for i in range(8)]
    for i in range(8):
        n = 8 * k + i
        name, col = LAMPS[n]
        rn, pin = f"RN{1 + 2 * k + i // 4}", str(8 - i % 4)
        d = f"D{7 + n}"
        s.add(Part(d, "Device:LED", LAMP_COL[col], xl, rows[i], 0, LED0603,
                   {"1": f"LAMP{n}_K", "2": "VLED"}, txt=(xv + 2.54, rows[i] + 0.64),
                   vtxt=(xv + 8.89, rows[i] + 0.64)))
        colx = X + 30.48 + 2.54 * (i if i < 4 else 7 - i)
        s.pw(rn, pin, (colx, None), (None, rows[i]), s.pt(d, "1"))
        s.w(s.pt(d, "2"), (xv, rows[i]))
        s.intent(f"{n:2d}  {name}", xv + 21.59, rows[i] + 1.27)
    s.w((xv, rows[7]), (xv, rows[0] - 5.08))
    s.rail("VLED", xv, rows[0] - 5.08)


def sheet_panel(lib) -> DSheet:
    s = sheet(lib, "Panel", "panel.kicad_sch", "48-lamp front panel: 6x 74HC595 sinking, 2k2 arrays, VLED")
    s.zone = "DIGITAL"
    for k in range(6):
        _register(s, k, 50.8 + 127.0 * (k % 3), 78.74 + 88.9 * (k // 3))
    # OE_N pull-up: lamps dark until the first write
    s.frame(15.24, 213.36, 45.72, 33.02, "OE_N pull-up")
    s.add(R("R20", "10k/5%", 40.64, 227.33, "+3V3", "LED_OE_N", Note="lamps dark until first write"))
    s.w(s.pt("R20", "2"), (40.64, 233.68), (25.4, 233.68))
    s.hlabel("LED_OE_N", 25.4, 233.68, "L", "input")
    s.rail_pin("R20", "1", "+3V3")
    # break-off link: the lamp strip is a snap-off part of the board; these two
    # unpopulated 1x7 rows (one each side of the break line) take a cable if it is snapped off
    s.frame(66.04, 205.74, 104.14, 40.64, "Snap-off lamp strip link")
    for k, (ref, x) in enumerate((("J4", 104.14), ("J5", 157.48))):
        y = 229.87
        nets = {"1": "+3V3", "2": "VLED", "3": "LED_SER", "4": "LED_SCK", "5": "LED_RCK", "6": "LED_OE_N",
                "7": "GND"}
        s.add(Part(ref, "Connector_Generic:Conn_01x07", "NA(HDR1x7/2.54)", x, y, 0,
                   "Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical", nets,
                   {"Place": "main board, right end of the break line" if k == 0 else
                    "lamp strip, right end of the break line"}, dnp=True, txt=(x + 2.54, y - 11.43)))
        s.pw(ref, "1", (x - 7.62, None), (None, y - 12.7))
        s.rail("+3V3", x - 7.62, y - 12.7)
        s.pw(ref, "2", (x - 12.7, None), (None, y - 17.78))
        s.rail("VLED", x - 12.7, y - 17.78)
        for pin, net in (("3", "LED_SER"), ("4", "LED_SCK"), ("5", "LED_RCK"), ("6", "LED_OE_N")):
            end = s.pw(ref, pin, (x - 17.78, None))
            s.hlabel(net, *end, "L", "input")
        s.pw(ref, "7", (x - 7.62, None), (None, y + 10.16))
        s.rail("GND", x - 7.62, y + 10.16)
    for i, t in enumerate(["Lamp n: register n/8 (U14 = first after the MCU), output Q(n%8) - firmware/src/panel.h.",
                           "Q low = lamp on (sink). 1 mA per lamp: (VLED - 2 V - 0.1 V) / 2k2. Use 2 V LEDs only.",
                           "Lamp test lights all 48 (48 mA) with the LNA still off. Outside it, at most 23 lamps.",
                           "Panel modes (config frame, kept in flash): full / bands = U14 only / off = OE_N high.",
                           "U14-U19, RN1-RN12, D7-D54, C41-C46 and J5 sit on the snap-off strip; J4 on the main board."]):
        s.intent(t, 177.8, 222.25 + 3.18 * i)
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
