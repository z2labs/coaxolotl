#!/usr/bin/env python3
"""Filter bank circuit simulation (scikit-rf): every branch F0..F7 straight from the KiCad netlist, each L and C
with its real model - vendor Touchstone file when there is one, else the datasheet RLC model (Q, SRF, DCR, ESL, DF)
from models/parts.json - optionally with the PE42582 switches and the board's layout N-port from openEMS.

    python3 simu/bank_sim.py                     # nominal, all branches -> simu/out/
    python3 simu/bank_sim.py --mc 300            # + Monte Carlo over the part tolerances
    python3 simu/bank_sim.py --ideal             # ideal L/C (the design check of filters.py)
    python3 simu/bank_sim.py --set L401=680n     # what-if: change part values (repeatable)

Topology comes from the netlist, values from the schematic value field, models by MPN via jlc/lcsc_parts.csv.
"""
import argparse, csv, json, math, os, re, sys
import numpy as np
import skrf as rf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
KD = os.path.join(ROOT, "hardware", "kicad")
OUT = os.path.join(HERE, "out")
Z0 = 50.0


# ------------------------------------------------------------ netlist
def parse(s):
    toks = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', s)
    st = [[]]
    for t in toks:
        if t == "(":
            st.append([])
        elif t == ")":
            x = st.pop(); st[-1].append(x)
        else:
            st[-1].append(t[1:-1] if t.startswith('"') else t)
    return st[0][0]


def find(n, k):
    return [c for c in n if isinstance(c, list) and c and c[0] == k]


def find1(n, k):
    r = find(n, k)
    return r[0] if r else None


def load_netlist():
    net = parse(open(os.path.join(KD, "coaxolotl.net"), encoding="utf-8").read())
    comps, pins = {}, {}
    for c in find(find1(net, "components"), "comp"):
        ref = find1(c, "ref")[1]
        sp = find1(c, "sheetpath")
        comps[ref] = dict(value=find1(c, "value")[1], sheet=find1(sp, "names")[1].strip("/") if sp else "")
    for n in find(find1(net, "nets"), "net"):
        name = find1(n, "name")[1]
        for node in find(n, "node"):
            pins.setdefault(find1(node, "ref")[1], {})[find1(node, "pin")[1]] = name
    return comps, pins


UNIT = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0}


def value_of(s):
    """'5.6nF/50V/C0G' -> 5.6e-9, '10uH/0.45A(1210)' -> 1e-5, '0R/0603' -> 0"""
    s = s.split("/")[0]
    m = re.match(r"^([0-9.]+)([pnum]?)([FH]|R)?([0-9]*)$", s)
    if not m:
        raise ValueError(s)
    if m.group(3) == "R":
        return float(m.group(1) + ("." + m.group(4) if m.group(4) else ""))
    return float(m.group(1)) * UNIT[m.group(2)]


# ------------------------------------------------------------ part models
def load_parts():
    by_ref = {}
    for r in csv.DictReader(open(os.path.join(KD, "jlc", "lcsc_parts.csv"), encoding="utf-8")):
        for ref in r["refs"].split():
            by_ref[ref] = r
    models = {}
    p = os.path.join(HERE, "models", "parts.json")
    if os.path.exists(p):
        for m in json.load(open(p, encoding="utf-8")):
            models[m.get("mpn") or m["value"]] = m
    return by_ref, models


