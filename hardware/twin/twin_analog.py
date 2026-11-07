"""Layer 3: the drawn copper against the currents and voltages it has to carry.

Ampacity is checked as a max-flow through the real copper of each high-current net - tracks, pads AND zone fills
(power pours) on every layer, rasterised at 0.2 mm, layers joined by vias (1.5 A per 0.3 mm drill, 25 um barrel,
conservative; scaled with the drill, since the barrel cross-section is pi * d * t) and by plated through-holes - between the pads where the current really enters and leaves (FLOWS).
Each copper cell carries k_layer * 0.2 mm, where k_layer = I / W_IPC(I, layer) is the current per mm of width that
IPC-2221 allows at the design current for a 20 K rise (1 oz outer = 35 um, 0.5 oz inner = 17.5 um, JLC 4-layer).
The 1 mm around a source or sink pad is the pad-entry neck: a component pin's own width is the part maker's rating.

Also: Pi 5 V IR drop at 5 A, set-points from the divider values (buck 5.0-5.25 V, boost), TAS5805M PVDD window,
LDO dissipation at a 20 V PD contract.

usage: kicad-python twin_analog.py board.kicad_pcb [--selftest]       exit 0 / 1 FAIL / 2 input / 3 selftest
"""
import os, sys, math, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from twin_common import Report, parse_value

DT = 20.0
CU = {"outer": 0.035, "inner": 0.0175}            # mm
RHO = 1.72e-5                                     # Ohm*mm (copper, 20 C)
VIA_A = 1.5                                       # A per 0.3 mm via
H = 0.2                                           # raster, mm
NECK = 1.0                                        # mm around source/sink pads


def ipc_amps(width_mm, outer):
    t = CU["outer" if outer else "inner"]
    a_mil2 = (width_mm / 0.0254) * (t / 0.0254)
    return (0.048 if outer else 0.024) * DT ** 0.44 * a_mil2 ** 0.725


def ipc_width(amps, outer):
    t = CU["outer" if outer else "inner"]
    a_mil2 = (amps / ((0.048 if outer else 0.024) * DT ** 0.44)) ** (1 / 0.725)
    return a_mil2 / (t / 0.0254) * 0.0254


T = lambda r, *p: [(r, x) for x in p]
FLOWS = [   # (net, source pads, sink pads, A): where the current enters and leaves the copper of that net
    ("/battery/CELL3", T("BT403", "1"), T("F401", "1"), 6.0),
    ("/battery/CELL0", T("BT401", "2"), T("J401", "1"), 6.0),
    ("/battery/CELL1", T("BT401", "1"), T("BT402", "2"), 6.0),
    ("/battery/CELL2", T("BT402", "1"), T("BT403", "2"), 6.0),
    ("+BATT", T("F401", "2"), T("U303", "3"), 2.8),                       # Pi buck input at 9 V, 5.1 V / 5 A
    ("+BATT", T("F401", "2"), T("U201", "15", "16", "27", "28"), 2.5),    # amp 1 PVDD
    ("+BATT", T("F401", "2"), T("R344", "1"), 2.5),                       # amp 2 PVDD via the 0R select link
    ("+BATT", T("R304", "2"), T("F401", "2"), 3.5),                       # charging current into the pack
    ("+5V_BUCK", T("L302", "2"), T("Q101", "5"), 5.0),
    ("+5V", T("Q101", "1", "2", "3"), T("J101", "2", "4"), 5.0),
    ("/power/BUCK_SW", T("U303", "2"), T("L302", "1"), 5.0),
    ("VBUS", T("J301", "A4", "A9", "B4", "B9"), T("R305", "1"), 3.0),
    ("/power/CHG_VIN", T("R305", "2"), T("R302", "1"), 3.0),
    ("/power/CHG_NODEB", T("R302", "2"), T("Q301", "5"), 3.0),
    ("/power/CHG_LX1", T("Q301", "1", "2", "3"), T("L301", "1"), 3.5),
    ("/power/CHG_LX2", T("L301", "2"), T("Q303", "1", "2", "3"), 3.5),
    ("/power/CHG_NODEC", T("Q303", "5"), T("R304", "1"), 3.5),
    ("PVDD_SUB", T("R344", "2"), T("U202", "15", "16", "27", "28"), 2.5),
    ("/audio/AMP2_P", T("U202", "26", "23"), T("L205", "1"), 3.0),
    ("/audio/AMP2_N", T("U202", "17", "20"), T("L206", "1"), 3.0),
    ("/audio/SUB_P", T("L205", "2"), T("J203", "1"), 3.0), ("/audio/SUB_N", T("L206", "2"), T("J203", "2"), 3.0),
    ("/audio/AMP1_AP", T("U201", "26"), T("L201", "1"), 2.0), ("/audio/AMP1_AN", T("U201", "23"), T("L202", "1"), 2.0),
    ("/audio/AMP1_BP", T("U201", "17"), T("L203", "1"), 2.0), ("/audio/AMP1_BN", T("U201", "20"), T("L204", "1"), 2.0),
    ("/audio/SAT_L_P", T("L201", "2"), T("J201", "1"), 2.0), ("/audio/SAT_L_N", T("L202", "2"), T("J201", "2"), 2.0),
    ("/audio/SAT_R_P", T("L203", "2"), T("J202", "1"), 2.0), ("/audio/SAT_R_N", T("L204", "2"), T("J202", "2"), 2.0),
]


