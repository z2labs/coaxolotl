"""F6 (2m/AIR, 108-174 MHz): part models alone vs part models + the openEMS layout N-port of the lane."""
import os, sys
import numpy as np, skrf as rf
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import bank_sim as B
comps, pins = B.load_netlist(); by_ref, models = B.load_parts()
freq = rf.Frequency(10, 1500, 1491, unit="MHz")
lay = B.load_layout(os.path.join(HERE, "openems", "results", "f6"), freq)
a, _ = B.build(6, comps, pins, by_ref, models, freq)
b, _ = B.build(6, comps, pins, by_ref, models, freq, layout=lay)
f = freq.f / 1e6
fig, ax = plt.subplots(1, 2, figsize=(13, 5))
for x, lim in ((ax[0], (60, 260, -6, 0.5)), (ax[1], (10, 1500, -80, 2))):
    x.plot(f, a.s_db[:, 1, 0], "tab:blue", lw=1.6, label="S21 parts")
    x.plot(f, b.s_db[:, 1, 0], "tab:purple", lw=1.6, label="S21 parts + layout")
    x.plot(f, a.s_db[:, 0, 0], "tab:red", lw=1, ls="--", label="S11 parts")
    x.plot(f, b.s_db[:, 0, 0], "tab:pink", lw=1, label="S11 parts + layout")
    x.axvspan(108, 174, color="tab:green", alpha=0.08)
    x.set_xlim(lim[:2]); x.set_ylim(lim[2:]); x.grid(alpha=0.3); x.set_xlabel("frequency (MHz)"); x.set_ylabel("dB")
ax[0].set_title("passband 108-174 MHz"); ax[1].set_title("10 MHz - 1.5 GHz")
ax[1].set_xscale("log"); ax[0].legend(fontsize=8, loc="lower center")
fig.suptitle("F6 2m/AIR: openEMS layout N-port (20 pad ports, copper as PEC sheet, no mask) + datasheet/s2p part models")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "out", "f6_layout.png"), dpi=110)
for nm, n in (("parts", a), ("parts+layout", b)):
    s21 = n.s_db[:, 1, 0]; ib = (f >= 108) & (f <= 174)
    i3 = f[s21 >= s21.max() - 3]
    print(f"{nm:13s} IL {-s21[ib].max():.2f}-{-s21[ib].min():.2f} dB, RL min {-n.s_db[ib, 0, 0].max():.1f} dB, "
          f"-3 dB {i3.min():.0f}-{i3.max():.0f} MHz, S21 at 88/300/600/1000 MHz: "
          + " / ".join(f"{s21[np.argmin(abs(f - q))]:.1f}" for q in (88, 300, 600, 1000)) + " dB")
