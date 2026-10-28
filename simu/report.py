"""Coaxolotl filter-bank simulation report (English, a few pages, charts) -> out/coaxolotl_filter_sim_report.pdf

    python3 simu/report.py
"""
import os, sys, json, datetime
import numpy as np, skrf as rf
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak, KeepTogether

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import bank_sim as B
OUT = os.path.join(HERE, "out"); FIG = os.path.join(OUT, "report_fig"); os.makedirs(FIG, exist_ok=True)
comps, pins = B.load_netlist(); by_ref, models = B.load_parts()
bp = json.load(open(os.path.join(B.ROOT, "bandplan.json"), encoding="utf-8"))["branches"]
plt.rcParams.update({"font.size": 9, "axes.grid": True, "grid.alpha": 0.3})
COL = ["#555555", "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2"]

# ---------------------------------------------------------------- figure 1: the bank, part models
freq = rf.Frequency(0.05, 2500, 1501, unit="MHz", sweep_type="log")
f = freq.f / 1e6
rows1 = []
fig, ax = plt.subplots(figsize=(7.2, 3.4))
for k in range(1, 8):
    n, parts = B.build(k, comps, pins, by_ref, models, freq)
    ax.semilogx(f, n.s_db[:, 1, 0], color=COL[k], lw=1.4, label=f"F{k} {bp[k]['name']}")
    lo, hi = bp[k]["lo_hz"] / 1e6, bp[k]["hi_hz"] / 1e6
    ib = (f >= max(lo, 0.1)) & (f <= hi)
    rows1.append([f"F{k}", bp[k]["name"], bp[k]["filter"], str(len(parts)),
                  f"{-n.s_db[ib, 1, 0].max():.2f} - {-n.s_db[ib, 1, 0].min():.2f}", f"{-n.s_db[ib, 0, 0].max():.1f}"])
ax.set_xlim(0.05, 2500); ax.set_ylim(-70, 2); ax.set_xlabel("frequency (MHz)"); ax.set_ylabel("S21 (dB)")
ax.legend(fontsize=7, ncol=4, loc="lower center"); ax.set_title("Filter bank, part models (no layout): insertion gain S21")
fig.tight_layout(); fig.savefig(os.path.join(FIG, "bank.png"), dpi=170); plt.close(fig)

# ---------------------------------------------------------------- figure 2: layout effect per lane
LANES = [(0, "thru", (10, 2500)), (5, "f5", (15, 130)), (6, "f6", (60, 260)), (7, "f7", (200, 2500))]
rows2 = []
fig, axs = plt.subplots(2, 2, figsize=(7.2, 5.6))
for ax, (k, d, zoom) in zip(axs.flat, LANES):
    dd = os.path.join(HERE, "openems", "results", d)
    nt = rf.Network(os.path.join(dd, next(x for x in os.listdir(dd) if ".s" in x and x.endswith("p"))))
    pj = json.load(open(os.path.join(dd, "ports.json")))
    fmax = nt.f[-1] / 1e6
    fr = rf.Frequency(10, fmax, int(fmax - 9), unit="MHz"); ff = fr.f / 1e6
    lay = B.load_layout(dd, fr)
    a, _ = B.build(k, comps, pins, by_ref, models, fr)
    b, _ = B.build(k, comps, pins, by_ref, models, fr, layout=lay)
    ax.plot(ff, a.s_db[:, 1, 0], "tab:blue", lw=1.3, label="S21 parts")
    ax.plot(ff, b.s_db[:, 1, 0], "tab:purple", lw=1.3, label="S21 parts + layout")
    ax.plot(ff, a.s_db[:, 0, 0], "tab:red", lw=0.9, ls="--", label="S11 parts")
    ax.plot(ff, b.s_db[:, 0, 0], "tab:pink", lw=0.9, label="S11 parts + layout")
    lo, hi = bp[k]["lo_hz"] / 1e6, bp[k]["hi_hz"] / 1e6
    ax.axvspan(max(lo, 10), min(hi, fmax), color="tab:green", alpha=0.08)
    ax.set_xlim(zoom); ax.set_ylim(-30 if k else -40, 1)
    ax.set_title(f"F{k} {bp[k]['name']} ({nt.nports} ports)", fontsize=9); ax.set_xlabel("frequency (MHz)"); ax.set_ylabel("dB")
    ib = (ff >= max(lo, 10)) & (ff <= min(hi, fmax))
    sim = pj.get("simulator", {}).get("md5", "")[:8] or "e7dd71be*"
    rows2.append([f"F{k} {bp[k]['name']}", f"{nt.nports}", f"{-a.s_db[ib, 1, 0].max():.2f} - {-a.s_db[ib, 1, 0].min():.2f}",
                  f"{-b.s_db[ib, 1, 0].max():.2f} - {-b.s_db[ib, 1, 0].min():.2f}",
                  f"{-a.s_db[ib, 0, 0].max():.1f}" if k else "-", f"{-b.s_db[ib, 0, 0].max():.1f}"])
