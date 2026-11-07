"""Layer 4 - mechanics of the control board, from the .kicad_pcb text (no pcbnew needed).

Sources
  [DOC-HW]    docs/hardware.md: 100 x 70 mm, two layers, every part on top; RTL-SDR SMA J3 (edge launch) on the right
              edge; lamp strip with a snap-off tab; J6 a 1x10 2.54 mm hole row
  [DOC-PANEL] docs/panel.md: the strip carries the 48 lamps on six registers
  [ISO-7045]  M3 pan head diameter 5.6 mm max -> 3.0 mm keep-out radius around a 3.2 mm mounting hole
  [HDR]       2.54 mm pin headers (J1, J4, J5, J6): pitch 2.54 mm on one line

usage: twin_mech.py <board.kicad_pcb> [--selftest] [-q]       exit 0 / 1 FAIL / 2 input / 3 selftest
"""
import copy, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from twin_common import PcbFile, Report
import spec_ctrl as S

W, HGT = 100.0, 70.0
HEAD_R = 5.6 / 2 + 0.2          # [ISO-7045] + 0.2 mm
PITCH = 2.54


def outline(B):
    xs = [p[0] for e in B.edges for p in e]; ys = [p[1] for e in B.edges for p in e]
    return min(xs), min(ys), max(xs), max(ys)


def slot(B):
    """the routed break slot: the horizontal Edge.Cuts pair inside the outline"""
    x0, y0, x1, y1 = outline(B)
    ys = sorted({e[0][1] for e in B.edges if e[0][1] == e[1][1] and y0 < e[0][1] < y1})
    return (ys[0], ys[-1]) if len(ys) >= 2 else None


def pad_box(B, rp):
    (x, y), (w, h) = B.padxy[rp], B.padsize[rp]
    return x - w / 2, y - h / 2, x + w / 2, y + h / 2


def check(B):
    rep = Report("twin_mech - control board, layer 4 (outline, strip, connectors, holes)")
    ok = lambda c, g, m: rep.add("PASS" if c else "FAIL", g, m)
    x0, y0, x1, y1 = outline(B)
    ok(abs(x1 - x0 - W) < 0.05 and abs(y1 - y0 - HGT) < 0.05, "outline", f"{x1 - x0:.2f} x {y1 - y0:.2f} mm ({W:g} x {HGT:g})")
    bottom = sorted(r for r, l in B.layer.items() if l != "F.Cu")
    ok(not bottom, "sides", "every part on top" if not bottom else f"parts on the bottom: {bottom}")
    out = sorted({r for (r, n), (x, y) in B.padxy.items()
                  if not (x0 <= x <= x1 and y0 <= y <= y1) and not r.startswith("MB")})
    ok(not out, "outline", "every pad inside the outline" if not out else f"pads outside: {out}")
    # ---- snap-off strip
    sl = slot(B)
    if not sl:
        rep.add("FAIL", "strip", "no break slot in Edge.Cuts")
    else:
        top, bot = sl
        strip_nets = {B.pads[k] for k in B.pads if k[0] in S.STRIP_CORE and B.pads[k]}
        wrong_up = sorted(r for r in S.STRIP_CORE if r in B.at and B.at[r][1] > top)
        ok(not wrong_up, "strip", f"registers, arrays and lamps above the slot (y < {top})" if not wrong_up else
           f"strip parts below the slot: {wrong_up}")
        main = sorted(r for r, pp in _parts(B).items() if r not in S.STRIP_CORE and r not in ("J4", "J5")
                      and not r.startswith("MB") and any(n and n not in strip_nets for n in pp.values())
                      and B.at[r][1] < bot)
        ok(not main, "strip", "main-board parts below the slot" if not main else f"main-board parts on the strip: {main}")
        j4, j5 = B.at.get("J4"), B.at.get("J5")
        ok(bool(j4 and j5) and j5[1] < top and j4[1] > bot, "strip",
           f"link J5 on the strip (y {j5[1]:.2f}), J4 on the main board (y {j4[1]:.2f})" if j4 and j5 else "J4/J5 missing")
        if j4 and j5:
            off = [k for k in range(1, 8) if abs(B.padxy[("J4", str(k))][0] - B.padxy[("J5", str(k))][0]) > 0.01]
            ok(not off, "strip", "J4 pin k sits straight below J5 pin k (a straight 1:1 link)" if not off else
               f"J4/J5 pins not aligned: {off}")
    # ---- headers on a 2.54 mm line
    for ref, n in (("J1", 4), ("J4", 7), ("J5", 7), ("J6", 10)):
        pts = [B.padxy.get((ref, str(k))) for k in range(1, n + 1)]
        if None in pts:
            rep.add("FAIL", "header", f"{ref}: pads missing"); continue
        d = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
        line = all(abs((b[0] - a[0]) * (pts[-1][1] - pts[0][1]) - (b[1] - a[1]) * (pts[-1][0] - pts[0][0])) < 0.05
                   for a, b in zip(pts, pts[1:]))
        ok(all(abs(x - PITCH) < 0.01 for x in d) and line, "header",
           f"{ref}: {n} pins at {min(d):.3f}-{max(d):.3f} mm on one line")
    # ---- J3 edge launch
    if ("J3", "1") in B.padxy:
        cy = B.padxy[("J3", "1")][1]
        gaps = [e for e in B.edges if abs(e[0][0] - x1) < 0.01 and abs(e[1][0] - x1) < 0.01]
        ys = sorted(y for e in gaps for y in (e[0][1], e[1][1]))
        notch = [(a, b) for a, b in zip(ys, ys[1:]) if b - a > 0.1 and not any(min(e[0][1], e[1][1]) <= a + 0.01 and
                 max(e[0][1], e[1][1]) >= b - 0.01 for e in gaps)]
        ok(len(notch) == 1 and abs(sum(notch[0]) / 2 - cy) < 0.05, "J3",
           f"right-edge notch {notch} centred on the SMA centre pin (y {cy:.2f})")
        gnd = [pad_box(B, k) for k in B.padxy if k[0] == "J3" and k[1] != "1"]
        reach = max(b[2] for b in gnd) if gnd else 0
        ok(abs(reach - x1) < 0.3, "J3", f"SMA ground lands reach the board edge ({x1 - reach:.2f} mm short)")
    else:
        rep.add("FAIL", "J3", "J3.1 missing")
    # ---- mounting holes
    mh = [h for h in B.holes if h[3] >= 3.0]
    ok(len(mh) >= 4, "holes", f"{len(mh)} M3 holes")
    near = []
    for (hr, hx, hy, d) in mh:
        for rp in B.padxy:
            if rp[0] == hr:
                continue
            bx0, by0, bx1, by1 = pad_box(B, rp)
            dx, dy = max(bx0 - hx, 0, hx - bx1), max(by0 - hy, 0, hy - by1)
            if math.hypot(dx, dy) < HEAD_R:
                near.append(f"{rp[0]}.{rp[1]} {math.hypot(dx, dy):.2f} mm from {hr}")
    ok(not near, "holes", f"no pad under an M3 head (r {HEAD_R:.1f} mm)" if not near else "under an M3 head: " + "; ".join(near[:6]))
    return rep


