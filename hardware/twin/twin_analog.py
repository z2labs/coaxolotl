"""Layer 3 - control board: the drawn copper and the part values against the currents and voltages they have to carry.

Ampacity is a max-flow through the real copper of each DC-path net (tracks, pads, zone fills on both layers,
rasterised at 0.2 mm, layers joined by vias at 1.5 A per 0.3 mm drill and by plated holes) between the pads where the
current enters and leaves, against IPC-2221 at a 20 K rise (1 oz = 35 um outer, JLC 2-layer).
Kelvin: the INA180A3 sense leads of the coax shunt must reach the rest of their net only through the shunt pads.
Set-points: the firmware scaling constants recomputed from the fitted values, the INA180 swing against the ADC and
the trip thresholds, the TPS2553 short-circuit current from RILIM, the back-channel level, the power budget against
the RTL-SDR V3 bias-T and the capacitor voltage ratings.

Sources: [DS-180] INA180 SBOS741H (gain A2 50 V/V, A3 100 V/V; output swing to VS - 20 mV), [DS-2553] TPS2553
SLVS841G IOS table (min/typ/max at RILIM 20 k, 49.9 k, 210 k), [DOC-HW] docs/hardware.md power budget (RTL-SDR V3
bias-T 4.5 V, about 180 mA; LNA budget 100 mA; 3V3 domain 1.5 mA active) and back-channel derivation (3rd harmonic,
25 R line, about -65 dBm), [FW] firmware/src/config.h and hal_efm8bb52.c constants (read by machine), USB 2.0 VBUS
5.25 V max (the V3 bias-T is switched from VBUS).

usage: kicad-python twin_analog.py board.kicad_pcb [--selftest] [-q]     exit 0 / 1 FAIL / 2 input / 3 selftest
"""
import os, re, sys, math, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from twin_common import Report

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
DT = 20.0
CU = {"outer": 0.035, "inner": 0.0175}            # mm
RHO = 1.72e-5                                     # Ohm*mm (copper, 20 C)
VIA_A = 1.5                                       # A per 0.3 mm via
H = 0.2                                           # raster, mm
NECK = 1.0                                        # mm around source/sink pads
I_COAX = 0.25        # A: design current of the coax DC path (above the V3 bias-T limit of about 180-200 mA [DOC-HW])
V_BUS_MAX = 5.25     # V: USB VBUS max, the highest the V3 bias-T can put on the coax
V_COAX_NOM = 4.5     # V: V3 bias-T [DOC-HW]
V_V3_LIMIT = 180     # mA [DOC-HW]
INA_SWING = 0.020    # V below VS [DS-180]
GAIN = {"INA180A1": 20, "INA180A2": 50, "INA180A3": 100, "INA180A4": 200}           # [DS-180]
IOS_TAB = [(20e3, 1200, 1295, 1375), (49.9e3, 475, 520, 565), (210e3, 110, 130, 150)]   # [DS-2553] mA


def ipc_amps(width_mm, outer):
    t = CU["outer" if outer else "inner"]
    a_mil2 = (width_mm / 0.0254) * (t / 0.0254)
    return (0.048 if outer else 0.024) * DT ** 0.44 * a_mil2 ** 0.725


def ipc_width(amps, outer):
    t = CU["outer" if outer else "inner"]
    a_mil2 = (amps / ((0.048 if outer else 0.024) * DT ** 0.44)) ** (1 / 0.725)
    return a_mil2 / (t / 0.0254) * 0.0254


T = lambda r, *p: [(r, x) for x in p]
FLOWS = [   # (source pads, sink pads, A): the net is the one on the source pads
    (T("J3", "1"), T("L5", "2"), I_COAX), (T("L5", "1"), T("L6", "2"), I_COAX), (T("L6", "1"), T("L7", "2"), I_COAX),
    (T("L7", "1"), T("R35", "1"), I_COAX), (T("R35", "2"), T("U1", "1"), I_COAX),
    (T("U1", "6"), T("U2", "1"), 0.21), (T("U1", "6"), T("FB1", "1"), 0.05),     # TPS2553 IOS max at RILIM; lamps
    (T("U2", "6"), T("R7", "1"), 0.21), (T("R7", "2"), T("J6", "2"), 0.21),
    (T("FB1", "2"), T("J4", "2"), 0.05),
]
KELVIN = [   # (sense pad, shunt pad, a load pad on the same net): the sense pad may reach the load only via the shunt pad
    (("U20", "3"), ("R35", "1"), ("L7", "1")), (("U20", "4"), ("R35", "2"), ("U1", "1")),
    (("U3", "3"), ("R7", "1"), ("U2", "6")), (("U3", "4"), ("R7", "2"), ("J6", "2")),
]


