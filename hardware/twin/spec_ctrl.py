"""Coaxolotl control board (hardware/kicad_ctrl) digital twin - SPECIFICATION written from source documents, never
from our own schematic or board. Every table names its source. Refdes are ours (identifiers only); pin numbers and
connectivity come from the sources.

Sources
  [DS-BB52]   Silicon Labs EFM8BB52 Data Sheet rev 1.0, Table 6.1 Pin Definitions for EFM8BB52-QFN32 (attached by the
              owner 2026-11-07): 1 P0.0 (ADC0.VREF, VREFP.OUT), 2 N/C, 3 VDD, 4 RSTb/C2CK, 5 P3.7/C2D, 6-10 P3.4..P3.0,
              11-17 P2.6..P2.0, 18-25 P1.7..P1.0, 26-32 P0.7..P0.1, centre pad GND. Analog: P0.2 ADC0.CH0, P0.3 ADC0.CH1,
              P2.4 ADC0.CH13, P2.5 ADC0.CH14 (P2.4/P2.5 have no crossbar), P0.1 is also ADC0.AGND; P0.4 UART0_TX,
              P0.5 UART0_RX; P1.7 on the crossbar. Table 4.1: GPIO max VDD + 0.3 V; ADC VIN max at a pin = VDD,
              range VREF / gain; VREF pin 2.4 V setting 2.36-2.43 V with >= 0.1 uF bypass. Same pin list as the
              LCSC/EasyEDA symbol of C3225443.
  [FW]        firmware pin map, firmware/src/hal_efm8bb52.c header (EFM8BB52 Reference Manual rev 0.3): P0.0 VREF,
              P0.1 FAULT_N, P0.2 SENSE (port match + ADC), P0.3 SCAP_ADC, P0.4 TX, P0.5 RX, P0.6 LNA_EN, P0.7 LNA_PATH,
              P1.0-P1.2 SW_V1..V3, P1.3 LNA_PATH_OUT, P1.4 FMN, P1.7 BEACON (PCA CEX0), P2.0 LED_SER, P2.1 LED_SCK,
              P2.2 LED_RCK, P2.3 LED_OE_N, P2.4 ILNA_ADC, P2.5 ITOT_ADC; config.h scaling constants
  [AN945]     Silicon Labs AN945 (EFM8 factory bootloader): UART bootloader on P0.4 TX / P0.5 RX, entered when C2D is
              low at reset -> C2D must be pulled up
  [DS-66100]  TI LM66100 data sheet, Pin Functions (DCK): 1 VIN, 2 GND, 3 CE (active-low chip enable, "can be
              connected to VOUT for reverse current protection, do not leave floating"), 4 N/C, 5 ST (open drain,
              "connect to GND if not required"), 6 VOUT
  [DS-2553]   TI TPS2553 SLVS841G, Pin Functions SOT-23 (DBV): 1 IN, 2 GND, 3 EN (logic high turns the switch on;
              the TPS2552 is the active-low one), 4 FAULT (open drain, active low), 5 ILIM (RILIM 15-232 k to GND),
              6 OUT; IOS table: 49.9 k -> 475/520/565 mA, 210 k -> 110/130/150 mA
  [DS-180]    TI INA180 SBOS741H, Pin Functions, SOT-23 pinout A (INA180Ax): 1 OUT, 2 GND, 3 IN+ ("high side: bus
              voltage side of the sense resistor"), 4 IN- (load side), 5 VS 2.7-5.5 V; gain A1 20, A2 50, A3 100,
              A4 200 V/V; common mode -0.2..26 V
  [DS-1700]   Microchip MCP1700 DS20001826E, pin function table SOT-23: 1 GND, 2 VOUT, 3 VIN
  [DS-595]    Nexperia 74HC595 pin description (LCSC/EasyEDA symbol C5948): 1-7 Q1-Q7, 8 GND, 9 Q7S, 10 MR (low
              active), 11 SHCP, 12 STCP, 13 OE (low active), 14 DS, 15 Q0, 16 VCC
  [DS-BAT54]  BAT54WS (LCSC/EasyEDA symbol C304155): 1 C(athode), 2 A(node)
  [DS-4D03]   Uniroyal 4D03 4-resistor array (convex, 8 pins): resistor k between pins k and 9-k
  [KICAD]     KiCad library convention: diode, TVS and LED pad 1 = cathode
  [DOC-HW]    docs/hardware.md: power tree (coax -> DC feed chokes -> VCOAX: bleeder, PWR lamp, sense divider, ideal
              diode -> VMAIN -> TPS2553 -> 0.39 R -> LNA_VCC; ferrite -> lamps; 100R/BAT54 -> 330 uF and 220R/BAT54 ->
              0.1 F -> BAT54 -> VHOLD -> MCP1700 -> 3V3), whole-coax shunt before every load, back-channel tap
              BEACON -> 4k7 / 51R pad -> 1k -> 100p C0G -> coax centre; J6 pin table (interface to the RF board)
  [DOC-PANEL] docs/panel.md: lamp n = LED D(7+n), register k = U14+k drives columns 4k..4k+3, output (n%4)+4*(n//24)
              (QA..QH), 2.2 k per lamp; lamp names and colours; docs/protocol.md frame layout (sync 101, B2 B1 B0 LNA
              QUIET CLR, check); bandplan.json branch names
"""

