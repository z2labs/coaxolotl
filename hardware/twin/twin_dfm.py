"""Layer 5: manufacturability - the board against the fab house's own DFM checks (JLCPCB).

Sources (source documents only, as for every twin layer):
  [JLC-CAP]  jlcpcb.com/capabilities/pcb-capabilities (2026-10-27): silkscreen to pad 0.15 mm, silk line >= 0.15 mm,
             PTH annular ring multilayer min 0.15 mm, via hole >= 0.15 / dia >= 0.25 mm, SMD pad to pad 0.15 mm.
  [JLC-DFM]  JLCDFM report on the rev 0.2 Gerbers (2026-10-27 16:24): flagged as Danger - via to pad 0 mm, pad spacing
             0.04 mm, slot width 0.6 mm, THT to SMD 1.69 mm, silkscreen to pad 0 mm, silkscreen to hole 0 mm;
             Warning - annular ring 0.12 mm, short slot (length < 2 x width).
KiCad's DRC passes all of these: it checks clearance between different nets only (a via ring on a pad of its own net
is legal copper), round-hole minimums only (not slot width), and silkscreen as a warning. JLC checks the Gerbers.

Checks (what JLC sees: copper, holes, silk after --subtract-soldermask):
  via-pad     via ring to any SMD pad (own net too):   FAIL < 0.10 mm, WARN < 0.15 mm      [JLC-DFM via to pad]
  pad-pad     pads of different parts on one layer:    FAIL < 0.10 mm                       [JLC-DFM pad spacing]
  slot        plated slot width:                       FAIL < 0.65 mm; WARN length < 2 x width [JLC-DFM]
  silk-pad    silk to a solder-mask opening:           FAIL < 0.15 mm                       [JLC-CAP] [JLC-DFM]
  silk-hole   silk to any drilled hole (vias included): FAIL < 0.15 mm                      [JLC-DFM silkscreen to hole]
  silk-width  silk stroke:                             WARN < 0.15 mm                       [JLC-CAP]
  ring        via annular ring:                        WARN < 0.15 mm                       [JLC-CAP] [JLC-DFM]
  tht-smd     THT pad to an SMD pad of another part:   WARN < 2.0 mm (assembly / selective solder) [JLC-DFM]

usage: kicad-python twin_dfm.py board.kicad_pcb [--selftest] [-q] [--list]     exit 0 / 1 FAIL / 2 input / 3 selftest
       kicad-python twin_dfm.py board.kicad_pcb --fix-silk      (flow step after silk_dfm --fix; the check never edits)
"""
import os, sys, math, copy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from twin_common import Report
except ImportError:                     # standalone (srouter tools/jlc_dfm_check.py)
    class Report:
        def __init__(self, title):
            self.title, self.rows = title, []
        def add(self, sev, group, msg):
            self.rows.append((sev, group, msg))
        def fails(self):
            return [r for r in self.rows if r[0] == "FAIL"]
        def show(self, quiet_pass=False):
            print(f"== {self.title}")
            for s, g, m in self.rows:
                if not (quiet_pass and s == "PASS"):
                    print(f"  {s:4} [{g}] {m}")
            n = {k: sum(1 for r in self.rows if r[0] == k) for k in ("PASS", "WARN", "FAIL")}
            print(f"  -> {n['PASS']} pass, {n['WARN']} warn, {n['FAIL']} FAIL")

VIA_PAD_FAIL, VIA_PAD_WARN = 0.10, 0.15
PAD_PAD_FAIL = 0.10
SLOT_MIN = 0.65
SILK_PAD, SILK_HOLE, SILK_W = 0.15, 0.15, 0.15
RING_MIN = 0.15
THT_SMD = 2.0
ERR = 0.005          # mm polygon approximation


# ------------------------------------------------------------------ geometry (plain python, mm)
def seg_pt(p, a, b):
    ax, ay = b[0] - a[0], b[1] - a[1]; L = ax * ax + ay * ay
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / L))
    return math.hypot(p[0] - a[0] - ax * t, p[1] - a[1] - ay * t)


def seg_seg(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if ((o1 > 0) != (o2 > 0)) and ((o3 > 0) != (o4 > 0)) and o1 and o2 and o3 and o4:
        return 0.0
    return min(seg_pt(a, c, d), seg_pt(b, c, d), seg_pt(c, a, b), seg_pt(d, a, b))


def inside(p, poly):
    c = False; n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]:
            c = not c
    return c