def z_part(kind, val, m, f, ideal=False):
    """impedance of one 2-terminal part over f"""
    w = 2 * np.pi * f
    if kind == "R":
        return np.full_like(f, max(val, 1e-3), dtype=complex)
    if ideal:
        return 1j * w * val if kind == "L" else 1 / (1j * w * val)
    if kind == "C":
        esl = m.get("esl_h") or 0.5e-9                       # 0603 MLCC typical when the maker gives none
        qc = [x["q"] for x in (m.get("q") or []) if x.get("q")]
        df = m.get("df") or (1.0 / min(qc) if qc else 1e-3)  # C0G: Q >= 1000 (DF <= 0.1 %) at 1 MHz
        esr = (m.get("esr_ohm") or 0.05) + df / (w * val)
        return esr + 1j * w * esl + 1 / (1j * w * val)
    # inductor: L + R(f) in series, the winding capacitance across (SRF)
    q = sorted(m.get("q") or [], key=lambda x: x["f_hz"])
    rdc = (m.get("rdc_ohm") or {}).get("typ") or (m.get("rdc_ohm") or {}).get("max") or 0.0
    if q:
        fq = np.array([x["f_hz"] for x in q]); qq = np.array([x["q"] for x in q])
        # R(f) from the Q points (log-log interpolation, sqrt(f) skin law outside them)
        rq = 2 * np.pi * fq * val / qq
        lr = np.interp(np.log(f), np.log(fq), np.log(rq))
        lr = np.where(f < fq[0], np.log(rq[0]) + 0.5 * (np.log(f) - np.log(fq[0])), lr)
        lr = np.where(f > fq[-1], np.log(rq[-1]) + 0.5 * (np.log(f) - np.log(fq[-1])), lr)
        r = np.maximum(np.exp(lr), rdc)
    else:
        r = np.maximum(rdc, w * val / 40.0)                  # no data: Q 40
    srf = (m.get("srf_hz") or {}).get("typ") or (m.get("srf_hz") or {}).get("min")
    zl = r + 1j * w * val
    if srf:
        cp = 1 / ((2 * np.pi * srf) ** 2 * val)
        zl = 1 / (1 / zl + 1j * w * cp)
    return zl


_S2P = {}


def z_s2p(path, f, m):
    """series impedance from a maker's series-mode Touchstone file; below its first point the part is
    extrapolated as L (from that point) + R ~ sqrt(f), not below the DC resistance"""
    if path not in _S2P:
        nt = rf.Network(os.path.join(HERE, "models", path))
        s21 = nt.s[:, 1, 0]
        _S2P[path] = (nt.f, 2 * Z0 * (1 - s21) / s21)
    fm, zm = _S2P[path]
    zr = np.interp(f, fm, zm.real); zi = np.interp(f, fm, zm.imag)
    lo = f < fm[0]
    if lo.any():
        l0 = zm[0].imag / (2 * np.pi * fm[0])
        rdc = ((m.get("rdc_ohm") or {}).get("typ") or (m.get("rdc_ohm") or {}).get("max") or 0.0) * 0.7
        zr = np.where(lo, np.maximum(zm[0].real * np.sqrt(f / fm[0]), rdc), zr)
        zi = np.where(lo, 2 * np.pi * f * l0, zi)
    return zr + 1j * zi


def series_ntwk(z, freq, name):
    s = np.zeros((len(freq.f), 2, 2), dtype=complex)
    s[:, 0, 0] = s[:, 1, 1] = z / (z + 2 * Z0)
    s[:, 0, 1] = s[:, 1, 0] = 2 * Z0 / (z + 2 * Z0)
    return rf.Network(frequency=freq, s=s, z0=Z0, name=name)


# ------------------------------------------------------------ branches
def branch_parts(k, comps, pins):
    """refs of the parts between U8 RF(k+1) and U11 RF(8-k) (everything reachable without crossing a switch or GND)"""
    start, end = f"Net-(U8-RF{k + 1})", f"Net-(U11-RF{8 - k})"
    bank = [r for r, c in comps.items() if c["sheet"] == "Filter bank" and r in pins and len(pins[r]) == 2
            and not c["value"].startswith("NA")]
    seen, todo, parts = {start}, [start], set()
    while todo:
        n = todo.pop()
        for r in bank:
            ns = set(pins[r].values())
            if n in ns and r not in parts:
                parts.add(r)
                for m in ns - {"GND"}:
                    if m not in seen:
                        seen.add(m); todo.append(m)
    assert end in seen, f"F{k}: no path"
    return start, end, sorted(parts)


def load_layout(d, freq):
    """openEMS N-port of one lane (lane_nport.py): ports on every part pad, in ports.json order.
    Returns (network on freq, ["REF.pad", ...]); below the simulated band the lowest point is held."""
    names = json.load(open(os.path.join(d, "ports.json"), encoding="utf-8"))["ports"]
    nt = rf.Network(os.path.join(d, next(f for f in os.listdir(d) if re.match(r".*\.s\d+p$", f))))
    f = np.clip(freq.f, nt.f[0], nt.f[-1])
    s = np.empty((len(f),) + nt.s.shape[1:], dtype=complex)
    for i in range(nt.nports):
        for j in range(nt.nports):
            s[:, i, j] = np.interp(f, nt.f, nt.s[:, i, j].real) + 1j * np.interp(f, nt.f, nt.s[:, i, j].imag)
    return rf.Network(frequency=freq, s=s, z0=nt.z0[0, 0], name="layout"), names