# ---------------------------------------------------------------- pin tables (function name -> pad number)
BB52 = {"P0.0": "1", "NC": "2", "VDD": "3", "RST": "4", "C2D": "5",
        "P3.4": "6", "P3.3": "7", "P3.2": "8", "P3.1": "9", "P3.0": "10",
        "P2.6": "11", "P2.5": "12", "P2.4": "13", "P2.3": "14", "P2.2": "15", "P2.1": "16", "P2.0": "17",
        "P1.7": "18", "P1.6": "19", "P1.5": "20", "P1.4": "21", "P1.3": "22", "P1.2": "23", "P1.1": "24",
        "P1.0": "25", "P0.7": "26", "P0.6": "27", "P0.5": "28", "P0.4": "29", "P0.3": "30", "P0.2": "31",
        "P0.1": "32", "EP": "33"}                                                                    # [DS-BB52]
LM66100 = {"VIN": "1", "GND": "2", "CE": "3", "NC": "4", "ST": "5", "VOUT": "6"}                     # [DS-66100]
TPS2553 = {"IN": "1", "GND": "2", "EN": "3", "FAULT": "4", "ILIM": "5", "OUT": "6"}                  # [DS-2553]
INA180 = {"OUT": "1", "GND": "2", "IN+": "3", "IN-": "4", "VS": "5"}                                 # [DS-180]
MCP1700 = {"GND": "1", "VOUT": "2", "VIN": "3"}                                                      # [DS-1700]
HC595 = {"Q1": "1", "Q2": "2", "Q3": "3", "Q4": "4", "Q5": "5", "Q6": "6", "Q7": "7", "GND": "8", "Q7S": "9",
         "MR": "10", "SHCP": "11", "STCP": "12", "OE": "13", "DS": "14", "Q0": "15", "VCC": "16"}     # [DS-595]
DIODE = {"K": "1", "A": "2"}                                                                         # [KICAD] [DS-BAT54]
RN_PAIRS = (("1", "8"), ("2", "7"), ("3", "6"), ("4", "5"))                                          # [DS-4D03]

# our refdes for the parts the sources describe
U_MCU, U_IDEAL, U_LSW, U_ILNA, U_LDO, U_ITOT = "U5", "U1", "U2", "U3", "U4", "U20"
REGS = ["U14", "U15", "U16", "U17", "U18", "U19"]                     # [DOC-PANEL] chain order, U14 at the MCU
PINTABLES = {U_MCU: BB52, U_IDEAL: LM66100, U_LSW: TPS2553, U_ILNA: INA180, U_ITOT: INA180, U_LDO: MCP1700,
             **{r: HC595 for r in REGS}}

