#!/usr/bin/env python3
"""LC filter design for the 8-way filter bank (F1-F7; F0 is a 0R link).

5th-order Chebyshev, 0.1 dB ripple, 50 ohm, values rounded to E12.
  LPF: pi  (shunt C, series L, shunt C, series L, shunt C)
  HPF: T   (series C, shunt L, series C, shunt L, series C)
  BPF: HPF then LPF (the bands are wide: f_hi / f_lo >= 2.5), cut-offs
       set a little outside the band edges so the ripple band covers it.

    python3 hardware/kicad/tools/filters.py      # table + check of the rounded values
"""
import math

Z0 = 50.0
G = [1.1468, 1.3712, 1.9750, 1.3712, 1.1468]          # Chebyshev 0.1 dB, n = 5
E12 = [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2]

# branch k (1..7): (name, hpf_fc_Hz or None, lpf_fc_Hz or None) - matches bandplan.json
BRANCHES = {
    1: ("F1 LPF 550 kHz", None, 600e3),
    2: ("F2 BPF 1.6-4 MHz", 1.3e6, 4.5e6),
    3: ("F3 BPF 4-12 MHz", 3.7e6, 13e6),
    4: ("F4 BPF 12-30 MHz", 11e6, 34e6),
    5: ("F5 BPF 30-80 MHz", 27e6, 80e6),
    6: ("F6 BPF 108-174 MHz", 100e6, 185e6),
    7: ("F7 HPF 380 MHz", 360e6, None),
}


def e12(x):
    d = math.floor(math.log10(x))
    m = x / 10 ** d
    best = min(E12 + [10.0], key=lambda e: abs(math.log(m / e)))
    return best * 10 ** d


def lpf(fc):
    w = 2 * math.pi * fc
    out = []
    for i, g in enumerate(G):
        out.append(("P", "C", g / (w * Z0)) if i % 2 == 0 else ("S", "L", g * Z0 / w))
    return out


def hpf(fc):
    w = 2 * math.pi * fc
    out = []
    for i, g in enumerate(G):
        out.append(("S", "C", 1 / (w * Z0 * g)) if i % 2 == 0 else ("P", "L", Z0 / (w * g)))
    return out


def design(k):
    """list of (topo 'S'/'P', kind 'C'/'L', exact value, E12 value)"""
    _, fh, fl = BRANCHES[k]
    els = (hpf(fh) if fh else []) + (lpf(fl) if fl else [])
    return [(t, kind, v, e12(v)) for t, kind, v in els]


def fmt(kind, v):
    if kind == "C":
        for unit, s in (("uF", 1e-6), ("nF", 1e-9), ("pF", 1e-12)):
            if v >= s * 0.999:
                n = v / s
                return (f"{n:.2g}" if n < 10 else f"{n:.0f}").replace(".", unit[0]) + ("" if "." in f"{n:.2g}" and n < 10 else unit) if False else _num(n) + unit
        return _num(v / 1e-12) + "pF"
    for unit, s in (("mH", 1e-3), ("uH", 1e-6), ("nH", 1e-9)):
        if v >= s * 0.999:
            return _num(v / s) + unit
    return _num(v / 1e-9) + "nH"


def _num(n):
    s = f"{n:.2g}" if n < 10 else f"{n:.0f}"
    return s


def part_value(kind, v):
    """schematic Value with the critical parameters; footprint by value"""
    if kind == "C":
        fp = "0805" if v > 10e-9 else "0603"
        return f"{fmt('C', v)}/50V/C0G", "Capacitor_SMD:C_%s_%sMetric" % (fp, {"0603": "1608", "0805": "2012"}[fp])
    if v > 10e-6:
        size, met = "1210", "3225"
    elif v > 470e-9:
        size, met = "0805", "2012"
    else:
        size, met = "0603", "1608"
    return f"{fmt('L', v)}/5%", f"Inductor_SMD:L_{size}_{met}Metric"


def response_db(k, f, rounded=True):
    """|S21| in dB of the rounded ladder between 50 ohm terminations (ABCD)."""
    w = 2 * math.pi * f
    A, B, C, D = 1, 0, 0, 1
    for t, kind, v, vr in design(k):
        val = vr if rounded else v
        z = (1j * w * val) if kind == "L" else 1 / (1j * w * val)
        if t == "S":
            m = (1, z, 0, 1)
        else:
            m = (1, 0, 1 / z, 1)
        A, B, C, D = (A * m[0] + B * m[2], A * m[1] + B * m[3], C * m[0] + D * m[2], C * m[1] + D * m[3])
    s21 = 2 / (A + B / Z0 + C * Z0 + D)
    return 20 * math.log10(abs(s21))


if __name__ == "__main__":
    import json, pathlib
    bp = json.loads((pathlib.Path(__file__).resolve().parents[3] / "bandplan.json").read_text())["branches"]
    for k in BRANCHES:
        name, fh, fl = BRANCHES[k]
        b = bp[k]
        print(f"{name}: " + "  ".join(f"{t}{kind} {part_value(kind, vr)[0]}" for t, kind, v, vr in design(k)))
        lo, hi = b["lo_hz"], b["hi_hz"]
        pts = [lo, math.sqrt(lo * hi) if lo else hi / 2, hi]
        il = [response_db(k, f) for f in pts]
        rej = []
        for f in (lo / 2 if lo > 40e3 else None, hi * 2 if hi < 1.5e9 else None, 98e6):
            if f and not (lo <= f <= hi):
                rej.append(f"{f/1e6:g} MHz {response_db(k, f):.0f} dB")
        print(f"   pass {lo/1e6:g}/{pts[1]/1e6:.3g}/{hi/1e6:g} MHz: " +
              " / ".join(f"{x:.2f}" for x in il) + " dB;  stop: " + ", ".join(rej))