def build(k, comps, pins, by_ref, models, freq, ideal=False, vals=None, scale=None, layout=None):
    """layout: (N-port, ["REF.pad"]) - the parts then join the layout ports at their pads instead of the net nodes;
    the lane's ends are the pads on the switch nets."""
    start, end, parts = branch_parts(k, comps, pins)
    lports = {}
    if layout:
        lnt, lnames = layout
        lports = {nm: i for i, nm in enumerate(lnames)}
        missing = [f"{r}.{p}" for r in parts for p in pins[r] if f"{r}.{p}" not in lports]
        assert not missing, f"F{k}: layout has no port at {missing}"
    nodes = {}
    for r in parts:
        v = (vals or {}).get(r, value_of(comps[r]["value"]))
        if scale:
            v *= scale.get(r, 1.0)
        kind = "R" if r.startswith("R") else r[0]
        mpn = by_ref.get(r, {}).get("mpn", "")
        m = (models.get(mpn) if mpn else None) or models.get(comps[r]["value"]) or {}
        if not ideal and m.get("s2p") and not (vals and r in vals):
            nt = series_ntwk(z_s2p(m["s2p"], freq.f, m) * (scale.get(r, 1.0) if scale else 1.0), freq, r)
        else:
            nt = series_ntwk(z_part(kind, v, m, freq.f, ideal), freq, r)
        for p, n in sorted(pins[r].items()):
            nodes.setdefault(f"{r}.{p}" if lports else n, []).append((nt, int(p) - 1))
    p1, p2 = rf.circuit.Circuit.Port(freq, "P1", z0=Z0), rf.circuit.Circuit.Port(freq, "P2", z0=Z0)
    if lports:
        for nm, i in lports.items():
            nodes.setdefault(nm, []).append((lnt, i))
        pad_on = lambda net: next(f"{r}.{p}" for r in parts for p, n in sorted(pins[r].items()) if n == net)
        nodes[pad_on(start)].append((p1, 0)); nodes[pad_on(end)].append((p2, 0))
    else:
        nodes[start].append((p1, 0)); nodes[end].append((p2, 0))
    cnx = []
    for n, lst in nodes.items():
        if n == "GND":
            cnx.append(lst + [(rf.circuit.Circuit.Ground(freq, "GND", z0=Z0), 0)])
        else:
            cnx.append(lst)
    return rf.circuit.Circuit(cnx).network, parts