# ---------------------------------------------------------------- lamps [DOC-PANEL]
BANDS = ["THRU", "LF", "160/80", "60-30", "20-10", "6/4m", "2m/AIR", "70/23cm"]                     # bandplan.json
FRAME = ["SYNC", "SYNC", "SYNC", "B2", "B1", "B0", "LNA", "QUIET", "CLR"] + ["CHK"] * 6              # docs/protocol.md
LAMPS = BANDS + FRAME + ["RX", "0", "1", "OK", "ERR", "SYNC", "CHK", "GAP", "RUNT", "TMO", "LNA", "BYP", "AUTO",
                         "FMN", "I-LO", "I-OK", "I-HI", "TRIP", "RUN", "HOLD", "LOW", "QUIET", "DROP", "GLT", "RST"]


def _panel_colours():
    """lamp colours read by machine from the docs/panel.md lamp table ('| 0–7 | F0…F7 | amber | ...')"""
    import os, re
    col = {}
    for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "panel.md"),
                     encoding="utf-8"):
        m = re.match(r"\|\s*(\d+)(?:[–-](\d+))?\s*\|[^|]*\|\s*(\w+)\s*\|", line)
        if m:
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            for n in range(a, b + 1):
                col[n] = m.group(3).upper()
    return [col.get(n) for n in range(48)]