def fmt(ps):
    return ps[0][0] + "." + "/".join(p for _, p in ps)


class Copper:
    """per-net copper polygons per layer from the board (tracks, pads, zone fills), plus vias and PTH pads"""
    def __init__(self, path):
        import pcbnew
        self.pcbnew = pcbnew
        self.b = pcbnew.LoadBoard(path)
        self.layers = [pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu]
        self.vals = {fp.GetReference(): fp.GetValue() for fp in self.b.GetFootprints()}
        self.drop_zones = set()     # selftest: nets whose pours are ignored
        self.width_override = {}    # selftest: net -> track width mm

    def net_copper(self, net):
        pcbnew = self.pcbnew
        FM, mm = pcbnew.FromMM, pcbnew.ToMM
        polys = {l: pcbnew.SHAPE_POLY_SET() for l in self.layers}
        vias, pth, pads = [], [], {}
        for t in self.b.GetTracks():
            if t.GetNetname() != net:
                continue
            if t.GetClass() == "PCB_VIA":
                vias.append((mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetDrillValue())))
            else:
                w0 = t.GetWidth()
                if net in self.width_override:
                    t.SetWidth(FM(self.width_override[net]))
                t.TransformShapeToPolygon(polys[t.GetLayer()], t.GetLayer(), 0, FM(0.02), pcbnew.ERROR_INSIDE)
                t.SetWidth(w0)
        for fp in self.b.GetFootprints():
            for p in fp.Pads():
                if p.GetNetname() != net:
                    continue
                ls = [l for l in self.layers if p.IsOnLayer(l)]
                for l in ls:
                    p.TransformShapeToPolygon(polys[l], l, 0, FM(0.02), pcbnew.ERROR_INSIDE)
                bb = p.GetBoundingBox()
                pads.setdefault((fp.GetReference(), p.GetNumber()), []).append(
                    (mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetRight()), mm(bb.GetBottom())))
                if len(ls) > 1:
                    pth.append((mm(p.GetPosition().x), mm(p.GetPosition().y)))
        if net not in self.drop_zones:
            for z in self.b.Zones():
                if z.GetIsRuleArea() or z.GetNetname() != net:
                    continue
                for l in self.layers:
                    if z.IsOnLayer(l):
                        fp_ = z.GetFilledPolysList(l)
                        if fp_ is not None and fp_.OutlineCount():
                            polys[l].Append(fp_)
        return polys, vias, pth, pads

    def flow(self, net, src, dst, amps):
        """max-flow, first inside a window around the source and sink pads (copper outside it is ignored, so the
        result can only be lower than the true one: conservative), widened step by step only if that is not enough"""
        res = None
        for margin in (6.0, 15.0, None):
            res = self._flow(net, src, dst, amps, margin)
            if res[0] is None or res[0] >= amps:
                return res
        return res

    def _flow(self, net, src, dst, amps, margin):
        pcbnew = self.pcbnew
        if not hasattr(self, "_nc"):
            self._nc = {}
        key = (net, tuple(sorted(self.drop_zones)), tuple(sorted(self.width_override.items())))
        if key not in self._nc:
            self._nc = {key: self.net_copper(net)}      # cache one net (polys are simplified in place below)
        polys, vias, pth, pads = self._nc[key]
        for rp in src + dst:
            if rp not in pads:
                return None, f"pad {rp[0]}.{rp[1]} is not on {net}"
        boxes = [b for v in pads.values() for b in v]
        x0 = min(b[0] for b in boxes) - 2; y0 = min(b[1] for b in boxes) - 2
        x1 = max(b[2] for b in boxes) + 2; y1 = max(b[3] for b in boxes) + 2
        for l in self.layers:
            if polys[l].OutlineCount():
                bb = polys[l].BBox()
                x0 = min(x0, pcbnew.ToMM(bb.GetX())); y0 = min(y0, pcbnew.ToMM(bb.GetY()))
                x1 = max(x1, pcbnew.ToMM(bb.GetRight())); y1 = max(y1, pcbnew.ToMM(bb.GetBottom()))
        if margin is not None:
            tb = [b for rp in src + dst for b in pads[rp]]
            x0 = max(x0, min(b[0] for b in tb) - margin); y0 = max(y0, min(b[1] for b in tb) - margin)
            x1 = min(x1, max(b[2] for b in tb) + margin); y1 = min(y1, max(b[3] for b in tb) + margin)
        nx, ny = int((x1 - x0) / H) + 1, int((y1 - y0) / H) + 1
        cells = set()
        outer = {pcbnew.F_Cu: True, pcbnew.B_Cu: True, pcbnew.In1_Cu: False, pcbnew.In2_Cu: False}
        kcap = {l: amps / ipc_width(amps, outer[l]) * H for l in self.layers}     # A per cell edge
        for li, l in enumerate(self.layers):
            ps = polys[l]
            if not ps.OutlineCount():
                continue
            ps.Simplify()
            bb = ps.BBox()
            i0 = max(0, int((pcbnew.ToMM(bb.GetX()) - x0) / H) - 1); i1 = min(nx, int((pcbnew.ToMM(bb.GetRight()) - x0) / H) + 2)
            j0 = max(0, int((pcbnew.ToMM(bb.GetY()) - y0) / H) - 1); j1 = min(ny, int((pcbnew.ToMM(bb.GetBottom()) - y0) / H) + 2)
            i0, j0 = max(i0, 0), max(j0, 0)
            for i in range(i0, i1):
                xx = pcbnew.FromMM(x0 + i * H)
                for j in range(j0, j1):
                    if ps.Contains(pcbnew.VECTOR2I(xx, pcbnew.FromMM(y0 + j * H))):
                        cells.add((li, i, j))
        if not cells:
            return None, "no copper"
        cap = collections.defaultdict(float); adj = collections.defaultdict(list)

        def edge(a, b, c):
            if (a, b) not in cap:
                adj[a].append(b); adj[b].append(a)
            cap[(a, b)] += c; cap[(b, a)] += c
        for (li, i, j) in cells:
            for di, dj, f in ((1, 0, 1.0), (0, 1, 1.0), (1, 1, 0.5), (1, -1, 0.5)):   # diagonals: thin 45-deg runs
                n2 = (li, i + di, j + dj)
                if n2 in cells:
                    edge((li, i, j), n2, kcap[self.layers[li]] * f)
        INF = 1e9

        def cell_of(x, y):
            return int(round((x - x0) / H)), int(round((y - y0) / H))

        def near(li, ci, cj):
            for di in (0, -1, 1):
                for dj in (0, -1, 1):
                    if (li, ci + di, cj + dj) in cells:
                        return (li, ci + di, cj + dj)
            return None
        for k, (vx, vy, vd) in enumerate(vias):
            ci, cj = cell_of(vx, vy)
            for li in range(len(self.layers)):
                c = near(li, ci, cj)
                if c:
                    edge(("V", k), c, VIA_A * vd / 0.3)   # barrel cross-section pi*d*t scales with the drill
        for k, (vx, vy) in enumerate(pth):
            ci, cj = cell_of(vx, vy)
            for li in range(len(self.layers)):
                c = near(li, ci, cj)
                if c:
                    edge(("P", k), c, INF)
        S, Tn = ("S",), ("T",)
        for term, rps in ((S, src), (Tn, dst)):
            for rp in rps:
                for (bx0, by0, bx1, by1) in pads[rp]:
                    i0, j0 = cell_of(bx0 - NECK, by0 - NECK); i1, j1 = cell_of(bx1 + NECK, by1 + NECK)
                    for li in range(len(self.layers)):
                        for i in range(i0, i1 + 1):
                            for j in range(j0, j1 + 1):
                                if (li, i, j) in cells:
                                    edge(term, (li, i, j), INF)
        flow = 0.0
        while flow < amps * 1.25:
            par = {S: None}; q = collections.deque([S])
            while q and Tn not in par:
                u = q.popleft()
                for w in adj[u]:
                    if w not in par and cap[(u, w)] > 1e-9:
                        par[w] = u; q.append(w)
            if Tn not in par:
                break
            f, w = INF, Tn
            while par[w] is not None:
                f = min(f, cap[(par[w], w)]); w = par[w]
            if f >= INF / 2:
                return INF, "source and sink overlap"
            w = Tn
            while par[w] is not None:
                cap[(par[w], w)] -= f; cap[(w, par[w])] += f; w = par[w]
            flow += f
        if flow == 0:
            return None, f"{fmt(src)} and {fmt(dst)} are not connected by copper"
        where = ""
        if flow < amps:
            # min cut: copper cells still reachable from the source in the residual graph, next to unreachable ones
            seen = {S}; q = collections.deque([S])
            while q:
                u = q.popleft()
                for w in adj[u]:
                    if w not in seen and cap[(u, w)] > 1e-9:
                        seen.add(w); q.append(w)
            cut = {}
            for u in seen:
                if not (isinstance(u, tuple) and len(u) == 3):
                    continue
                for w in adj[u]:
                    if w not in seen:
                        if isinstance(w, tuple) and len(w) == 3 and w[0] == u[0]:
                            cut.setdefault(self.layers[u[0]], []).append((x0 + u[1] * H, y0 + u[2] * H))
                        elif isinstance(w, tuple) and w[0] == "V":
                            cut.setdefault("via", []).append(vias[w[1]][:2])
            parts = []
            for l, pts in cut.items():
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                name = l if l == "via" else self.b.GetLayerName(l)
                parts.append(f"{name} x {min(xs):.1f}-{max(xs):.1f} y {min(ys):.1f}-{max(ys):.1f}")
            where = "neck: " + "; ".join(parts)
        return flow, where