# tolerance per part for the Monte Carlo (uniform within +-tol)
def tol_of(ref, comps, by_ref, models):
    v = comps[ref]["value"]
    m = models.get(by_ref.get(ref, {}).get("mpn", ""), {})
    if m.get("tol"):
        t = str(m["tol"]).strip("%± ")
        try:
            return float(t) / 100
        except ValueError:
            pass
    mt = re.search(r"/(\d+)%", v)
    if mt:
        return float(mt.group(1)) / 100
    if ref.startswith("C"):
        c = value_of(v)
        return 0.25e-12 / c if c < 10e-12 else 0.05
    return 0.05


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ideal", action="store_true")
    ap.add_argument("--mc", type=int, default=0)
    ap.add_argument("--set", action="append", default=[], help="REF=value, e.g. L401=680n")
    ap.add_argument("--tag", default="")
    ap.add_argument("--layout", action="append", default=[], metavar="K=DIR",
                    help="branch K with the openEMS lane N-port in DIR, e.g. 6=openems/results/f6")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    comps, pins = load_netlist()
    by_ref, models = load_parts()
    vals = {}
    for s in a.set:
        r, v = s.split("=")
        vals[r] = value_of(v + ("H" if r.startswith("L") else "F"))
    bp = json.load(open(os.path.join(ROOT, "bandplan.json"), encoding="utf-8"))["branches"]
    freq = rf.Frequency(0.05, 2000, 2001, unit="MHz", sweep_type="log")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    lay, lay_dir, lay_sum = {}, {}, {}
    for s_ in a.layout:
        kk, d = s_.split("=", 1)
        d = d if os.path.isabs(d) else os.path.join(HERE, d)
        lay[int(kk)] = load_layout(d, freq); lay_dir[int(kk)] = os.path.relpath(d, HERE)
    fig, axs = plt.subplots(4, 2, figsize=(14, 15))
    summary = []
    for k in range(8):
        ax = axs[k // 2][k % 2]
        b = bp[k]
        lo, hi = b["lo_hz"], b["hi_hz"]
        ideal, parts = build(k, comps, pins, by_ref, models, freq, ideal=True)
        real, _ = build(k, comps, pins, by_ref, models, freq, ideal=a.ideal, vals=vals)
        if k in lay:
            lnet, _ = build(k, comps, pins, by_ref, models, freq, ideal=a.ideal, vals=vals, layout=lay[k])
            ax.semilogx(freq.f / 1e6, lnet.s_db[:, 1, 0], "tab:purple", lw=1.6, label="S21 parts + layout (openEMS)")
            ax.semilogx(freq.f / 1e6, lnet.s_db[:, 0, 0], "tab:pink", lw=1, label="S11 parts + layout")
            lnet.write_touchstone(os.path.join(OUT, f"F{k}{a.tag}_openems"))
            inb = (freq.f >= max(bp[k]["lo_hz"], 0.1e6)) & (freq.f <= bp[k]["hi_hz"])
            lay_sum[k] = dict(il_max_db=round(float(-lnet.s_db[inb, 1, 0].min()), 2),
                              il_min_db=round(float(-lnet.s_db[inb, 1, 0].max()), 2),
                              rl_min_db=round(float(-lnet.s_db[inb, 0, 0].max()), 1), layout=lay_dir[k])
            ax.legend(fontsize=8, loc="lower left")
        f = freq.f
        if a.mc:
            rng = np.random.default_rng(1)
            env = []
            for i in range(a.mc):
                sc = {r: 1 + rng.uniform(-1, 1) * tol_of(r, comps, by_ref, models) for r in parts if not r.startswith("R")}
                n, _ = build(k, comps, pins, by_ref, models, freq, vals=vals, scale=sc)
                env.append(n.s_db[:, 1, 0])
            env = np.array(env)
            ax.fill_between(f / 1e6, env.min(0), env.max(0), color="tab:orange", alpha=0.25, label=f"MC {a.mc}x tolerance")
        ax.semilogx(f / 1e6, ideal.s_db[:, 1, 0], "k:", lw=1, label="ideal L/C")
        ax.semilogx(f / 1e6, real.s_db[:, 1, 0], "tab:blue", lw=1.6, label="S21 part models")
        ax.semilogx(f / 1e6, real.s_db[:, 0, 0], "tab:red", lw=1, label="S11 part models")
        ax.axvspan(max(lo, 0.05e6) / 1e6, hi / 1e6, color="tab:green", alpha=0.08)
        ax.set_ylim(-80, 2); ax.set_xlim(0.05, 2000); ax.grid(True, which="both", alpha=0.3)
        ax.set_title(f"F{k} {b.get('name', '')}  ({len(parts)} parts)", fontsize=10)
        ax.set_xlabel("MHz"); ax.set_ylabel("dB")
        inb = (f >= max(lo, 0.1e6)) & (f <= hi)
        il = -real.s_db[inb, 1, 0]; rl = -real.s_db[inb, 0, 0]
        summary.append(dict(branch=f"F{k}", band_mhz=[lo / 1e6, hi / 1e6], il_max_db=round(float(il.max()), 2),
                            il_min_db=round(float(il.min()), 2), rl_min_db=round(float(rl.min()), 1)))
        if k in lay_sum:
            summary[-1]["with_layout"] = lay_sum[k]
        real.write_touchstone(os.path.join(OUT, f"F{k}{a.tag}"))
        if k == 0:
            ax.legend(fontsize=8, loc="lower left")
    fig.suptitle("Coaxolotl filter bank - " + ("ideal" if a.ideal else "part models") +
                 (f", Monte Carlo {a.mc}x" if a.mc else "") + (" " + " ".join(a.set) if a.set else ""), fontsize=12)
    fig.tight_layout()
    png = os.path.join(OUT, f"bank{a.tag}.png")
    fig.savefig(png, dpi=110)
    json.dump(summary, open(os.path.join(OUT, f"summary{a.tag}.json"), "w"), indent=1)
    for s in summary:
        print(s)
    print("->", png)


if __name__ == "__main__":
    main()