def fmt(ps):
    return ps[0][0] + "." + "/".join(p for _, p in ps)


class Copper:
    """per-net copper polygons per layer from the board (tracks, pads, zone fills), plus vias and PTH pads"""
    def __init__(self, path):
        import pcbnew
        self.pcbnew = pcbnew
        self.b = pcbnew.LoadBoard(path)
        n = self.b.GetCopperLayerCount()
        self.layers = [pcbnew.F_Cu] + [pcbnew.In1_Cu, pcbnew.In2_Cu][:max(0, n - 2)] + [pcbnew.B_Cu]
        self.vals = {fp.GetReference(): fp.GetValue() for fp in self.b.GetFootprints()}
        self.drop_zones = set()     # selftest: nets whose pours are ignored
        self.width_override = {}    # selftest: net -> track width mm
        self.skip_pads = set()      # pads left out of the copper
        self.cut_pads = set()       # Kelvin: copper inside these pads' outline removed (tracks included)
        self.neck = NECK

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
                if p.GetNetname() != net or (fp.GetReference(), p.GetNumber()) in self.skip_pads:
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
        key = (net, tuple(sorted(self.drop_zones)), tuple(sorted(self.width_override.items())),
               tuple(sorted(self.skip_pads)), len(self.b.GetTracks()), tuple(sorted(self.cut_pads)))
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
        outer = {l: l in (pcbnew.F_Cu, pcbnew.B_Cu) for l in self.layers}
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
        for rp in self.cut_pads:
            for (bx0, by0, bx1, by1) in pads.get(rp, []):
                i0, j0 = int(math.floor((bx0 - x0) / H)), int(math.floor((by0 - y0) / H))
                i1, j1 = int(math.ceil((bx1 - x0) / H)), int(math.ceil((by1 - y0) / H))
                cells -= {(li, i, j) for li in range(len(self.layers)) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)}
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
                    i0, j0 = cell_of(bx0 - self.neck, by0 - self.neck); i1, j1 = cell_of(bx1 + self.neck, by1 + self.neck)
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




# ---------------------------------------------------------------- values
def ohms(v):
    m = re.match(r"(\d+)([RkKM])(\d*)", v or "")
    if not m:
        return None
    a, u, b = m.groups()
    return float(f"{a}.{b or 0}") * {"R": 1, "k": 1e3, "K": 1e3, "M": 1e6}[u]


def farads(v):
    m = re.match(r"(\d+(?:\.\d+)?)([pnuµ]?)F", v or "")
    return float(m.group(1)) * {"": 1, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6}[m.group(2)] if m else None


def volts(v):
    m = re.search(r"/(\d+(?:\.\d+)?)V", v or "")
    return float(m.group(1)) if m else None


def gain(v):
    return next((g for k, g in GAIN.items() if k in (v or "")), None)


def fw_consts():
    """#define NAME number from the firmware sources [FW]"""
    out = {}
    for f in ("firmware/src/config.h", "firmware/src/hal_efm8bb52.c"):
        for m in re.finditer(r"#define\s+(\w+)\s+(\d+)u?l?\b", open(os.path.join(ROOT, f), encoding="utf-8").read()):
            out.setdefault(m.group(1), int(m.group(2)))
    return out


def ios(rilim):
    """TPS2553 short-circuit current (min, typ, max) in mA, log-log interpolated in the data sheet table [DS-2553]"""
    t = IOS_TAB
    for (r0, *a), (r1, *b) in zip(t, t[1:]):
        if r0 <= rilim <= r1 or (r1, *b) == t[-1]:
            k = math.log(rilim / r0) / math.log(r1 / r0)
            return tuple(math.exp(math.log(x) + k * (math.log(y) - math.log(x))) for x, y in zip(a, b))


def padnet(cu, rp):
    for fp in cu.b.GetFootprints():
        if fp.GetReference() == rp[0]:
            for p in fp.Pads():
                if p.GetNumber() == rp[1]:
                    return p.GetNetname()
    return None


