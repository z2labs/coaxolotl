#!/usr/bin/env python3
"""Generate the Coaxolotl KiCad project (schematic sheets + project symbol lib).

    python3 hardware/kicad/tools/build.py
    kicad-cli sch erc hardware/kicad/coaxolotl.kicad_sch

Edit this file, not the generated .kicad_sch files. Sheets are added one at
a time, each reviewed before the next (power + MCU first).
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import schgen as g  # noqa: E402
from schgen import Part, Sym  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent
PROJECT = "coaxolotl"

# ---------------------------------------------------------------------------
# footprints

R0603 = "Resistor_SMD:R_0603_1608Metric"
R1206 = "Resistor_SMD:R_1206_3216Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"
SOD323 = "Diode_SMD:D_SOD-323"
SMA_D = "Diode_SMD:D_SMA"
LED0603 = "LED_SMD:LED_0603_1608Metric"
FB0603 = "Inductor_SMD:L_0603_1608Metric"
TP = "TestPoint:TestPoint_Pad_D1.0mm"

# ---------------------------------------------------------------------------
# project symbol library


def ic_symbol(name, ref, footprint, datasheet, desc, left, right, width=15.24, pin_types=None,
              top_pins=(), bottom_pins=()):
    """Rectangle IC. left/right: lists of (number, name) or None for a gap.
    top_pins/bottom_pins: (number, name) placed on the top/bottom edge (power)."""
    pin_types = pin_types or {}
    n = max(len(left), len(right))
    top = (n - 1) * 2.54 / 2
    top = round(top / 1.27) * 1.27
    half = width / 2
    body = [Sym("symbol"), f"{name}_0_1",
            [Sym("rectangle"), [Sym("start"), -half, top + 2.54], [Sym("end"), half, top - n * 2.54],
             [Sym("stroke"), [Sym("width"), 0.254], [Sym("type"), Sym("default")]],
             [Sym("fill"), [Sym("type"), Sym("background")]]]]
    pins = [Sym("symbol"), f"{name}_1_1"]
    for side, lst in ((-1, left), (1, right)):
        for i, p in enumerate(lst):
            if p is None:
                continue
            num, pname = p
            y = top - i * 2.54
            x = side * (half + 2.54)
            ang = 0 if side < 0 else 180
            ptype = pin_types.get(num, "bidirectional")
            pins.append([Sym("pin"), Sym(ptype), Sym("line"), [Sym("at"), x, y, ang], [Sym("length"), 2.54],
                         [Sym("name"), pname, [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]]]],
                         [Sym("number"), num, [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]]]]])

    ybot = top - n * 2.54
    for edge, lst in ((1, top_pins), (-1, bottom_pins)):
        k = len(lst)
        for i, (num, pname) in enumerate(lst):
            x = round(((i - (k - 1) / 2) * 5.08) / 1.27) * 1.27
            y = (top + 2.54 + 2.54) if edge > 0 else (ybot - 2.54)
            ang = 270 if edge > 0 else 90
            ptype = pin_types.get(num, "bidirectional")
            pins.append([Sym("pin"), Sym(ptype), Sym("line"), [Sym("at"), x, y, ang], [Sym("length"), 2.54],
                         [Sym("name"), pname, [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]]]],
                         [Sym("number"), num, [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]]]]])

    def prop(k, v, y, hide=False):
        eff = [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]]]
        if hide:
            eff.append(Sym("hide"))
        return [Sym("property"), k, v, [Sym("at"), 0, y, 0], eff]

    return [Sym("symbol"), name, [Sym("in_bom"), Sym("yes")], [Sym("on_board"), Sym("yes")],
            prop("Reference", ref, top + 3.81), prop("Value", name, top - n * 2.54 - 1.27),
            prop("Footprint", footprint, 0, True), prop("Datasheet", datasheet, 0, True),
            prop("Description", desc, 0, True), body, pins]


def build_library(path: pathlib.Path):
    syms = [
        ic_symbol(
            "EFM8BB21F16G-C-QSOP24", "U", "Package_SO:QSOP-24_3.9x8.7mm_P0.635mm",
            "https://www.silabs.com/documents/public/data-sheets/efm8bb2-datasheet.pdf",
            "EFM8 Busy Bee 8051 MCU, 16 kB flash, 2.25 kB RAM, QSOP-24",
            left=[("7", "RSTb/C2CK"), ("8", "P3.0/C2D"), None,
                  ("12", "P2.0"), ("11", "P2.1"), ("10", "P2.2"), ("9", "P2.3")],
            top_pins=[("6", "VDD")], bottom_pins=[("5", "GND")],
            right=[("4", "P0.0/VREF"), ("3", "P0.1/AGND"), ("2", "P0.2"), ("1", "P0.3/EXTCLK"),
                   ("24", "P0.4/UART0_TX"), ("23", "P0.5/UART0_RX"), ("22", "P0.6/CNVSTR"), ("21", "P0.7"),
                   ("20", "P1.0"), ("19", "P1.1"), ("18", "P1.2"), ("17", "P1.3"),
                   ("16", "P1.4"), ("15", "P1.5/SDA"), ("14", "P1.6/SCL"), ("13", "P1.7")],
            width=30.48,
            pin_types={"6": "power_in", "5": "power_in", "7": "input"}),
        ic_symbol(
            "LM66100DCK", "U", "Package_TO_SOT_SMD:SOT-363_SC-70-6",
            "https://www.ti.com/lit/ds/symlink/lm66100.pdf",
            "Ideal diode, 1.5-5.5 V, 1.5 A, 79 mOhm. CE tied to VOUT = reverse blocking",
            left=[("1", "VIN"), ("3", "CE"), ("4", "NC")],
            right=[("6", "VOUT")],
            bottom_pins=[("2", "GND"), ("5", "~{ST}")],
            pin_types={"1": "power_in", "6": "power_out", "2": "power_in", "3": "input",
                       "5": "open_collector", "4": "no_connect"}),
        ic_symbol(
            "TPS2553DBV", "U", "Package_TO_SOT_SMD:SOT-23-6",
            "https://www.ti.com/lit/ds/symlink/tps2553.pdf",
            "Current-limited load switch, adjustable ILIM, active-high EN, 8 ms FAULT deglitch",
            left=[("1", "IN"), ("3", "EN"), ("5", "ILIM")],
            right=[("6", "OUT"), ("4", "~{FAULT}")],
            bottom_pins=[("2", "GND")],
            pin_types={"1": "power_in", "6": "power_out", "2": "power_in", "3": "input",
                       "4": "open_collector", "5": "passive"}),
    ]
    lib = [Sym("kicad_symbol_lib"), [Sym("version"), 20220914], [Sym("generator"), Sym("coaxolotl_build")]]
    lib += syms
    path.write_text(g.dump(lib) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# helpers


def _f(mpn, fields):
    f = dict(fields or {})
    if mpn:
        f["MPN"] = mpn
    return f


def R(ref, val, x, y, a, b, fp=R0603, rot=0, mpn="", fields=None, **kw):
    return Part(ref, "Device:R", val, x, y, rot, fp, {"1": a, "2": b}, _f(mpn, fields), **kw)


def C(ref, val, x, y, a, b, fp=C0603, rot=0, mpn="", pol=False, fields=None, **kw):
    return Part(ref, "Device:C_Polarized" if pol else "Device:C", val, x, y, rot, fp,
                {"1": a, "2": b}, _f(mpn, fields), **kw)


def D(ref, val, x, y, anode, cathode, fp=SOD323, rot=0, lib="Device:D_Schottky", mpn=""):
    return Part(ref, lib, val, x, y, rot, fp, {"2": anode, "1": cathode}, {"MPN": mpn} if mpn else {})


GLOBALS = {
    # power rails
    "VCOAX", "VMAIN", "VHOLD", "VLED", "LNA_VCC",
    # MCU <-> power sheet
    "SENSE", "SCAP_ADC", "ILNA", "FAULT_N", "LNA_EN",
    # MCU -> RF sheet (next review)
    "LNA_PATH", "SW_V1", "SW_V2", "SW_V3", "FMN",
    # MCU -> panel sheet (next review)
    "LED_SER", "LED_SCK", "LED_RCK", "LED_OE_N",
}


# ---------------------------------------------------------------------------
# sheet: power


def sheet_power(lib) -> g.Sheet:
    s = g.Sheet(lib, PROJECT, "Power", "power.kicad_sch", globals_=GLOBALS,
                comment="Coax DC tap, ideal diode, LNA switch + current monitor, hold-up, 3V3")
    # placement hints (docs: satellites next to their parent pin, TVS at the connector)
    s.place = {
        "D1": "edge:J_COAX (RF sheet bias-T), shortest GND return", "C1": "D1",
        "R3": "VCOAX tap", "R4": "R3", "C2": "U5.4 (SENSE at the MCU pin)",
        "C3": "U1.6", "C4": "U2.1", "C5": "U2.6", "R5": "U2.5 (short ILIM loop)", "R6": "U2.4",
        "R7": "between U2.6 and LNA feed", "C6": "U3.5", "R8": "U5.1 (ILNA RC at the ADC pin)", "C7": "U5.1",
        "C12": "U4.3", "C13": "U4.2", "R11": "C10", "R12": "U5.2", "C11": "U5.2",
        "C9": "D3 cathode / U4.3", "C8": "first 74HC595 (panel sheet)",
    }

    # A: coax DC tap -------------------------------------------------------
    s.zone = "POWER"
    s.box(15, 20, 130, 77, "A  VCOAX tap (ahead of all bulk capacitance)", [
        "Only these loads sit on VCOAX, so the OOK falling edge is set by R1 + coax/RTL capacitance.",
        "R1 = 680R gives ~6 ms with 10 uF on the RTL side; lower to 470R if bring-up step 1 needs it.",
        "SENSE = 0.69 x VCOAX (3.0 V @ 4.4 V). Goes to P0.0 (port match) and P0.1 (ADC)."])
    s.pwr_flag("VCOAX", 22.86, 40.64)
    s.add(D("D1", "SMF5.0A", 35.56, 58.42, "GND", "VCOAX", fp="Diode_SMD:D_SOD-123F", rot=270,
            lib="Device:D_Zener", mpn="SMF5.0A"))
    s.add(C("C1", "100n", 48.26, 58.42, "VCOAX", "GND"))
    s.add(R("R1", "680", 60.96, 58.42, "VCOAX", "GND", fp=R0603))
    s.add(R("R2", "3k3", 76.2, 45.72, "VCOAX", "PWR_LED_A"))
    s.add(Part("D2", "Device:LED", "PWR green", 76.2, 81.28, 90, LED0603, {"2": "PWR_LED_A", "1": "GND"}))
    s.zone = "ANALOG"
    s.add(R("R3", "10k 1%", 96.52, 58.42, "VCOAX", "SENSE"))
    s.add(R("R4", "22k 1%", 114.3, 58.42, "SENSE", "GND"))
    s.add(C("C2", "10n", 132.08, 58.42, "SENSE", "GND"))
    s.zone = "POWER"

    # B: ideal diode --------------------------------------------------------
    s.zone = "POWER"
    s.box(150, 20, 115, 75, "B  Ideal diode VCOAX -> VMAIN", [
        "CE tied to VOUT: the comparator turns the FET off whenever VMAIN > VCOAX,",
        "so LNA, lamps and hold-up never back-feed the sense node during a gap.",
        "ST unused -> GND (datasheet)."])
    s.add(Part("U1", "coaxolotl:LM66100DCK", "LM66100DCKR", 195.58, 55.88, 0,
               "Package_TO_SOT_SMD:SOT-363_SC-70-6",
               {"1": "VCOAX", "3": "VMAIN", "4": None, "6": "VMAIN", "5": "GND", "2": "GND"},
               {"MPN": "LM66100DCKR"}))
    s.add(C("C3", "10u 10V", 243.84, 55.88, "VMAIN", "GND", fp=C0805))

    # C: LNA switch + current ----------------------------------------------
    s.zone = "POWER"
    s.box(270, 20, 135, 112, "C  LNA supply: current-limited switch + shunt monitor", [
        "TPS2553: EN active-high, 8 ms FAULT deglitch, ILIM tied to IN = 75 mA typ (too low).",
        "R5 VERIFY: pick from datasheet Eq.1 / Fig.22 for IOS(min) > 130 mA (1% part).",
        "Shunt 0.39R x INA180A2 (50 V/V) = 19.5 mV/mA -> 100 mA = 1.95 V at P0.3.",
        "LNA_VCC continues to the LNA bias filter on the RF sheet."])
    s.add(Part("U2", "coaxolotl:TPS2553DBV", "TPS2553DBVR", 302.26, 45.72, 0, "Package_TO_SOT_SMD:SOT-23-6",
               {"1": "VMAIN", "3": "LNA_EN", "5": "ILIM", "6": "LNA_SW", "4": "FAULT_N", "2": "GND"},
               {"MPN": "TPS2553DBVR"}))
    s.add(C("C4", "1u", 281.94, 88.9, "VMAIN", "GND"))
    s.add(R("R5", "150k 1%", 297.18, 88.9, "ILIM", "GND", fields={"Note": "VERIFY vs TPS2553 Eq.1"}))
    s.add(R("R6", "100k", 312.42, 88.9, "+3V3", "FAULT_N"))
    s.add(C("C5", "1u", 327.66, 88.9, "LNA_SW", "GND"))
    s.add(R("R7", "0R39 1%", 347.98, 48.26, "LNA_SW", "LNA_VCC", fp=R1206, mpn="ERJ-8BQFR39V"))
    s.zone = "ANALOG"
    s.add(Part("U3", "Amplifier_Current:INA180A2", "INA180A2IDBVR", 368.3, 86.36, 0,
               "Package_TO_SOT_SMD:SOT-23-5",
               {"3": "LNA_SW", "4": "LNA_VCC", "1": "ILNA_RAW", "5": "+3V3", "2": "GND"},
               {"MPN": "INA180A2IDBVR"}))
    s.add(C("C6", "100n", 335.28, 109.22, "+3V3", "GND"))
    s.add(R("R8", "1k", 386.08, 111.76, "ILNA_RAW", "ILNA"))
    s.add(C("C7", "100n", 398.78, 111.76, "ILNA", "GND"))

    # D: lamp rail ----------------------------------------------------------
    s.zone = "POWER"
    s.box(270, 137, 135, 50, "D  Lamp rail", [
        "Lamps hang on VMAIN (not the hold-up): they go dark in every OOK gap",
        "and in a dropout instead of draining the supercap. 48 x ~1 mA max."])
    s.add(Part("FB1", "Device:FerriteBead_Small", "600R@100MHz", 299.72, 160.02, 0, FB0603,
               {"1": "VMAIN", "2": "VLED"}, {"MPN": "BLM18KG601SN1D"}))
    s.add(C("C8", "47u 10V", 335.28, 160.02, "VLED", "GND", fp=C1206))

    # E: hold-up ------------------------------------------------------------
    s.zone = "POWER"
    s.box(15, 100, 130, 120, "E  Hold-up: fast 330 uF + 0.1 F supercap, diode-OR", [
        "Fast path R9/D3 boots the MCU in ~30 ms. Slow path R10/D4 charges the supercap",
        "(<=18 mA, tau ~22 s). D5 lets the supercap carry VHOLD through a dropout (~4 min @ 0.6 mA).",
        "SCAP_ADC = VSCAP / 2 -> P0.2 (HOLD lamp)."])
    s.add(R("R9", "100", 25.4, 124.46, "VMAIN", "HU_FAST"))
    s.add(D("D3", "BAT54WS", 58.42, 124.46, "HU_FAST", "VHOLD", mpn="BAT54WS"))
    s.add(R("R10", "220", 25.4, 157.48, "VMAIN", "SC_CHG"))
    s.add(D("D4", "BAT54WS", 58.42, 157.48, "SC_CHG", "VSCAP", mpn="BAT54WS"))
    s.add(D("D5", "BAT54WS", 104.14, 157.48, "VSCAP", "VHOLD", mpn="BAT54WS"))
    s.add(C("C9", "330u 6.3V polymer", 96.52, 124.46, "VHOLD", "GND", pol=True,
            fp="Capacitor_Tantalum_SMD:CP_EIA-7343-31_Kemet-D", mpn="T520D337M006ATE025"))
    s.add(C("C10", "0.1F 5.5V", 30.48, 187.96, "VSCAP", "GND", pol=True,
            fp="Capacitor_THT:CP_Radial_D13.0mm_P5.00mm",
            mpn="KR-5R5V104-R", fields={"Note": "VERIFY footprint vs chosen coin supercap"}))
    s.zone = "ANALOG"
    s.add(R("R11", "1M", 63.5, 187.96, "VSCAP", "SCAP_ADC"))
    s.add(R("R12", "1M", 86.36, 187.96, "SCAP_ADC", "GND"))
    s.add(C("C11", "100n", 104.14, 187.96, "SCAP_ADC", "GND"))
    s.pwr_flag("VHOLD", 124.46, 116.84)

    # F: 3V3 ----------------------------------------------------------------
    s.zone = "POWER"
    s.box(150, 100, 115, 55, "F  3V3 for MCU, switches, INA180, 595s", [
        "MCP1700: 1.6 uA Iq, 178 mV dropout. Needs >= 1 uF ceramic on the output."])
    s.add(Part("U4", "Regulator_Linear:MCP1700x-330xxTT", "MCP1700T-3302E/TT", 200.66, 121.92, 0,
               "Package_TO_SOT_SMD:SOT-23", {"3": "VHOLD", "2": "+3V3", "1": "GND"},
               {"MPN": "MCP1700T-3302E/TT"}))
    s.add(C("C12", "1u", 170.18, 134.62, "VHOLD", "GND"))
    s.add(C("C13", "1u", 233.68, 134.62, "+3V3", "GND"))
    s.pwr_flag("GND", 254.0, 116.84)
    return s


# ---------------------------------------------------------------------------
# sheet: MCU


def sheet_mcu(lib) -> g.Sheet:
    s = g.Sheet(lib, PROJECT, "MCU", "mcu.kicad_sch", globals_=GLOBALS,
                comment="EFM8BB21F16G, C2 debug, reset-safe pull resistors")
    s.place = {"C14": "U5.6", "C15": "U5.6 (closest)", "R13": "U5.7", "J1": "edge",
               "R14": "U5.22", "R15": "U5.21", "R16": "U5.20", "R17": "U5.19", "R18": "U5.18",
               "R19": "U5.17", "R20": "U5.9"}

    s.zone = "DIGITAL"
    s.box(15, 20, 175, 130, "A  EFM8BB21F16G-C-QSOP24", [
        "Pin map = firmware/src/hal_efm8bb2.c. P0.0 and P0.1 share SENSE (digital + ADC).",
        "Pins follow placement: QSOP pins 1-12 (analog, debug, lamp chain P2.x) face power/panel,",
        "pins 13-24 (switch control, LNA_EN, FAULT_N) face the RF chain. No control line crosses the MCU.",
        "SYSCLK HFOSC0/8 = 3.06 MHz; Snooze when idle (no clock -> no spurs). Keep under the can."])
    s.add(Part("U5", "coaxolotl:EFM8BB21F16G-C-QSOP24", "EFM8BB21F16G-C-QSOP24", 101.6, 76.2, 0,
               "Package_SO:QSOP-24_3.9x8.7mm_P0.635mm",
               {"6": "+3V3", "7": "RST_N", "8": "C2D",
                "12": "LED_SER", "11": "LED_SCK", "10": "LED_RCK", "9": "LED_OE_N", "5": "GND",
                "4": "SENSE", "3": "SENSE", "2": "SCAP_ADC", "1": "ILNA",
                "24": "TP_TX", "23": "FAULT_N", "22": "LNA_EN", "21": "LNA_PATH",
                "20": "SW_V1", "19": "SW_V2", "18": "SW_V3", "17": "FMN",
                "16": "SPARE_P14", "15": "SPARE_P15", "14": "SPARE_P16", "13": "SPARE_P17"},
               {"MPN": "EFM8BB21F16G-C-QSOP24"}))
    s.add(C("C14", "1u", 30.48, 132.08, "+3V3", "GND"))
    s.add(C("C15", "100n", 43.18, 132.08, "+3V3", "GND"))

    # B: debug -------------------------------------------------------------
    s.zone = "DIGITAL"
    s.box(195, 20, 90, 60, "B  C2 debug / programming", [
        "4-pin 2.54 mm: 3V3, C2D, C2CK(RSTb), GND. Works with a SiLabs debug adapter",
        "or an Arduino-based C2 programmer. Do not add a cap on RSTb (C2 clock)."])
    s.add(Part("J1", "Connector_Generic:Conn_01x04", "C2", 246.38, 45.72, 0,
               "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
               {"1": "+3V3", "2": "C2D", "3": "RST_N", "4": "GND"}))
    s.add(R("R13", "10k", 213.36, 50.8, "+3V3", "RST_N"))

    # C: reset-safe defaults ------------------------------------------------
    s.zone = "DIGITAL"
    s.box(195, 85, 210, 65, "C  Reset-safe defaults (pins are weak-pull-up / Hi-Z until firmware runs)", [
        "Pull-downs: LNA off, bypass, branch 0 = THRU, FM notch out -> the box is a plain cable during reset.",
        "OE_N pull-up keeps all lamps dark until the first panel write. FAULT_N pull-up is on the power sheet."])
    x = 205.74
    for i, net in enumerate(["LNA_EN", "LNA_PATH", "SW_V1", "SW_V2", "SW_V3", "FMN"]):
        s.add(R(f"R{14 + i}", "10k", x + i * 22.86, 115.57, net, "GND"))
    s.add(R("R20", "10k", x + 6 * 22.86, 115.57, "+3V3", "LED_OE_N"))

    # D: test points --------------------------------------------------------
    s.zone = "DIGITAL"
    s.box(15, 155, 175, 40, "D  Test points", [
        "TP_TX: firmware drives it low; route UART0 TX here for debug prints if ever needed."])
    for i, net in enumerate(["TP_TX", "SPARE_P14", "SPARE_P15", "SPARE_P16", "SPARE_P17", "SENSE", "+3V3"]):
        s.add(Part(f"TP{i + 1}", "Connector:TestPoint", net.replace("+", ""), 30.48 + i * 22.86, 175.26, 0,
                   TP, {"1": net}))
    return s


# ---------------------------------------------------------------------------
# root


def main():
    sym_path = OUT / "coaxolotl.kicad_sym"
    build_library(sym_path)
    lib = g.Library(extra=[sym_path])

    root = g.Sheet(lib, PROJECT, "Coaxolotl", f"{PROJECT}.kicad_sch",
                   comment="Bias-T powered, OOK switched LNA + 8-way filter bank", globals_=GLOBALS)
    sheets = [sheet_power(lib), sheet_mcu(lib)]
    pages = {sh.file: str(i + 2) for i, sh in enumerate(sheets)}

    root.text("COAXOLOTL", 25.4, 30.48, size=5, bold=True)
    root.text("Mast-head LNA + 8-way filter bank, powered and keyed over one coax by the RTL-SDR bias-T.",
              25.4, 38.1, size=1.8)
    notes = [
        "Sheets (review order):",
        "  2  Power  - VCOAX tap, LM66100 ideal diode, TPS2553 + INA180 LNA supply, hold-up, 3V3     [this review]",
        "  3  MCU    - EFM8BB21F16G, C2, reset-safe pulls, test points                                [this review]",
        "  4  RF     - antenna protection, LNA/bypass SPDTs, preselector, FM notch, 2x PE42582, 8 filters, bias-T  [next]",
        "  5  Panel  - 6x 74HC595, 48 lamps                                                          [next]",
        "",
        "Conventions: nets in CAPS with global labels cross sheets. Every pin is labelled (no long wires),",
        "so the Python in hardware/kicad/tools/build.py is the source of truth.",
        "Firmware pin map: firmware/src/hal_efm8bb2.c   Design notes: docs/hardware.md",
    ]
    for i, n in enumerate(notes):
        root.text(n, 25.4, 50.8 + i * 3.3, size=1.5)
    for i, sh in enumerate(sheets):
        root.subsheet(sh, 25.4 + i * 60, 101.6, 50.8, 20.32)

    root_path = "/" + root.uuid
    (OUT / f"{PROJECT}.kicad_sch").write_text(root.render(root.uuid, root_path, PROJECT, "1", pages))
    for sh in sheets:
        (OUT / sh.file).write_text(sh.render(root.uuid, f"{root_path}/{sh.sheet_uuid}", PROJECT,
                                             pages[sh.file], pages))

    (OUT / "sym-lib-table").write_text(
        '(sym_lib_table\n  (lib (name "coaxolotl")(type "KiCad")(uri "${KIPRJMOD}/coaxolotl.kicad_sym")'
        '(options "")(descr "Coaxolotl project symbols"))\n)\n')
    pro = OUT / f"{PROJECT}.kicad_pro"
    if not pro.exists():
        pro.write_text(json.dumps({"meta": {"filename": f"{PROJECT}.kicad_pro", "version": 1},
                                   "sheets": [[root.uuid, "Root"]]}, indent=2) + "\n")
    n = sum(len(sh.parts) for sh in sheets)
    print(f"wrote {len(sheets) + 1} sheets, {n} parts -> {OUT}")


if __name__ == "__main__":
    main()