def poly_pt(p, poly):
    """distance from point to polygon area (0 inside)"""
    if inside(p, poly):
        return 0.0
    return min(seg_pt(p, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))


def poly_poly(P, Q):
    if inside(P[0], Q) or inside(Q[0], P):
        return 0.0
    best = 1e9
    for i in range(len(P)):
        a, b = P[i], P[(i + 1) % len(P)]
        for j in range(len(Q)):
            best = min(best, seg_seg(a, b, Q[j], Q[(j + 1) % len(Q)]))
            if best == 0:
                return 0.0
    return best


def bbox(poly, m=0.0):
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    return (min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)


def bb_hit(a, b):
    return not (a[0] > b[2] or a[2] < b[0] or a[1] > b[3] or a[3] < b[1])


# ------------------------------------------------------------------ board -> geometry
def extract(path, board=None):
    import pcbnew
    mm = pcbnew.ToMM; FM = pcbnew.FromMM
    b = board or pcbnew.LoadBoard(path)

    def polys(item, layer, clr=0.0):
        ps = pcbnew.SHAPE_POLY_SET()
        item.TransformShapeToPolygon(ps, layer, FM(clr), FM(ERR), pcbnew.ERROR_INSIDE)
        out = []
        for i in range(ps.OutlineCount()):
            o = ps.Outline(i)
            out.append([(mm(o.CPoint(j).x), mm(o.CPoint(j).y)) for j in range(o.PointCount())])
        return [p for p in out if len(p) >= 3]

    g = dict(vias=[], pads=[], holes=[], slots=[], silk=[], mask=[])
    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA":
            c = (mm(t.GetPosition().x), mm(t.GetPosition().y))
            g["vias"].append(dict(c=c, r=mm(t.GetWidth(pcbnew.F_Cu)) / 2, d=mm(t.GetDrillValue()), net=t.GetNetname()))
            g["holes"].append((c, mm(t.GetDrillValue()) / 2, "via " + t.GetNetname()))
    for fp in b.GetFootprints():
        ref = fp.GetReference()
        for p in fp.Pads():
            attr = p.GetAttribute()
            kind = {pcbnew.PAD_ATTRIB_SMD: "smd", pcbnew.PAD_ATTRIB_PTH: "pth", pcbnew.PAD_ATTRIB_NPTH: "npth"}.get(attr, "other")
            c = (mm(p.GetPosition().x), mm(p.GetPosition().y))
            dr = p.GetDrillSize()
            if kind in ("pth", "npth"):
                w, l = sorted((mm(dr.x), mm(dr.y)))
                g["holes"].append((c, w / 2, f"{ref}.{p.GetNumber()}"))      # a slot counts by its width at the centre
                if w != l and kind == "pth":
                    g["slots"].append(dict(name=f"{ref}.{p.GetNumber()}", w=w, l=l))
            for lay, side in ((pcbnew.F_Cu, "F"), (pcbnew.B_Cu, "B")):
                if kind != "npth" and p.IsOnLayer(lay):
                    for q in polys(p, lay):
                        g["pads"].append(dict(ref=ref, num=p.GetNumber(), kind=kind, side=side, net=p.GetNetname(), poly=q, bb=bbox(q)))
            for lay, side in ((pcbnew.F_Mask, "F"), (pcbnew.B_Mask, "B")):
                if p.IsOnLayer(lay):
                    for q in polys(p, lay):
                        g["mask"].append(dict(name=f"{ref}.{p.GetNumber()}", side=side, poly=q, bb=bbox(q)))
    items = list(b.GetDrawings())
    for fp in b.GetFootprints():
        items += list(fp.GraphicalItems()) + [fp.Reference(), fp.Value()]
    for it in items:
        for lay, side in ((pcbnew.F_SilkS, "F"), (pcbnew.B_SilkS, "B")):
            if it.GetLayer() != lay:
                continue
            if hasattr(it, "IsVisible") and not it.IsVisible():
                continue
            par = it.GetParentFootprint() if hasattr(it, "GetParentFootprint") else None
            name = (par.GetReference() + ":" if par else "") + it.GetClass().replace("PCB_", "").lower()
            w = mm(it.GetWidth()) if hasattr(it, "GetWidth") and it.GetClass() == "PCB_SHAPE" else \
                (mm(it.GetTextThickness()) if hasattr(it, "GetTextThickness") else None)
            for q in polys(it, lay):
                g["silk"].append(dict(name=name, side=side, poly=q, bb=bbox(q), w=w, obj=it))
    return g