axs[0][0].legend(fontsize=7, loc="lower left")
fig.suptitle("Layout effect: part models alone vs part models joined into the openEMS lane N-port", fontsize=10)
fig.tight_layout(); fig.savefig(os.path.join(FIG, "lanes.png"), dpi=170); plt.close(fig)

# ---------------------------------------------------------------- figure 3: F7 retune
fr = rf.Frequency(10, 2500, 2491, unit="MHz"); ff = fr.f / 1e6
lay = B.load_layout(os.path.join(HERE, "openems", "results", "f7"), fr)
NEW = {"C701": 12e-12, "C703": 12e-12, "C702": 6.8e-12, "L701": 10e-9, "L702": 10e-9}
cur, _ = B.build(7, comps, pins, by_ref, models, fr, layout=lay)
new, _ = B.build(7, comps, pins, by_ref, models, fr, vals=NEW, layout=lay)
fig, ax = plt.subplots(figsize=(7.2, 3.0))
ax.plot(ff, cur.s_db[:, 1, 0], "tab:purple", lw=1.4, label="S21 current values + layout")
ax.plot(ff, new.s_db[:, 1, 0], "tab:green", lw=1.4, label="S21 retuned + layout")
ax.plot(ff, cur.s_db[:, 0, 0], "tab:pink", lw=0.9, label="S11 current + layout")
ax.plot(ff, new.s_db[:, 0, 0], "tab:olive", lw=0.9, label="S11 retuned + layout")
for lo, hi in ((430, 440), (1030, 1090), (1240, 1300)):
    ax.axvspan(lo, hi, color="tab:green", alpha=0.12)
ax.set_xlim(250, 2500); ax.set_ylim(-30, 1); ax.set_xlabel("frequency (MHz)"); ax.set_ylabel("dB")
ax.legend(fontsize=7, loc="lower right", ncol=2)
ax.set_title("F7 70/23cm HPF: 8.2p/4.7p/8.2p, 15n/15n vs 12p/6.8p/12p, 10n/10n (70 cm, ADS-B, 23 cm shaded)", fontsize=9)
fig.tight_layout(); fig.savefig(os.path.join(FIG, "f7.png"), dpi=170); plt.close(fig)
rows3 = []
for lo, hi, nm in ((430, 440, "70 cm"), (1030, 1090, "ADS-B 1090 MHz"), (1240, 1300, "23 cm"), (1700, 2000, "1.7-2 GHz"), (88, 108, "FM 88-108 MHz (stop)")):
    m = (ff >= lo) & (ff <= hi)
    if lo == 88:
        rows3.append([nm, f"{cur.s_db[m, 1, 0].max():.1f} dB", "", f"{new.s_db[m, 1, 0].max():.1f} dB", ""])
    else:
        rows3.append([nm, f"{-cur.s_db[m, 1, 0].min():.2f}", f"{-cur.s_db[m, 0, 0].max():.1f}",
                      f"{-new.s_db[m, 1, 0].min():.2f}", f"{-new.s_db[m, 0, 0].max():.1f}"])

