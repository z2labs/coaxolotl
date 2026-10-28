"""One filter-bank lane: part models alone vs part models + the openEMS layout N-port of the lane.

    python3 simu/lane_layout_plot.py 6 openems/results/f6      -> out/f6_layout.png
"""
import os, sys, json
import numpy as np, skrf as rf
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import bank_sim as B
k, d = int(sys.argv[1]), os.path.join(HERE, sys.argv[2])
bp = json.load(open(os.path.join(B.ROOT, "bandplan.json"), encoding="utf-8"))["branches"][k]
lo, hi = bp["lo_hz"] / 1e6, bp["hi_hz"] / 1e6
comps, pins = B.load_netlist(); by_ref, models = B.load_parts()
nt = rf.Network(os.path.join(d, next(f for f in os.listdir(d) if f.endswith("p") and ".s" in f)))
fmax = nt.f[-1] / 1e6
freq = rf.Frequency(10, fmax, int(fmax - 9), unit="MHz")
lay = B.load_layout(d, freq)
a, _ = B.build(k, comps, pins, by_ref, models, freq)
b, _ = B.build(k, comps, pins, by_ref, models, freq, layout=lay)
f = freq.f / 1e6
fig, ax = plt.subplots(1, 2, figsize=(13, 5))
zoom = (max(10, lo * 0.5), min(fmax, hi * 1.6))
for x, lim in ((ax[0], zoom + (-6, 0.5)), (ax[1], (10, fmax, -80, 2))):
    x.plot(f, a.s_db[:, 1, 0], "tab:blue", lw=1.6, label="S21 parts")
    x.plot(f, b.s_db[:, 1, 0], "tab:purple", lw=1.6, label="S21 parts + layout")
    x.plot(f, a.s_db[:, 0, 0], "tab:red", lw=1, ls="--", label="S11 parts")
    x.plot(f, b.s_db[:, 0, 0], "tab:pink", lw=1, label="S11 parts + layout")
    x.axvspan(max(lo, 10), min(hi, fmax), color="tab:green", alpha=0.08)
    x.set_xlim(lim[:2]); x.set_ylim(lim[2:]); x.grid(alpha=0.3); x.set_xlabel("frequency (MHz)"); x.set_ylabel("dB")
ax[0].set_title(f"passband {lo:g}-{hi:g} MHz"); ax[1].set_title(f"10 MHz - {fmax:g} MHz")
ax[1].set_xscale("log"); ax[0].legend(fontsize=8, loc="lower center")
fig.suptitle(f"F{k} {bp['name']}: openEMS layout N-port ({nt.nports} ports, copper as PEC sheet, no mask) + datasheet/s2p part models")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "out", f"f{k}_layout.png"), dpi=110)
ib = (f >= max(lo, 10)) & (f <= min(hi, fmax))
for nm, n in (("parts", a), ("parts+layout", b)):
    s21 = n.s_db[:, 1, 0]
    i3 = f[s21 >= s21.max() - 3]
    print(f"F{k} {nm:13s} IL {-s21[ib].max():.2f}-{-s21[ib].min():.2f} dB, RL min {-n.s_db[ib, 0, 0].max():.1f} dB, "
          f"-3 dB {i3.min():.0f}-{i3.max():.0f} MHz")