def check(cu):
    rep = Report("layer 3: copper vs current, set-points")
    vals = cu.vals
    for (net, src, dst, amps) in FLOWS:
        cap, why = cu.flow(net, src, dst, amps)
        if cap is None:
            rep.add("FAIL", "ampacity", f"{net}: {why}"); continue
        rep.add("PASS" if cap >= amps else "FAIL", "ampacity",
                f"{net} {fmt(src)} -> {fmt(dst)}: {amps:g} A needed, copper carries {min(cap, amps*1.25):.1f}{'+' if cap >= amps*1.25 else ''} A "
                f"(IPC-2221 {DT:g} K, pours included, vias {VIA_A} A per 0.3 mm drill)" + (f"  {why}" if why else ""))
    # ---- Pi 5 V: set-point and drop (FET + copper; the ampacity check guarantees at least the IPC 5 A width)
    vout = 0.760 * (1 + parse_value(vals.get("R331", "0")) / parse_value(vals.get("R332", "1")))
    rep.add("PASS" if 5.0 <= vout <= 5.25 else "FAIL", "set-point", f"Pi buck: 0.760*(1+R331/R332) = {vout:.3f} V (5.0-5.25 V)")
    drop = 5.0 * 0.017                                    # DMP3013SFV Rds(on) at -4.5 V [DS-DMP]
    for net, a, b_ in (("+5V_BUCK", ("L302", "2"), ("Q101", "5")), ("+5V", ("Q101", "1"), ("J101", "2"))):
        polys, vias, pth, pads = cu.net_copper(net)
        if a in pads and b_ in pads:
            pa = pads[a][0]; pb = pads[b_][0]
            L = math.dist(((pa[0] + pa[2]) / 2, (pa[1] + pa[3]) / 2), ((pb[0] + pb[2]) / 2, (pb[1] + pb[3]) / 2)) * 1.4
            drop += 5.0 * RHO * L / (ipc_width(5.0, True) * CU["outer"])
    rep.add("PASS" if vout - drop >= 4.90 else "FAIL", "IR drop",
            f"Pi 5 V at the header: {vout:.3f} V - {drop*1000:.0f} mV (ZVD FET + copper at 5 A) = {vout-drop:.3f} V (>= 4.90 V)")
    vb = 0.6 * (1 + parse_value(vals.get("R341", "0")) / parse_value(vals.get("R342", "1")))
    rep.add("PASS" if 4.5 <= vb <= 26.4 else "FAIL", "PVDD", f"sub boost {vb:.1f} V inside TAS5805M PVDD 4.5-26.4 V; pack 9.0-12.6 V also inside")
    rep.add("PASS" if vb <= 28 * 0.85 else "FAIL", "boost", f"MT3608 SW max 28 V: {vb:.1f} V leaves margin")
    # ---- LDOs: USB side XC6216 at a 20 V PD contract (500 mW without PCB credit), battery side HT7533 at 12.6 V (250 mW)
    i_ch340 = 0.015                                      # CH340C supply current, typ. (CH340DS1); confirm
    for ref, part, vin, i, pdmax in (("U306", "XC6216", 20.0, i_ch340, 0.5), ("U305", "HT7533-1", 12.6, 0.010, 0.25)):
        pd = (vin - 3.3) * i
        rep.add("PASS" if pd <= 0.8 * pdmax else "FAIL", "LDO",
                f"{ref} {part} at {vin:g} V, {i*1000:g} mA: {pd*1000:.0f} mW vs {pdmax*1000:.0f} mW rating (80 % = {pdmax*800:.0f} mW)")
    return rep


def selftest(cu):
    base = {r[2] for r in check(cu).fails()}
    st = Report("selftest: injected faults")
    cases = []
    cu.drop_zones = {"/battery/CELL1"}; cu.width_override = {"/battery/CELL1": 0.25}
    cases.append(("CELL1 link necked to 0.25 mm, no pour", check(cu)))
    cu.drop_zones = set(); cu.width_override = {}
    v = dict(cu.vals); cu.vals["R332"] = "8k2 1%"
    cases.append(("buck divider -> 6.1 V", check(cu)))
    cu.vals.clear(); cu.vals.update(v)
    for name, r in cases:
        new = {x[2] for x in r.fails()} - base
        st.add("PASS" if new else "FAIL", "inject", f"{name}: {len(new)} new FAIL")
    return st


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args or not os.path.exists(args[0]):
        print(__doc__); sys.exit(2)
    cu = Copper(args[0])
    r = check(cu); r.show("-q" in sys.argv)
    code = 1 if r.fails() else 0
    if "--selftest" in sys.argv:
        st = selftest(cu); st.show()
        if st.fails():
            code = 3
    sys.exit(code)