# ---------------------------------------------------------------- checks
def check(cu):
    rep = Report("twin_analog - control board, layer 3 (copper vs current, Kelvin, set-points)")
    v = cu.vals
    ok = lambda c, g, m: rep.add("PASS" if c else "FAIL", g, m)
    for (src, dst, amps) in FLOWS:
        net = padnet(cu, src[0])
        cap, why = cu.flow(net, src, dst, amps)
        if cap is None:
            rep.add("FAIL", "ampacity", f"{net}: {why}"); continue
        ok(cap >= amps, "ampacity", f"{net} {fmt(src)} -> {fmt(dst)}: {amps:g} A needed, copper carries "
           f"{min(cap, amps * 1.25):.2f}{'+' if cap >= amps * 1.25 else ''} A (IPC-2221 {DT:g} K)" + (f"  {why}" if why else ""))
    # ---- Kelvin: with the shunt pad's outline cut out, the sense lead must no longer reach the load copper
    for sense, shunt, load in KELVIN:
        net = padnet(cu, sense)
        cu.neck, cu.cut_pads = 0.0, {shunt}
        cap, why = cu.flow(net, [sense], [load], 0.01)
        cu.neck, cu.cut_pads = NECK, set()
        cap2, _ = cu.flow(net, [sense], [load], 0.01)
        ok(cap is None and cap2 is not None, "kelvin",
           f"{fmt([sense])} meets {fmt([load])} only through the {fmt([shunt])} pad" if cap is None else
           f"{fmt([sense])} reaches {fmt([load])} outside the {fmt([shunt])} pad: shunt current flows in the sense lead")
    # ---- firmware scaling vs fitted values [FW]
    fw = fw_consts()
    g3, g20 = gain(v.get("U3")), gain(v.get("U20"))
    r7, r35 = ohms(v.get("R7")), ohms(v.get("R35"))
    for name, got, want in (("I_UV_PER_MA", r7 * g3 * 1000 if r7 and g3 else None, fw.get("I_UV_PER_MA")),
                            ("IT_UV_PER_MA", r35 * g20 * 1000 if r35 and g20 else None, fw.get("IT_UV_PER_MA"))):
        ok(got is not None and want and abs(got - want) <= 0.01 * want, "firmware",
           f"{name}: fitted parts give {got} uV/mA, firmware uses {want}")
    r3, r4, r11, r12 = (ohms(v.get(r)) for r in ("R3", "R4", "R11", "R12"))
    for name, got, num, den in (("VC_DIV", (r3 + r4) / r4, fw["VC_DIV_NUM"], fw["VC_DIV_DEN"]),
                                ("SC_DIV", (r11 + r12) / r12, fw["SC_DIV_NUM"], fw["SC_DIV_DEN"])):
        ok(abs(got - num / den) <= 0.005 * num / den, "firmware", f"{name}: fitted divider {got:.4f}, firmware {num}/{den}")
    vdd = fw["VDD_MV"] / 1000
    vs = vdd - INA_SWING
    fs_lna, fs_tot = vs / (r7 * g3) * 1000, vs / (r35 * g20) * 1000
    ok(fs_lna >= 1.05 * fw["I_TRIP_MA"], "range", f"LNA monitor full scale {fs_lna:.0f} mA (VS - 20 mV) vs trip {fw['I_TRIP_MA']} mA")
    ok(fs_tot >= max(fw["I_BUDGET_MA"], V_V3_LIMIT), "range",
       f"coax monitor full scale {fs_tot:.0f} mA vs budget {fw['I_BUDGET_MA']} mA and the V3 limit {V_V3_LIMIT} mA")
    ok(vs * 1000 < fw.get("ADC_REF_MV", 0), "range", f"INA180 swing {vs:.2f} V inside the ADC range {fw.get('ADC_REF_MV')} mV")
    sense_nom, sense_max = V_COAX_NOM * r4 / (r3 + r4), V_BUS_MAX * r4 / (r3 + r4)
    ok(sense_nom < vdd, "range", f"SENSE at {V_COAX_NOM} V coax: {sense_nom:.2f} V < VDD {vdd} V")
    absmax = vdd * 0.98 + 0.3          # [DS-BB52] Table 4.1 GPIO <= VDD + 0.3 V, at the MCP1700's -2 % VDD
    rep.add("PASS" if sense_max <= vdd * 0.98 else "WARN", "range",
            f"SENSE at {V_BUS_MAX} V (USB max): {sense_max:.2f} V" + ("" if sense_max <= vdd * 0.98 else
            f" - above VDD (ADC VIN max [DS-BB52]: reads full scale)" + (f" and above the GPIO maximum "
            f"{absmax:.2f} V (VDD - 2 % + 0.3); through {r3 * r4 / (r3 + r4) / 1000:.1f} k only uA flow. "
            f"Not reached with the V3 ({V_COAX_NOM} V); a 15 k top resistor (VC_DIV 37/22) keeps it under VDD"
            if sense_max > absmax else "")))
    ok(fw["RAIL_LOW_MV"] * r4 / (r3 + r4) < vdd * 1000, "range", f"RAIL_LOW {fw['RAIL_LOW_MV']} mV -> SENSE "
       f"{fw['RAIL_LOW_MV'] * r4 / (r3 + r4):.0f} mV, inside the ADC")
    # ---- TPS2553 [DS-2553]
    r5 = ohms(v.get("R5"))
    lo, ty, hi = ios(r5)
    ok(15e3 <= r5 <= 232e3, "tps2553", f"RILIM {r5 / 1000:.0f} k inside 15-232 k")
    ok(fw["I_HI_MA"] < fw["I_TRIP_MA"] < lo, "tps2553",
       f"IOS at {r5 / 1000:.0f} k: {lo:.0f}/{ty:.0f}/{hi:.0f} mA (min/typ/max); firmware I-HI {fw['I_HI_MA']} < trip "
       f"{fw['I_TRIP_MA']} < IOS min, so the firmware trips before the switch limits")
    # ---- back-channel level [DOC-HW]
    r31, r32, r33 = (ohms(v.get(r)) for r in ("R31", "R32", "R33"))
    h3 = 4 / math.pi * vdd / 2 / 3
    rp = r32 * (r33 + 25) / (r32 + r33 + 25)
    pad = h3 * rp / (r31 + rp)
    line = pad * 25 / (r33 + 25)
    dbm = 10 * math.log10(line ** 2 / 2 / 50 / 1e-3)
    ok(abs(dbm - (-65)) <= 3, "beacon", f"3rd harmonic at the coax: {line * 1e3:.3f} mV peak = {dbm:.1f} dBm "
       f"(docs/hardware.md: about -65 dBm)")
    # ---- power budget [DOC-HW]
    vf = 2.0
    lamp = (V_COAX_NOM - vf - 0.1) / ohms(v.get("RN1")) * 1000
    fixed = (V_COAX_NOM / ohms(v.get("R1")) + (V_COAX_NOM - vf) / ohms(v.get("R2")) + V_COAX_NOM / (r3 + r4)) * 1000
    worst = 100 + 23 * lamp + fixed + 1.5
    charge = (V_COAX_NOM - 0.3) / ohms(v.get("R10")) * 1000
    ok(worst <= fw["I_BUDGET_MA"], "budget", f"steady worst: LNA 100 + 23 lamps x {lamp:.2f} + bleeder/PWR/divider "
       f"{fixed:.1f} + 3V3 1.5 = {worst:.0f} mA <= BUDGET {fw['I_BUDGET_MA']} mA (docs: 132 mA)")
    ok(worst + charge <= V_V3_LIMIT, "budget", f"boot peak with the supercap charging ({charge:.0f} mA): "
       f"{worst + charge:.0f} mA <= V3 bias-T {V_V3_LIMIT} mA")
    # ---- capacitor voltage ratings
    rail = {}
    for fp in cu.b.GetFootprints():
        r = fp.GetReference()
        if re.fullmatch(r"C\d+", r):
            nets = {p.GetNetname() for p in fp.Pads()} - {"GND"}
            rail[r] = nets
    vmax = lambda n: vdd + 0.1 if "3V3" in n or "VREF" in n or "ADC" in n or "SENSE" in n else V_BUS_MAX
    for r, nets in sorted(rail.items(), key=lambda x: int(x[0][1:])):
        vr = volts(v.get(r))
        need = max([vmax(n) for n in nets] or [0])
        if r == "C47":
            need = V_BUS_MAX      # DC block: the full coax voltage across it
        if vr is None:
            rep.add("WARN", "rating", f"{r} {v.get(r)}: no voltage in the value"); continue
        if (farads(v.get(r)) or 0) >= 0.01:          # EDLC supercap: rated voltage is the maximum, no derating rule
            ok(need <= vr, "rating", f"{r} {v.get(r)} (supercap) on {sorted(nets)}: {need} V max <= {vr} V")
            continue
        tant = "CP_EIA" in str(cu.fp_of.get(r, "")) or vr <= 6.3 and (farads(v.get(r)) or 0) >= 100e-6
        if tant:
            rep.add("PASS" if need <= 0.8 * vr else ("WARN" if V_COAX_NOM <= 0.8 * vr else "FAIL"), "rating",
                    f"{r} {v.get(r)} (tantalum/electrolytic) on {sorted(nets)}: {need} V max vs 80 % of {vr} V = "
                    f"{0.8 * vr:.2f} V" + ("" if need <= 0.8 * vr else f"; the V3's {V_COAX_NOM} V is inside"))
        else:
            ok(need <= vr, "rating", f"{r} {v.get(r)} on {sorted(nets)}: {need} V max")
    return rep