# ---------------------------------------------------------------- PDF
ss = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=15, spaceAfter=4)
H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11.5, spaceBefore=8, spaceAfter=3)
P = ParagraphStyle("p", parent=ss["BodyText"], fontSize=9, leading=12)
S = ParagraphStyle("s", parent=P, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#444444"))


def table(data, widths, head=True):
    t = Table(data, colWidths=[w * mm for w in widths])
    st = [("FONT", (0, 0), (-1, -1), "Helvetica", 8), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")),
          ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (-1, -1), "CENTER"),
          ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if head:
        st += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef6"))]
    t.setStyle(TableStyle(st)); return t


def img(name, w=180):
    from PIL import Image as PI
    p = os.path.join(FIG, name); W, Hh = PI.open(p).size
    return Image(p, width=w * mm, height=w * mm * Hh / W)


story = [Paragraph("Coaxolotl filter bank - simulation report", H1),
         Paragraph(f"Mast-head LNA + 8-way switched filter bank, bias-T powered over coax. "
                   f"branch feat/filter-sim, {datetime.date.today():%Y-%m-%d}.", S), Spacer(1, 4),
         Paragraph("Summary", H2),
         Paragraph("All eight filter branches were simulated from the KiCad netlist with real part models (vendor S-parameters "
                   "where published, otherwise datasheet Q, self-resonance and DC resistance). The four VHF/UHF lanes were "
                   "then simulated as copper with openEMS (FDTD) and the part models connected into those N-ports. "
                   "<b>F5, F6 and THRU are fine with the layout. F7 (70/23cm high-pass) is not:</b> the pad capacitance of "
                   "0603 parts over the In1 ground plane (0.4-0.9 pF per lane node) pulls its return loss down to 5.4 dB "
                   "above 1 GHz. A value change alone (no PCB change) fixes most of it: C701 = C703 = 12 pF, "
                   "C702 = 6.8 pF, L701 = L702 = 10 nH.", P),
         Paragraph("Method", H2),
         Paragraph("<b>Circuit level</b> (scikit-rf, simu/bank_sim.py): every branch is built from the netlist between the two "
                   "PE42582 switch ports (switches not yet in the chain). Inductors: Murata LQW18 s2p where available, "
                   "else datasheet model at minimum Q / minimum SRF (pessimistic). C0G capacitors: ESL + loss tangent. "
                   "<b>Layout level</b> (openEMS, z2 twin kicad_sim 2026-10-21, simu/openems/lane_nport.py): the routed "
                   "board (JLC04161H-7628 stack-up) cropped to one lane, a lumped port on every part pad referenced to the plane "
                   "under it, copper as PEC sheet, no solder mask, 10 MHz to 1.5/2.5 GHz. The parts are joined across the port "
                   "pairs afterwards, so values can be changed without a new field run. GPUs: GTX 1080 (office-z170) and "
                   "GTX 1080 Ti (X99); one N-port can be split over both machines (--exc / --collect, verified bit-identical).", P),
         Paragraph("1. Filter bank with part models", H2), img("bank.png"),
         table([["branch", "name", "filter", "parts", "in-band IL (dB)", "min RL (dB)"]] + rows1, [14, 20, 42, 12, 32, 22]),
         Paragraph("IL: insertion loss, RL: return loss, worst case over the branch's band. F4: LQW18CA R33 (L403/L404) is a "
                   "ferrite-core audio-range choke; LQW18AN R33 recommended (it also has no JLC/EasyEDA footprint). "
                   "F5 RL: E12 rounding of the design values. F1/F2: stop band returns near 1-2 GHz from inductor self-resonance.", S),
         PageBreak(),
         Paragraph("2. Layout effect (openEMS)", H2), img("lanes.png"),
         table([["lane", "ports", "IL parts (dB)", "IL + layout (dB)", "RL parts (dB)", "RL + layout (dB)"]] + rows2,
               [32, 14, 32, 32, 30, 32]),
         Paragraph("F6 ran on the routed board before the 2 mm lamp-strip change (same lane geometry); F5, F7 and THRU on the "
                   "final board 6cacac4. THRU: 17 mm of 50 ohm CPWG + R21 between the switch pins (R21's model gives the "
                   "small loss). Lane nodes carry 1.0-1.1 pF (F5), 0.8-0.95 pF (F6) and 0.4-0.9 pF (F7) to In1; "
                   "harmless below 200 MHz.", S),
         KeepTogether([Paragraph("3. F7 70/23cm: retune for the pad capacitance", H2), img("f7.png")]),
         table([["band", "IL current (dB)", "RL current (dB)", "IL retuned (dB)", "RL retuned (dB)"]] + rows3, [44, 32, 32, 32, 32]),
         Paragraph("E24 capacitor / E12 inductor grid search with the layout N-port: best worst-case RL over 380-2000 MHz with "
                   "at least 20 dB rejection below 230 MHz. 70 cm loses a little (RL 14.8 to 10.1 dB), ADS-B and 23 cm gain a lot. "
                   "Clearing In1/In2 under the F7 pads was also simulated (RL 14 dB) but is not conclusive: the ports then "
                   "reference B.Cu 1.5 mm below and their own ~1 nH enters the series parts; pad-to-pad ports would be needed.", S),
         Paragraph("4. Limits and next steps", H2),
         Paragraph("- Copper is lossless (PEC) and the solder mask is left out: real IL is slightly higher.<br/>"
                   "- PE42582 switch S-parameters are not yet in the chain (adds its own IL at both ends of every branch).<br/>"
                   "- F1-F4 (below 30 MHz) are circuit-level only: the layout is electrically tiny there.<br/>"
                   "- Decision needed: F7 retune values; F4 L403/L404 replacement; LCSC codes for 14 open BOM lines.", P)]

doc = SimpleDocTemplate(os.path.join(OUT, "coaxolotl_filter_sim_report.pdf"), pagesize=A4, leftMargin=15 * mm,
                        rightMargin=15 * mm, topMargin=13 * mm, bottomMargin=13 * mm, title="Coaxolotl filter bank - simulation report",
                        author="z2labs")
doc.build(story)
print("->", os.path.join(OUT, "coaxolotl_filter_sim_report.pdf"))