# ------------------------------------------------------------------ checks
def check(g, listing=False):
    rep = Report("layer 5: manufacturability (JLC DFM)")
    smd = [p for p in g["pads"] if p["kind"] == "smd"]

    def group(name, bad, warn, total, fmt):
        if bad:
            rep.add("FAIL", name, f"{len(bad)} of {total}: " + "; ".join(fmt(x) for x in bad[:6]) + (" ..." if len(bad) > 6 else ""))
            if listing:
                for x in bad:
                    print("   ", name, fmt(x))
        if warn:
            rep.add("WARN", name, f"{len(warn)} of {total}: " + "; ".join(fmt(x) for x in warn[:4]) + (" ..." if len(warn) > 4 else ""))
        if not bad and not warn:
            rep.add("PASS", name, f"{total} checked")

    # via ring to SMD pads
    bad, warn = [], []
    for v in g["vias"]:
        m = v["r"] + VIA_PAD_WARN
        q = (v["c"][0] - m, v["c"][1] - m, v["c"][0] + m, v["c"][1] + m)
        gap = min([poly_pt(v["c"], p["poly"]) - v["r"] for p in smd if bb_hit(q, p["bb"])] or [9])
        if gap < VIA_PAD_FAIL:
            bad.append((v, gap))
        elif gap < VIA_PAD_WARN:
            warn.append((v, gap))
    group("via-pad", bad, warn, len(g["vias"]), lambda x: f"{x[0]['net']} ({x[0]['c'][0]:.2f},{x[0]['c'][1]:.2f}) {x[1]:.2f} mm")

    # pads of different parts on the same side
    bad = []
    P = g["pads"]
    for i in range(len(P)):
        a = P[i]; qa = (a["bb"][0] - PAD_PAD_FAIL, a["bb"][1] - PAD_PAD_FAIL, a["bb"][2] + PAD_PAD_FAIL, a["bb"][3] + PAD_PAD_FAIL)
        for j in range(i + 1, len(P)):
            c = P[j]
            if c["ref"] == a["ref"] or c["side"] != a["side"] or not bb_hit(qa, c["bb"]):
                continue
            d = poly_poly(a["poly"], c["poly"])
            if d < PAD_PAD_FAIL:
                bad.append((a, c, d))
    group("pad-pad", bad, [], len(P), lambda x: f"{x[0]['ref']}.{x[0]['num']}-{x[1]['ref']}.{x[1]['num']} {x[2]:.2f} mm")

    # slots
    bad = [s for s in g["slots"] if s["w"] < SLOT_MIN - 1e-6]
    warn = [s for s in g["slots"] if s["w"] >= SLOT_MIN - 1e-6 and s["l"] < 2 * s["w"]]
    group("slot", bad, warn, len(g["slots"]), lambda s: f"{s['name']} {s['w']:.2f} x {s['l']:.2f} mm")

    # silk to mask openings and to holes
    bad = []
    for s in g["silk"]:
        q = (s["bb"][0] - SILK_PAD, s["bb"][1] - SILK_PAD, s["bb"][2] + SILK_PAD, s["bb"][3] + SILK_PAD)
        for o in g["mask"]:
            if o["side"] == s["side"] and bb_hit(q, o["bb"]):
                d = poly_poly(s["poly"], o["poly"])
                if d < SILK_PAD:
                    bad.append((s, o["name"], d)); s["bad"] = True; break
    group("silk-pad", bad, [], len(g["silk"]), lambda x: f"{x[0]['side']}.Silk {x[0]['name']} - {x[1]} {x[2]:.2f} mm")
    bad = []
    for s in g["silk"]:
        for (c, r, name) in g["holes"]:
            m = r + SILK_HOLE
            if not bb_hit((c[0] - m, c[1] - m, c[0] + m, c[1] + m), s["bb"]):
                continue
            d = poly_pt(c, s["poly"]) - r
            if d < SILK_HOLE:
                bad.append((s, name, d)); s["bad"] = True; break
    group("silk-hole", bad, [], len(g["silk"]), lambda x: f"{x[0]['side']}.Silk {x[0]['name']} - hole {x[1]} {max(x[2], 0):.2f} mm")
    warn = [s for s in g["silk"] if s["w"] is not None and 0 < s["w"] < SILK_W - 1e-6]
    group("silk-width", [], warn, len(g["silk"]), lambda s: f"{s['name']} {s['w']:.2f} mm")

    # via annular ring
    warn = [v for v in g["vias"] if v["r"] - v["d"] / 2 < RING_MIN - 1e-6]
    group("ring", [], warn, len(g["vias"]), lambda v: f"{v['net']} {2*v['r']:.2f}/{v['d']:.2f} mm")

    # THT pad to SMD pads of other parts
    warn = []
    tht = [p for p in g["pads"] if p["kind"] == "pth"]
    for t in tht:
        q = (t["bb"][0] - THT_SMD, t["bb"][1] - THT_SMD, t["bb"][2] + THT_SMD, t["bb"][3] + THT_SMD)
        for s in smd:
            if s["ref"] != t["ref"] and s["side"] == t["side"] and bb_hit(q, s["bb"]):
                d = poly_poly(t["poly"], s["poly"])
                if d < THT_SMD:
                    warn.append((t, s, d)); break
    group("tht-smd", [], warn, len(tht), lambda x: f"{x[0]['ref']}.{x[0]['num']}-{x[1]['ref']} {x[2]:.2f} mm")
    return rep


