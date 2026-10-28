"""F7 70/23cm high-pass with the openEMS layout N-port: current values vs values retuned for the pad capacitance
(~0.4-0.9 pF per lane node to In1). Grid search over E24 C / E12 L: best worst-case RL 380-2000 MHz with >= 20 dB
below 230 MHz -> C701 = C703 = 12 pF, C702 = 6.8 pF, L701 = L702 = 10 nH.   out/f7_retune.png"""
import os, sys
import numpy as np, skrf as rf
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import bank_sim as B
comps, pins = B.load_netlist(); by_ref, models = B.load_parts()
freq = rf.Frequency(10, 2500, 2491, unit="MHz"); f = freq.f / 1e6
lay = B.load_layout(os.path.join(HERE, "openems", "results", "f7"), freq)
NEW = {"C701": 12e-12, "C703": 12e-12, "C702": 6.8e-12, "L701": 10e-9, "L702": 10e-9}
cur, _ = B.build(7, comps, pins, by_ref, models, freq, layout=lay)
new, _ = B.build(7, comps, pins, by_ref, models, freq, vals=NEW, layout=lay)
ref, _ = B.build(7, comps, pins, by_ref, models, freq)
fig, ax = plt.subplots(1, 2, figsize=(13, 5))
for x, lim in ((ax[0], (300, 2500, -6, 0.5)), (ax[1], (10, 2500, -80, 2))):
    x.plot(f, ref.s_db[:, 1, 0], "0.6", lw=1, ls=":", label="S21 current, parts only")
    x.plot(f, cur.s_db[:, 1, 0], "tab:purple", lw=1.6, label="S21 current + layout")
    x.plot(f, new.s_db[:, 1, 0], "tab:green", lw=1.6, label="S21 retuned + layout")
    x.plot(f, cur.s_db[:, 0, 0], "tab:pink", lw=1, label="S11 current + layout")
    x.plot(f, new.s_db[:, 0, 0], "tab:olive", lw=1, label="S11 retuned + layout")
    for lo, hi in ((430, 440), (1030, 1090), (1240, 1300)):
        x.axvspan(lo, hi, color="tab:green", alpha=0.1)
    x.set_xlim(lim[:2]); x.set_ylim(lim[2:]); x.grid(alpha=0.3); x.set_xlabel("frequency (MHz)"); x.set_ylabel("dB")
ax[1].set_xscale("log"); ax[0].legend(fontsize=8, loc="lower right")
ax[0].set_title("passband (70 cm, ADS-B, 23 cm shaded)"); ax[1].set_title("10 MHz - 2.5 GHz")
fig.suptitle("F7 70/23cm HPF with layout: current 8.2p/4.7p/8.2p, 15n/15n vs retuned 12p/6.8p/12p, 10n/10n")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "out", "f7_retune.png"), dpi=110)
for nm, n in (("current", cur), ("retuned", new)):
    out = []
    for lo, hi, b in ((430, 440, "70cm"), (1030, 1090, "ADS-B"), (1240, 1300, "23cm"), (1700, 2000, "1.7-2 GHz")):
        m = (f >= lo) & (f <= hi)
        out.append(f"{b} IL {-n.s_db[m, 1, 0].min():.2f} dB RL {-n.s_db[m, 0, 0].max():.1f} dB")
    m = (f >= 88) & (f <= 108)
    print(f"{nm}: " + " | ".join(out) + f" | FM 88-108 MHz {n.s_db[m, 1, 0].max():.1f} dB")