def selftest(cu):
    import pcbnew
    base = {r[2] for r in check(cu).fails()}
    out = []

    def case(name, key, mutate, undo):
        mutate()
        new = [m for s, g, m in check(cu).rows if s == "FAIL" and m not in base]
        undo()
        hit = [m for m in new if key in m]
        print(f"  {'caught' if hit else 'MISSED'}  {name}" + (f"  <- {hit[0][:110]}" if hit else f"  (new FAILs: {new[:2]})"))
        out.append(bool(hit))
    v0 = dict(cu.vals)

    def val(r, x):
        return lambda: cu.vals.__setitem__(r, x), lambda: (cu.vals.clear(), cu.vals.update(v0))
    added = []

    def kelvin_short():
        fps = {fp.GetReference(): fp for fp in cu.b.GetFootprints()}
        a = [p for p in fps["U20"].Pads() if p.GetNumber() == "3"][0]
        b = [p for p in fps["L7"].Pads() if p.GetNumber() == "1"][0]
        t = pcbnew.PCB_TRACK(cu.b); t.SetStart(a.GetPosition()); t.SetEnd(b.GetPosition())
        t.SetWidth(pcbnew.FromMM(0.25)); t.SetLayer(pcbnew.F_Cu); t.SetNet(a.GetNet()); cu.b.Add(t); added.append(t)

    def kelvin_undo():
        for t in added:
            cu.b.Remove(t)
        added.clear(); cu._nc = {}
    case("INA180A3 sense lead tied to the L7 track", "outside the R35.1 pad", kelvin_short, kelvin_undo)
    net = padnet(cu, ("R35", "2"))

    def neck():
        cu.drop_zones = {net}; cu.width_override = {net: 0.02}

    def unneck():
        cu.drop_zones = set(); cu.width_override = {}
    case("coax DC path necked to 0.02 mm, no pour", "VCOAX: R35.2", neck, unneck)
    case("LNA shunt 0R47", "I_UV_PER_MA", *val("R7", "0R47/1%"))
    case("coax monitor fitted with an A2", "IT_UV_PER_MA", *val("U20", "INA180A2IDBVR(SOT23-5)"))
    case("SENSE divider bottom 20 k", "VC_DIV", *val("R4", "20k/1%"))
    case("RILIM 210 k", "IOS", *val("R5", "210k/1%"))
    case("beacon series 470 R", "dBm", *val("R31", "470R/1%"))
    case("330 uF rated 4 V", "C9", *val("C9", "330uF/4V/20%"))
    case("bleeder 100 R", "steady worst", *val("R1", "100R/1%"))
    case("1 nF 10 V on the coax", "C1", *val("C1", "100nF/4V/10%"))
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args or not os.path.exists(args[0]):
        print(__doc__); sys.exit(2)
    cu = Copper(args[0])
    cu.fp_of = {fp.GetReference(): fp.GetFPIDAsString() for fp in cu.b.GetFootprints()}
    code = 0
    if "--selftest" in sys.argv:
        print("== selftest (injected faults)")
        res = selftest(cu)
        print(f"  -> {sum(res)}/{len(res)} caught")
        code = 0 if all(res) else 3
    r = check(cu); r.show("-q" in sys.argv)
    sys.exit(code or (1 if r.fails() else 0))