COLOUR = _panel_colours()
LAMP_R = 2200                                                                                         # 2.2 k per lamp
QNAME = ["Q0", "Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]                                             # QA..QH


def lamp_output(n):
    """lamp n -> (register refdes, 595 output name) [DOC-PANEL]"""
    row, col = divmod(n, 24)
    return REGS[col // 4], QNAME[(col % 4) + 4 * row]


J6_SILK = ["3V3", "LNA", "V1", "V2", "V3", "PTH", "OUT", "FMN", "GND", "GND"]   # [DOC-HW] J6 table, short names
STRIP_CORE = set(REGS) | {f"RN{k}" for k in range(1, 13)} | {f"D{7 + n}" for n in range(48)}   # [DOC-PANEL]

BB52_ADC = {"P0.2": 0, "P0.3": 1, "P2.4": 13, "P2.5": 14}          # [DS-BB52] ADC0 channel of each pin
FW_ADC = {"P0.2": "ADC_CH_VC", "P0.3": "ADC_CH_SCAP", "P2.4": "ADC_CH_ILNA", "P2.5": "ADC_CH_ITOT"}   # [FW] names

# ---------------------------------------------------------------- design-intent assertions (by function)
M = lambda p: (U_MCU, p)
A = [
    # ---- MCU supply, reset, debug, bootloader [DS-BB52] [AN945]
    ("gnd", [M("EP")], "EFM8BB52 EP is its only ground pin"),
    ("same", [M("VDD"), (U_LDO, "VOUT")], "MCU VDD from the 3.3 V LDO"),
    ("nc", M("NC"), "pin 2 is N/C"),
    ("pullup", M("RST"), M("VDD"), 10000, "RSTb 10 k pull-up"),
    ("pullup", M("C2D"), M("VDD"), 10000, "C2D pull-up: the factory UART bootloader is entered when C2D is low at reset"),
    ("walk", M("RST"), ("J1", "3"), 0, "C2 header pin 3 = C2CK/RST"),
    ("walk", M("C2D"), ("J1", "2"), 0, "C2 header pin 2 = C2D"),
    ("gnd", [("J1", "1")], "C2 header pin 1 = GND"),
    ("same", [("J1", "4"), M("VDD")], "C2 header pin 4 = 3V3"),
    ("walk", M("P0.4"), ("TP7", "1"), 0, "UART0 TX test point (bootloader)"),
    ("walk", M("P0.5"), ("TP8", "1"), 0, "UART0 RX test point (bootloader)"),
    ("only", M("P0.0"), {"C50"}, "VREF pin: only its bypass capacitor"),
    ("cap_gnd", M("P0.0"), 100e-9, "VREF bypass 100 nF to GND"),
    # ---- RF control lines to J6 [FW] [DOC-HW J6]
    ("walk", M("P1.0"), ("J6", "3"), 0, "SW_V1 -> J6.3"), ("walk", M("P1.1"), ("J6", "4"), 0, "SW_V2 -> J6.4"),
    ("walk", M("P1.2"), ("J6", "5"), 0, "SW_V3 -> J6.5"), ("walk", M("P0.7"), ("J6", "6"), 0, "LNA_PATH -> J6.6"),
    ("walk", M("P1.3"), ("J6", "7"), 0, "LNA_PATH_OUT -> J6.7"), ("walk", M("P1.4"), ("J6", "8"), 0, "FMN -> J6.8"),
    ("same", [("J6", "1"), M("VDD")], "J6.1 = 3V3"),
    ("same", [("J6", "2"), (U_ILNA, "IN-")], "J6.2 = LNA_VCC (after the shunt)"),
    ("gnd", [("J6", "9"), ("J6", "10")], "J6.9/10 = GND"),
    *[("pulldown", M(p), 10000, f"{p} 10 k pull-down (safe state in reset: THRU, bypass, notch out, LNA off)")
      for p in ("P1.0", "P1.1", "P1.2", "P0.7", "P1.3", "P1.4", "P0.6")],
    # ---- LNA supply [DS-2553] [DS-180]
    ("walk", M("P0.6"), (U_LSW, "EN"), 0, "LNA_EN -> TPS2553 EN (active high)"),
    ("walk", M("P0.1"), (U_LSW, "FAULT"), 0, "TPS2553 FAULT -> P0.1"),
    ("pullup", M("P0.1"), M("VDD"), 100000, "FAULT open drain: 100 k pull-up"),
    ("same", [(U_LSW, "IN"), (U_IDEAL, "VOUT")], "TPS2553 IN from VMAIN"),
    ("gnd", [(U_LSW, "GND")], "TPS2553 GND"),
    ("res_gnd", (U_LSW, "ILIM"), None, "RILIM from ILIM to GND"),
    ("thru_r", (U_LSW, "OUT"), (U_ILNA, "IN-"), 0.39, "0.39 R LNA shunt between TPS2553 OUT and LNA_VCC"),
    ("same", [(U_ILNA, "IN+"), (U_LSW, "OUT")], "INA180A2 IN+ on the supply side of the LNA shunt"),
    ("same", [(U_ILNA, "VS"), M("VDD")], "INA180A2 VS = 3V3"), ("gnd", [(U_ILNA, "GND")], "INA180A2 GND"),
    ("walk", (U_ILNA, "OUT"), M("P2.4"), 1, "INA180A2 OUT -> RC -> P2.4 (ILNA_ADC)"),
    ("part", U_ILNA, "INA180A2", "LNA monitor is the 50 V/V version (firmware I_UV_PER_MA 19500 = 0.39 R x 50)"),
    # ---- coax DC feed and whole-coax current [DOC-HW] [DS-180]
    ("chain", ("J3", "1"), ["L5", "L6", "L7", "R35"], (U_IDEAL, "VIN"),
     "coax centre -> 100 nH -> 10 uH -> 1 mH -> 0.1 R shunt -> VCOAX (DC feed, shunt last)"),
    ("value", "L5", "100nH", "100 nH first choke"), ("value", "L6", "10uH", "10 uH middle choke"),
    ("value", "L7", "1mH", "1 mH last choke"), ("value", "R35", "0R1", "0.1 R coax shunt"),
    ("parallel", "R24", "L6", 220, "220 R damping across the 10 uH"),
    ("only", ("R35", "1"), {"L7", U_ITOT}, "nothing but the shunt and its sense input before the shunt: every load is measured"),
    ("same", [(U_ITOT, "IN+"), ("R35", "1")], "INA180A3 IN+ on the coax side of R35 (bus side)"),
    ("same", [(U_ITOT, "IN-"), (U_IDEAL, "VIN")], "INA180A3 IN- on the load side of R35 (VCOAX)"),
    ("same", [(U_ITOT, "VS"), M("VDD")], "INA180A3 VS = 3V3"), ("gnd", [(U_ITOT, "GND")], "INA180A3 GND"),
    ("walk", (U_ITOT, "OUT"), M("P2.5"), 1, "INA180A3 OUT -> RC -> P2.5 (ITOT_ADC)"),
    ("part", U_ITOT, "INA180A3", "coax monitor is the 100 V/V version (firmware IT_UV_PER_MA 10000 = 0.1 R x 100)"),
    # ---- VCOAX loads [DOC-HW] [DS-66100]
    ("same", [("D1", "K"), (U_IDEAL, "VIN")], "TVS cathode on VCOAX"), ("gnd", [("D1", "A")], "TVS anode GND"),
    ("res_gnd", (U_IDEAL, "VIN"), 680, "680 R bleeder on VCOAX (sets the fall time)"),
    ("divider", (U_IDEAL, "VIN"), M("P0.2"), 10000, 22000, "VCOAX -> 10 k / 22 k -> SENSE (P0.2)"),
    ("led", "D2", (U_IDEAL, "VIN"), "PWR lamp from VCOAX through a resistor, cathode to GND"),
    ("same", [(U_IDEAL, "CE"), (U_IDEAL, "VOUT")], "LM66100 CE tied to VOUT (reverse current protection)"),
    ("gnd", [(U_IDEAL, "ST"), (U_IDEAL, "GND")], "LM66100 ST and GND to ground"),
    ("diff", [(U_IDEAL, "VIN"), (U_IDEAL, "VOUT")], "VCOAX and VMAIN are separate rails"),
    # ---- hold-up [DOC-HW] [DS-BAT54] [DS-1700]
    ("same", [("D3", "K"), ("D5", "K"), (U_LDO, "VIN")], "both hold-up diodes into VHOLD = LDO input"),
    ("res_between", ("D3", "A"), (U_IDEAL, "VOUT"), 100, "100 R from VMAIN to the fast hold-up diode"),
    ("res_between", ("D4", "A"), (U_IDEAL, "VOUT"), 220, "220 R from VMAIN to the supercap charge diode"),
    ("same", [("D4", "K"), ("D5", "A"), ("C10", "1")], "supercap node: charge diode out, OR diode in, C10 +"),
    ("divider", ("D4", "K"), M("P0.3"), 1e6, 1e6, "supercap -> 1 M / 1 M -> SCAP_ADC (P0.3)"),
    ("gnd", [(U_LDO, "GND")], "MCP1700 GND"),
    # ---- back-channel tap [DOC-HW]
    ("res_between", M("P1.7"), ("R32", "1"), 4700, "BEACON (P1.7) -> 4k7"),
    ("res_gnd", ("R32", "1"), 51, "51 R shunt of the beacon pad"),
    ("res_between", ("R32", "1"), ("C47", "1"), 1000, "1 k from the pad"),
    ("cap_between", ("C47", "1"), ("J3", "1"), 100e-12, "100 pF DC block to the coax centre"),
    # ---- lamp chain [DS-595] [DOC-PANEL]
    ("walk", M("P2.0"), (REGS[0], "DS"), 0, "LED_SER -> first register DS"),
    *[("same", [(REGS[k], "Q7S"), (REGS[k + 1], "DS")], f"chain {REGS[k]}.Q7S -> {REGS[k + 1]}.DS") for k in range(5)],
    ("same", [M("P2.1")] + [(r, "SHCP") for r in REGS], "LED_SCK to every SHCP"),
    ("same", [M("P2.2")] + [(r, "STCP") for r in REGS], "LED_RCK to every STCP"),
    ("same", [M("P2.3")] + [(r, "OE") for r in REGS], "LED_OE_N to every OE"),
    ("pullup", M("P2.3"), M("VDD"), 10000, "OE_N 10 k pull-up: lamps dark until the first write"),
    ("same", [M("VDD")] + [(r, "MR") for r in REGS] + [(r, "VCC") for r in REGS], "595 VCC and MR (inactive) on 3V3"),
    ("gnd", [(r, "GND") for r in REGS], "595 GND"),
    ("adc_fw",),
    ("lamps",), ("strip_link",), ("silk_lamps",), ("silk_j6",),
]

PHYSICAL = [
    "EFM8BB52 register set-up (ADC0CF2 GNDSL = 0 so P0.1/FAULT_N is not the ADC ground, REFSL = VREF pin, gain 0.5, "
    "PCA CEX0 routed to P1.7) is from the Reference Manual, not the data sheet: bring-up steps 3b/6",
    "LED polarity in the JLC placement preview (EasyEDA symbols disagree on the anode pin number; CPL corrected by "
    "the footprint cathode mark)",
    "J6 mate: the RF board's header pin order is defined only by docs/hardware.md until that board exists",
]