def fix_silk(path):
    """last silk step after silk_dfm --fix: silk items that still come within 0.15 mm of a mask opening or a drilled hole
    (measured on the real pad outline, not its bounding box) go to the Fab layer of their side - kept as documentation,
    not printed."""
    import pcbnew
    b = pcbnew.LoadBoard(path)
    g = extract(path, b)
    check(g)
    n = 0
    for s in g["silk"]:
        if s.get("bad"):
            s["obj"].SetLayer(pcbnew.F_Fab if s["side"] == "F" else pcbnew.B_Fab); n += 1
            print(f"  to Fab: {s['side']}.Silk {s['name']}")
    pcbnew.SaveBoard(path, b)
    print(f"dfm silk fix: {n} silk items moved to Fab")


def selftest(g):
    def nfail(rep, grp):
        return sum(int(m.split(" of ")[0]) for s, gr, m in rep.rows if s == "FAIL" and gr == grp)
    for s in g["silk"]:
        s.pop("obj", None)                  # pcbnew objects do not deep-copy
    base = check(g)
    st = Report("selftest: injected faults")
    p = next(p for p in g["pads"] if p["kind"] == "smd")
    g1 = copy.deepcopy(g); g1["vias"].append(dict(c=p["poly"][0], r=0.225, d=0.2, net=p["net"]))
    g2 = copy.deepcopy(g); g2["slots"].append(dict(name="J301.S1", w=0.6, l=1.7))
    v = g["holes"][0]
    sq = [(v[0][0] - 1, v[0][1] - 0.05), (v[0][0] + 1, v[0][1] - 0.05), (v[0][0] + 1, v[0][1] + 0.05), (v[0][0] - 1, v[0][1] + 0.05)]
    g3 = copy.deepcopy(g); g3["silk"].append(dict(name="inject", side="F", poly=sq, bb=bbox(sq), w=0.15))
    o = g["mask"][0]["poly"]
    sq2 = [(x + 0.05, y) for x, y in o[:3]]
    g4 = copy.deepcopy(g); g4["silk"].append(dict(name="inject", side=g["mask"][0]["side"], poly=sq2, bb=bbox(sq2), w=0.15))
    q = next(x for x in g["pads"] if x["kind"] == "smd" and x["ref"] != p["ref"])
    shift = (p["poly"][0][0] - q["poly"][0][0] + 0.03, p["poly"][0][1] - q["poly"][0][1])
    moved = [(x + shift[0], y + shift[1]) for x, y in q["poly"]]
    g5 = copy.deepcopy(g); g5["pads"].append(dict(q, side=p["side"], poly=moved, bb=bbox(moved)))
    for name, gg, grp in (("via on an SMD pad", g1, "via-pad"), ("0.6 mm plated slot", g2, "slot"),
                          ("silk line across a hole", g3, "silk-hole"), ("silk on a pad", g4, "silk-pad"),
                          ("two parts' pads 0.03 mm apart", g5, "pad-pad")):
        hit = nfail(check(gg), grp) > nfail(base, grp)
        st.add("PASS" if hit else "FAIL", "inject", f"{name}: {'caught' if hit else 'MISSED'} ({grp})")
    return st


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args or not os.path.exists(args[0]):
        print(__doc__); sys.exit(2)
    if "--fix-silk" in sys.argv:
        fix_silk(args[0]); sys.exit(0)
    g = extract(args[0])
    r = check(g, "--list" in sys.argv); r.show("-q" in sys.argv)
    code = 1 if r.fails() else 0
    if "--selftest" in sys.argv:
        st = selftest(g); st.show()
        if st.fails():
            code = 3
    sys.exit(code)