def _parts(B):
    out = {}
    for (r, n), net in B.pads.items():
        out.setdefault(r, {})[n] = net
    return out


def _move(B, ref, dx, dy):
    x, y, a = B.at[ref]; B.at[ref] = (x + dx, y + dy, a)
    for k in list(B.padxy):
        if k[0] == ref:
            B.padxy[k] = (B.padxy[k][0] + dx, B.padxy[k][1] + dy)


WITNESSES = [
    ("lamp register below the slot", lambda B: _move(B, "U16", 0, 15), "strip parts below"),
    ("MCU on the strip", lambda B: _move(B, "U5", 0, -40), "main-board parts on the strip"),
    ("SMA 1 mm inboard", lambda B: _move(B, "J3", -1, 0), "ground lands"),
    ("SMA 0.5 mm off its notch", lambda B: _move(B, "J3", 0, 0.5), "notch"),
    ("J6 pin 4 off pitch", lambda B: B.padxy.__setitem__(("J6", "4"), (B.padxy[("J6", "4")][0] + 0.2,
                                                                       B.padxy[("J6", "4")][1])), "J6"),
    ("J5 shifted 1 mm", lambda B: _move(B, "J5", 1, 0), "not aligned"),
    ("part under a screw head", lambda B: _move(B, "R1", B.holes[[h[3] for h in B.holes].index(3.2)][1] + 2 - B.at["R1"][0],
                                                B.holes[[h[3] for h in B.holes].index(3.2)][2] - B.at["R1"][1]), "M3"),
    ("part on the bottom", lambda B: B.layer.__setitem__("C6", "B.Cu"), "bottom"),
]


def selftest(B0):
    base = {m for s, g, m in check(B0).rows if s == "FAIL"}
    missed = []
    for name, mut, key in WITNESSES:
        B = copy.deepcopy(B0); mut(B)
        new = [m for s, g, m in check(B).rows if s == "FAIL" and m not in base]
        hit = [m for m in new if key in m]
        print(f"  {'caught' if hit else 'MISSED'}  {name}" + (f"  <- {hit[0][:110]}" if hit else f"  (new FAILs: {new[:2]})"))
        if not hit:
            missed.append(name)
    return missed


def main(argv):
    args = [a for a in argv if not a.startswith("-")]
    if not args or not os.path.exists(args[0]):
        print(__doc__); return 2
    B = PcbFile(args[0])
    rc = 0
    if "--selftest" in argv:
        print("== selftest (injected faults)")
        missed = selftest(B)
        print(f"  -> {len(WITNESSES) - len(missed)}/{len(WITNESSES)} caught")
        rc = 3 if missed else 0
    rep = check(B); rep.show(quiet_pass="-q" in argv)
    return rc or (1 if rep.fails() else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
