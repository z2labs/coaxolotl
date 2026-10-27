"""Build the Coaxolotl board with KiCad's pcbnew API: outline, 4-layer stack-up, placement, GND vias, zones.

    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" hardware/kicad/tools/pcb_build.py

Outline (mm, x to the right, y down, origin top-left):
    y   0 - 30   lamp strip: 2 rows x 24 lamps, 6x 74HC595, 12 resistor arrays, J5
    y  30 - 32   routed slot; a left tab and a right tab with mouse-bite holes. The 7 strip signals cross the right tab
                 between the holes, straight from J5 to J4 (same x, pin 1 left), so the strip can be snapped off and
                 linked back with a 7-wire cable.
    y  32 - 102  main board: antenna SMA left, RF axis at y = YC, filter bank in the middle, RTL-SDR SMA right,
                 power and hold-up along the bottom, MCU top right.

Stack-up (JLC04161H-7628): F.Cu RF + parts + GND pour, In1 solid GND (RF reference), In2 power/signal + GND fill,
B.Cu GND pour. Every part is on the top side.

Placement = hard anchors (connectors, RF switches, lamp grid) + chain placer (each RF path laid out along a polyline:
series parts in line, shunt parts hanging off the line to one side, GND via beyond the shunt) + a spiral placer for
the rest (each part as close as possible to the pad of its partner, courtyard-free).
"""
import os, sys, math, json, re
import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PRJ = os.path.normpath(os.path.join(HERE, ".."))
FPDIR = os.environ.get("KICAD_FP", r"C:\Program Files\KiCad\10.0\share\kicad\footprints")
OX, OY = 50.0, 40.0          # board origin on the KiCad page
W, H = 100.0, 102.0
STRIP_Y1, SLOT_Y0, SLOT_Y1 = 30.0, 30.0, 32.0
SLOT_X0, SLOT_X1 = 5.0, 79.0  # slot centre-line ends (radius 1 mm)
YC = 64.0                    # RF axis
EDGE = 0.8                   # parts keep this far from the board edge
GAP = 0.5                    # courtyard gap for the spiral placer
CGAP = 0.35                  # courtyard gap inside an RF chain
LANE0, LANE_PITCH = 3.5, 5.0  # filter-bank lanes at YC +- (LANE0 + i * LANE_PITCH)


def mm(v):
    return pcbnew.FromMM(v)


def P(x, y):
    return pcbnew.VECTOR2I(mm(OX + x), mm(OY + y))


def tomm(p):
    return pcbnew.ToMM(p.x) - OX, pcbnew.ToMM(p.y) - OY


# ------------------------------------------------------------ s-expression reader (netlist)
def parse(s):
    toks = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', s)
    st = [[]]
    for t in toks:
        if t == "(":
            st.append([])
        elif t == ")":
            x = st.pop(); st[-1].append(x)
        else:
            st[-1].append(t[1:-1].replace('\\"', '"') if t.startswith('"') else t)
    return st[0][0]


def find(n, k):
    return [c for c in n if isinstance(c, list) and c and c[0] == k]


def find1(n, k):
    r = find(n, k)
    return r[0] if r else None


# the schematic generator owns the net classes (POWER ...); pcbnew's Save rewrites the project file from the board's
# own (empty) settings, so keep them now and merge them back after the save
PRO_ORIG = json.load(open(os.path.join(PRJ, "coaxolotl.kicad_pro"), encoding="utf-8"))

board = pcbnew.BOARD()
board.SetCopperLayerCount(4)
ds = board.GetDesignSettings()
ds.SetBoardThickness(mm(1.6))
board.SetLayerName(pcbnew.In1_Cu, "In1.Cu")
board.SetLayerName(pcbnew.In2_Cu, "In2.Cu")
board.SetLayerType(pcbnew.In1_Cu, pcbnew.LT_POWER)     # solid GND: the router keeps off it
board.SetLayerType(pcbnew.In2_Cu, pcbnew.LT_SIGNAL)


# ------------------------------------------------------------ outline + slot
def edge_seg(a, b):
    s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(P(*a)); s.SetEnd(P(*b)); s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(0.1)); board.Add(s)


def edge_arc(a, m, b):
    s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_ARC)
    s.SetArcGeometry(P(*a), P(*m), P(*b)); s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(0.1)); board.Add(s)


for a, b in (((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0))):
    edge_seg(a, b)
ym = (SLOT_Y0 + SLOT_Y1) / 2
edge_seg((SLOT_X0, SLOT_Y0), (SLOT_X1, SLOT_Y0))
edge_seg((SLOT_X0, SLOT_Y1), (SLOT_X1, SLOT_Y1))
edge_arc((SLOT_X0, SLOT_Y0), (SLOT_X0 - 1, ym), (SLOT_X0, SLOT_Y1))
edge_arc((SLOT_X1, SLOT_Y0), (SLOT_X1 + 1, ym), (SLOT_X1, SLOT_Y1))

# ------------------------------------------------------------ netlist
net = parse(open(os.path.join(PRJ, "coaxolotl.net"), encoding="utf-8").read())
comps, sheet_of = {}, {}
for c in find(find1(net, "components"), "comp"):
    ref = find1(c, "ref")[1]
    props = {find1(p, "name")[1] for p in find(c, "property") if find1(p, "name")}
    fl = find1(c, "fields")
    fields = {find1(f, "name")[1]: (f[2] if len(f) > 2 else "") for f in find(fl, "field")} if fl else {}
    if find1(c, "datasheet"):
        fields["Datasheet"] = find1(c, "datasheet")[1]
    comps[ref] = dict(value=find1(c, "value")[1], fp=(find1(c, "footprint") or [None, ""])[1],
                      dnp="dnp" in props, nobom="exclude_from_bom" in props, fields=fields)
    sp = find1(c, "sheetpath")
    sheet_of[ref] = (find1(sp, "names")[1] if sp else "/").strip("/")
pinnet, netobjs, netpins = {}, {}, {}
for n in find(find1(net, "nets"), "net"):
    name = find1(n, "name")[1]
    ni = pcbnew.NETINFO_ITEM(board, name)
    board.Add(ni)
    netobjs[name] = ni
    for node in find(n, "node"):
        k = (find1(node, "ref")[1], find1(node, "pin")[1])
        pinnet[k] = name
        netpins.setdefault(name, []).append(k)


def short(n):
    return n.split("/")[-1]


def nets_of(ref):
    return {p: n for (r, p), n in pinnet.items() if r == ref}


def is_na(ref):
    return comps[ref]["value"].startswith("NA")


RAILS = {"GND", "+3V3", "VCOAX", "VMAIN", "VHOLD", "VLED", "LNA_VCC"}


def load_fp(fpid):
    lib, name = fpid.split(":")
    own = os.path.join(PRJ, lib + ".pretty")
    fp = pcbnew.FootprintLoad(own if os.path.isdir(own) else os.path.join(FPDIR, lib + ".pretty"), name)
    if fp is None:
        raise RuntimeError("footprint not found: " + fpid)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


fps, where, padpos = {}, {}, {}
for ref, c in sorted(comps.items()):
    if not c["fp"]:
        continue
    fp = load_fp(c["fp"])
    fp.SetReference(ref); fp.SetValue(c["value"])
    fp.SetDNP(c["dnp"]); fp.SetExcludedFromBOM(c["nobom"])
    for fname, fval in c["fields"].items():          # symbol fields (MPN, Place, Zone): schematic parity
        try:
            fp.SetField(fname, fval)
            f = fp.GetField(fname)
        except Exception:
            f = pcbnew.PCB_FIELD(fp, fp.GetFieldCount(), fname); f.SetText(fval); fp.AddField(f)
        f.SetVisible(False); f.SetLayer(pcbnew.F_Fab)
    for pad in fp.Pads():
        n = pinnet.get((ref, pad.GetNumber()))
        if n:
            pad.SetNet(netobjs[n])
    board.Add(fp)
    fps[ref] = fp

# ------------------------------------------------------------ placement bookkeeping
occupied, owner = [], []
BLOCK = [(0, SLOT_Y0 - 0.9, W, SLOT_Y1 + 0.9)]       # nothing across the break line


def geom(fp, rot):
    fp.SetOrientationDegrees(rot)
    fp.SetPosition(P(0, 0))
    cy = fp.GetCourtyard(pcbnew.F_CrtYd)
    bb = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False)
    x0, y0 = tomm(pcbnew.VECTOR2I(bb.GetX(), bb.GetY()))
    box = (x0, y0, x0 + pcbnew.ToMM(bb.GetWidth()), y0 + pcbnew.ToMM(bb.GetHeight()))
    pads = {}
    for p in fp.Pads():
        if p.GetNumber():
            pads.setdefault(p.GetNumber(), tomm(p.GetPosition()))
    return box, pads


def free(rect, gap=GAP):
    x0, y0, x1, y1 = rect
    if x0 < EDGE or y0 < EDGE or x1 > W - EDGE or y1 > H - EDGE:
        return False
    for (a, b, c, d) in occupied:
        if x0 < c + gap and x1 > a - gap and y0 < d + gap and y1 > b - gap:
            return False
    for (a, b, c, d) in BLOCK:
        if x0 < c and x1 > a and y0 < d and y1 > b:
            return False
    return True


def put(ref, x, y, rot):
    fp = fps[ref]
    box, pads = geom(fp, rot)
    fp.SetPosition(P(x, y))
    where[ref] = (x, y, rot)
    occupied.append((box[0] + x, box[1] + y, box[2] + x, box[3] + y)); owner.append(ref)
    for n, (px, py) in pads.items():
        padpos[(ref, n)] = (px + x, py + y)


def orient(ref, want):
    """rotation that points the given pins the given way: want = {pin: (dx, dy)}"""
    best = None
    for r in (0, 90, 180, 270):
        _, pads = geom(fps[ref], r)
        sc = sum(pads[p][0] * d[0] + pads[p][1] * d[1] for p, d in want.items())
        if best is None or sc > best[0]:
            best = (sc, r)
    return best[1]


def place_near(ref, tgt, rots=(0, 90, 180, 270), maxr=40.0):
    fp = fps[ref]
    geoms = {r: geom(fp, r) for r in rots}
    for ring in range(0, int(maxr / 0.25)):
        rad = ring * 0.25
        k = 1 if ring == 0 else max(8, int(2 * math.pi * rad / 0.25))
        for i in range(k):
            cx = round((tgt[0] + rad * math.cos(2 * math.pi * i / k)) * 4) / 4
            cy = round((tgt[1] + rad * math.sin(2 * math.pi * i / k)) * 4) / 4
            for r, g in geoms.items():
                b = g[0]
                if free((b[0] + cx, b[1] + cy, b[2] + cx, b[3] + cy)):
                    put(ref, cx, cy, r)
                    return True
    print("WARNING: no room for", ref)
    return False


def block_line(a, b, hw=0.6):
    BLOCK.append((min(a[0], b[0]) - hw, min(a[1], b[1]) - hw, max(a[0], b[0]) + hw, max(a[1], b[1]) + hw))


# ------------------------------------------------------------ RF chains
def chain(start, end, allowed):
    """series parts from net start to net end (BFS over parts with exactly two non-GND nets), and the shunt parts
    (all pins on one chain node or GND) hanging off every node"""
    edges = {}
    for ref in allowed:
        if ref in where:
            continue
        ns = sorted(set(nets_of(ref).values()) - {"GND"})
        for i in range(len(ns)):
            for j in range(i + 1, len(ns)):
                edges.setdefault(ns[i], []).append((ref, ns[j])); edges.setdefault(ns[j], []).append((ref, ns[i]))
    prev, q = {start: None}, [start]
    while q:
        n = q.pop(0)
        if n == end:
            break
        for ref, m in sorted(edges.get(n, [])):
            if m not in prev:
                prev[m] = (n, ref); q.append(m)
    if end not in prev:
        raise RuntimeError(f"no chain {short(start)} -> {short(end)}")
    nodes, series = [end], []
    while prev[nodes[-1]] is not None:
        n, ref = prev[nodes[-1]]
        series.append(ref); nodes.append(n)
    nodes.reverse(); series.reverse()
    used = set(series)
    items = []
    for i, node in enumerate(nodes):
        if i > 0:
            items.append(("S", series[i - 1], nodes[i - 1], node))
        for ref in sorted(allowed):
            if ref in used or ref in where:
                continue
            ns = set(nets_of(ref).values())
            if node in ns and "GND" in ns and ns <= {node, "GND"}:
                items.append(("H", ref, node, "GND")); used.add(ref)
    return items


EXTRA = {"R22": 3.6}           # room either side of the FM-notch bypass link for the notch switches U9 / U10


CHAINS = []                    # laid chains: (polyline segments, items) - pre-routed along their line below


def lay(items, poly, side, s0=0.0, s_end=0.0, name=""):
    """lay a chain along a polyline. side(d) -> unit vector of the side the shunts hang to for direction d."""
    segs = []
    for a, b in zip(poly, poly[1:]):
        L = math.dist(a, b)
        if L > 1e-6:
            segs.append((a, ((b[0] - a[0]) / L, (b[1] - a[1]) / L), L))
    si, s = 0, s0
    for kind, ref, n_in, n_out in items:
        s += EXTRA.get(ref, 0.0)
        while True:
            a, d, L = segs[si]
            o = side(d)
            pins = nets_of(ref)
            best = None
            for r in (0, 90, 180, 270):
                box, pads = geom(fps[ref], r)
                pin_in = [pads[p] for p, n in pins.items() if n == n_in and p in pads]
                pin_out = [pads[p] for p, n in pins.items() if n == n_out and p in pads]
                if not pin_in or not pin_out:
                    continue
                ci = (sum(p[0] for p in pin_in) / len(pin_in), sum(p[1] for p in pin_in) / len(pin_in))
                co = (sum(p[0] for p in pin_out) / len(pin_out), sum(p[1] for p in pin_out) / len(pin_out))
                v = (co[0] - ci[0], co[1] - ci[1])
                sc = v[0] * d[0] + v[1] * d[1] if kind == "S" else v[0] * o[0] + v[1] * o[1]
                if kind == "H" and len(pins) > 2:
                    sc = (co[0] * o[0] + co[1] * o[1]) - (ci[0] * o[0] + ci[1] * o[1])
                if best is None or sc > best[0] + 1e-6:
                    best = (sc, r, box, ci, co)
            _, r, box, ci, co = best
            ext = [box[0] * d[0] + box[1] * d[1], box[2] * d[0] + box[3] * d[1]]
            amin, amax = min(ext), max(ext)
            if kind == "S":     # the in/out pin centre on the line
                m = ((ci[0] + co[0]) / 2, (ci[1] + co[1]) / 2)
            else:               # the chain-node pin on the line
                m = ci
            m_al = m[0] * d[0] + m[1] * d[1]
            # centre position along the segment so the courtyard starts at s
            c_al = s - amin
            if c_al + amax > L - s_end * (si == len(segs) - 1) and si < len(segs) - 1:
                si += 1
                s = 1.4       # past the corner: clear of the parts round it
                continue
            # line point at the pin
            lp = (a[0] + d[0] * (c_al + m_al), a[1] + d[1] * (c_al + m_al))
            perp = (-d[1], d[0])
            m_pp = m[0] * perp[0] + m[1] * perp[1]
            cx = lp[0] - d[0] * m_al - perp[0] * m_pp
            cy = lp[1] - d[1] * m_al - perp[1] * m_pp
            put(ref, round(cx, 3), round(cy, 3), r)
            s = c_al + amax + CGAP + EXTRA.get(ref, 0.0)
            if s > L - s_end + 0.01 and si == len(segs) - 1:
                print(f"WARNING: chain {name} overruns its line at {ref}")
            break
    for a, d, L in segs:
        block_line(a, (a[0] + d[0] * L, a[1] + d[1] * L), 0.5)
    CHAINS.append((segs, items))
    return s


# ------------------------------------------------------------ 1) RF anchors
DOWN, UP, LEFT, RIGHT = (0, 1), (0, -1), (-1, 0), (1, 0)
X_U6, X_U7, X_U8 = 18.0, 33.0, 42.0
put("J2", 2.541, YC, 180)             # flange line at local x = +2.54: rot 180 puts it on the board edge (1 um in)
put("J3", W - 2.54, YC, 0)
put("U6", X_U6, YC, orient("U6", {"7": LEFT, "11": DOWN}))     # RFC to the antenna, RF2 (LNA) down
put("U7", X_U7, YC, orient("U7", {"7": RIGHT, "2": DOWN}))     # RFC to the bank, RF1 (LNA out) down
put("U8", X_U8, YC, orient("U8", {"22": LEFT}))                 # RFC to U7, controls toward U11

fe = [r for r in comps if sheet_of[r] == "RF front end" and not is_na(r) and r in fps]
bk = [r for r in comps if sheet_of[r] == "Filter bank" and not is_na(r) and r in fps]
OUTSIDE = lambda d: (-1, 0) if d == DOWN else (0, 1) if d == RIGHT else (1, 0) if d == UP else (0, -1)

# antenna line: J2 -> U6 RFC, protection (GDT, clamp diodes, bleeder) hanging down off the line
j2, u6c = padpos[("J2", "1")], padpos[("U6", "7")]
lay(chain(pinnet[("J2", "1")], pinnet[("U6", "7")], fe), [(j2[0] + 3.3, YC), (u6c[0], YC)],
    lambda d: (0, 1), s_end=1.0, name="ANT")
# LNA loop: U6 RF2 down, along the bottom, up into U7 RF1
a, b = padpos[("U6", "11")], padpos[("U7", "2")]
LOOP = YC + 23.0
lay(chain(pinnet[("U6", "11")], pinnet[("U7", "2")], fe),
    [a, (a[0], LOOP), (b[0], LOOP), b], OUTSIDE, s0=0.9, s_end=0.9, name="LNA")
# FM notch (U9 / U10, not fitted by default): the switches sit just outside the loop at both ends of the R22 link,
# RFC toward the line, so fitting them means only lifting R22
r22 = nets_of("R22")
p_in = next(padpos[("R22", p)] for p, n in r22.items() if n == pinnet[("U12", "3")])
p_out = next(padpos[("R22", p)] for p, n in r22.items() if n != pinnet[("U12", "3")])
d = ((p_out[0] - p_in[0]) / math.dist(p_in, p_out), (p_out[1] - p_in[1]) / math.dist(p_in, p_out))
o = OUTSIDE((round(d[0]), round(d[1])))
for u, p, sg in (("U9", p_in, -1), ("U10", p_out, 1)):
    put(u, round(p[0] + sg * d[0] * 2.4 + o[0] * 3.4, 3), round(p[1] + sg * d[1] * 2.4 + o[1] * 3.4, 3),
        orient(u, {"7": (-o[0], -o[1])}))
block_line(padpos[("U6", "2")], (padpos[("U6", "2")][0], YC - 4.5))     # bypass arc over the top
block_line((padpos[("U6", "2")][0], YC - 4.5), (padpos[("U7", "11")][0], YC - 4.5))
block_line(padpos[("U7", "11")], (padpos[("U7", "11")][0], YC - 4.5))
block_line(padpos[("U7", "7")], padpos[("U8", "22")])

# ------------------------------------------------------------ 2) filter bank: nested lanes between U8 and U11
lanes = []
for k in range(8):
    s_net, e_net = f"Net-(U8-RF{k + 1})", f"Net-(U11-RF{8 - k})"
    items = chain(s_net, e_net, bk)
    lanes.append((k, items))
# lane order: U8 ports above the axis, the one nearest the control side innermost
u8 = where["U8"]
above, below = [], []
for k, items in lanes:
    pin = [p for (r, p), n in pinnet.items() if r == "U8" and n == f"Net-(U8-RF{k + 1})"][0]
    px, py = padpos[("U8", pin)]
    (above if py < u8[1] else below).append((-(px - u8[0]), k, pin))
above.sort(); below.sort()


def length(items):
    tot = 0
    for kind, ref, *_ in items:
        box, _ = geom(fps[ref], 0 if kind == "S" else 90)
        tot += box[2] - box[0] + CGAP
    return tot


Lmax = max(length(items) for _, items in lanes)
X0 = X_U8 + 3.3
X_U11 = X0 + Lmax + 3.3
put("U11", X_U11, YC, orient("U11", {"22": RIGHT}))
print(f"bank: lanes {X0:.1f} .. {X0 + Lmax:.1f} mm, U11 at x = {X_U11:.1f} mm")
lane_y = {}
for half, sgn in ((above, -1), (below, 1)):
    for i, (_, k, pin) in enumerate(half):
        y = YC + sgn * (LANE0 + i * LANE_PITCH)
        lane_y[k] = y
        items = dict(lanes)[k]
        out = (0, sgn)
        lay(items, [(X0, y), (X_U11 - 3.3, y)], lambda d, o=out: o, name=f"F{k}")
        # risers from the switch pins to the lane
        p8 = padpos[("U8", pin)]
        u11pin = [p for (r, p), n in pinnet.items() if r == "U11" and n == f"Net-(U11-RF{8 - k})"][0]
        p11 = padpos[("U11", u11pin)]
        for p, xe in ((p8, X0), (p11, X_U11 - 3.3)):
            block_line(p, (p[0], y), 0.4); block_line((p[0], y), (xe, y), 0.4)
        print(f"  F{k}: lane y = {y:.1f} mm, U8 pin {pin} -> U11 pin {u11pin}, "
              f"{len([i for i in items if i[0] == 'S'])} series / {len([i for i in items if i[0] == 'H'])} shunt")
# planarity check: U11 port order must mirror U8's
for half, sgn in ((above, -1), (below, 1)):
    ks = [k for _, k, _ in half]
    ys11 = []
    for k in ks:
        u11pin = [p for (r, p), n in pinnet.items() if r == "U11" and n == f"Net-(U11-RF{8 - k})"][0]
        ys11.append(padpos[("U11", u11pin)])
    order = sorted(range(len(ks)), key=lambda i: (ys11[i][0] - X_U11))
    if order != list(range(len(ks))):
        print("WARNING: U11 port order does not mirror U8 on", "top" if sgn < 0 else "bottom", ks, ys11)
BLOCK.append((X0, YC - 1.2, X_U11 - 3.3, YC + 1.2))       # control corridor SW_V1..3 between the switches

# output: U11 RFC -> DC block -> J3, bias feed at the connector
u11c, j3 = padpos[("U11", "22")], padpos[("J3", "1")]
lay(chain(pinnet[("U11", "22")], pinnet[("J3", "1")], bk), [(u11c[0] + 1.0, YC), (j3[0] - 3.3, YC)],
    lambda d: (0, 1), name="OUT")

# ------------------------------------------------------------ 3) lamp strip
LAMP_X0, LAMP_DX = 4.0, 3.2
ROW_Y = (8.0, 16.0)
NAMES = ["THRU", "LF", "160/80", "60-30", "20-10", "6/4m", "2m/AIR", "70/23",
         "SYNC", "SYNC", "SYNC", "B2", "B1", "B0", "LNA", "QUIET", "CLR", "CHK", "CHK", "CHK", "CHK", "CHK", "CHK", "RX",
         "0", "1", "OK", "ERR", "SYNC", "CHK", "GAP", "RUNT", "TMO", "LNA", "BYP", "AUTO", "FMN", "I-LO", "I-OK",
         "I-HI", "TRIP", "RUN", "HOLD", "LOW", "QUIET", "DROP", "GLT", "RST"]
rot_led = orient("D7", {"1": DOWN})
for n in range(48):
    row, i = divmod(n, 24)
    put(f"D{7 + n}", LAMP_X0 + i * LAMP_DX, ROW_Y[row], rot_led)
# register k sits under the 4 lamp columns it drives (both rows): U14 .. U19 left to right
REG_X = [LAMP_X0 + 1.5 * LAMP_DX + 4 * LAMP_DX * i for i in range(6)]
REG_ORDER = ["U14", "U15", "U16", "U17", "U18", "U19"]
for x, u in zip(REG_X, REG_ORDER):
    # arrays under the lamps, LED side (pins 5-8) up; the register below them, Q1-Q7 (pins 1-7) up
    put(u, x, 25.0, orient(u, {"1": UP}))
    k = int(u[1:]) - 14
    put(f"RN{1 + 2 * k}", x - 2.0, 19.4, orient(f"RN{1 + 2 * k}", {p: UP for p in "5678"}))
    put(f"RN{2 + 2 * k}", x + 2.0, 19.4, orient(f"RN{2 + 2 * k}", {p: UP for p in "5678"}))
rot_hdr = orient("J5", {"7": RIGHT})
put("J5", 82.55, 27.0, rot_hdr)
put("J4", 82.55, 35.0, rot_hdr)
BLOCK.append((80.5, 26.0, 99.5, 36.0))                 # the J5 -> J4 link traces

# ------------------------------------------------------------ 4) holes: mounting + mouse bites
HOLES = [(3.5, 35.5), (3.5, 98.5), (96.5, 98.5), (96.5, 41.0)]
for i, (x, y) in enumerate(HOLES):
    fp = load_fp("MountingHole:MountingHole_3.2mm_M3")
    fp.SetReference(f"H{i + 1}"); fp.SetPosition(P(x, y)); fp.SetBoardOnly(True)
    board.Add(fp)
    occupied.append((x - 3.2, y - 3.2, x + 3.2, y + 3.2)); owner.append(f"H{i + 1}")
bites = [0.7, 1.6, 2.5, 3.4] + [80.2, 81.1] + [83.82 + 2.54 * j for j in range(6)] + [99.3]
for i, x in enumerate(bites):
    fp = load_fp("coaxolotl:MouseBite_NPTH_0.6mm")
    fp.SetReference(f"MB{i + 1}"); fp.SetPosition(P(x, ym)); fp.SetBoardOnly(True)
    board.Add(fp)

# ------------------------------------------------------------ 4b) RF switch control pins: fan-out via positions
# each control / supply pin gets its own via right off the pin (two staggered rows, 0.4/0.2 mm), so those nets drop
# to In2/B.Cu inside the rings the RF tracks close on F.Cu. Kept free of parts from here on.
FANOUT = {"U6": "14 16", "U7": "14 16", "U9": "14 16", "U10": "14 16", "U8": "8 9 10 11", "U11": "8 9 10 11"}
TIE = [("U6", "15", "16"), ("U9", "15", "16")]   # LS = VDD: pin to pin, one via
FAN = []
for ref, pins in FANOUT.items():
    c = where[ref][:2]
    side = []
    for pin in pins.split():
        q = padpos[(ref, pin)]
        dx, dy = q[0] - c[0], q[1] - c[1]
        n = (1.0 if dx > 0 else -1.0, 0.0) if abs(dx) > abs(dy) else (0.0, 1.0 if dy > 0 else -1.0)
        side.append((pin, q, n))
    side.sort(key=lambda t: t[1][0] * t[2][1] - t[1][1] * t[2][0])      # along the side
    for i, (pin, q, n) in enumerate(side):
        dist = 1.0 if i % 2 == 0 else 1.8
        v = (q[0] + n[0] * dist, q[1] + n[1] * dist)
        FAN.append((ref, pin, q, v))
        BLOCK.append((v[0] - 0.5, v[1] - 0.5, v[0] + 0.5, v[1] + 0.5))
        block_line(q, v, 0.3)

# ------------------------------------------------------------ 5) soft anchors (nearest free spot)
# fine-pitch ICs get a fan-out ring: their own passives keep this far off the courtyard so every pin can escape
CLEAR_AROUND = {"U5": 1.6}
SOFT = {
    "L7": (88.0, 80.0, 0), "C10": (83.0, 94.0, 0),
    "U5": (87.0, 50.0, 0), "J1": (77.0, 40.0, 90),
    "U1": (72.0, 93.0, 0), "U4": (64.0, 97.0, 0), "U2": (58.0, 92.0, 0), "U3": (50.0, 92.0, 0),
    "C9": (64.0, 91.0, 90), "R7": (54.0, 97.0, 0), "D1": (76.0, 99.0, 0),
}
for ref, (x, y, r) in SOFT.items():
    place_near(ref, (x, y), (r,))
    if ref in CLEAR_AROUND:
        m = CLEAR_AROUND[ref]
        bx = occupied[owner.index(ref)]
        BLOCK.append((bx[0] - m, bx[1] - m, bx[2] + m, bx[3] + m))

# ------------------------------------------------------------ 6) everything else next to its partner pad
PARTNER = {
    "C36": ("U6", "16"), "C37": ("U7", "16"), "C38": ("U8", "8"), "C39": ("U11", "8"),
    "C41": ("U14", "16"), "C42": ("U15", "16"), "C43": ("U16", "16"), "C44": ("U17", "16"), "C45": ("U18", "16"),
    "C46": ("U19", "16"), "C14": ("U5", "6"), "C15": ("U5", "6"), "C6": ("U4", "2"), "C13": ("U4", "2"),
    "C9": ("U4", "3"), "C12": ("U4", "3"), "C1": ("U1", "1"), "C3": ("U1", "3"), "C4": ("U2", "1"),
    "C32": ("FB2", "1"), "C40": ("FB2", "1"), "FB2": ("L4", "1"), "R1": ("U1", "1"), "D1": ("U1", "1"),
    "C8": ("FB1", "2"), "R16": ("U5", "20"), "R17": ("U5", "19"), "R18": ("U5", "18"), "R15": ("U6", "14"),
    "R26": ("U7", "14"), "R19": ("U5", "17"), "R14": ("U2", "3"), "R20": ("U14", "13"),
    "R9": ("U1", "3"), "FB1": ("U1", "3"), "R2": ("U1", "1"), "D3": ("R9", "2"), "D2": ("R2", "2"),
}


def partner_target(ref):
    if ref in PARTNER:
        k = PARTNER[ref]
        return padpos.get(k)
    nets = sorted({n for n in nets_of(ref).values() if n not in RAILS}, key=lambda n: len(netpins.get(n, [])))
    for n in nets:
        ks = [k for k in netpins[n] if k[0] != ref and k in padpos]
        if ks:
            k = max(ks, key=lambda k: (len(fps[k[0]].Pads()) if k[0] in fps else 0, k))
            return padpos[k]
    return None


def best_spot(ref, tgt):
    fp = fps[ref]
    geoms = {r: geom(fp, r) for r in (0, 90, 180, 270)}
    mine_nets = {n for n in nets_of(ref).values() if n != "GND"}
    best = None
    for ring in range(0, 160):
        rad = ring * 0.25
        k = 1 if ring == 0 else max(8, int(2 * math.pi * rad / 0.25))
        for i in range(k):
            cx = round((tgt[0] + rad * math.cos(2 * math.pi * i / k)) * 4) / 4
            cy = round((tgt[1] + rad * math.sin(2 * math.pi * i / k)) * 4) / 4
            for r, (box, pads) in geoms.items():
                if not free((box[0] + cx, box[1] + cy, box[2] + cx, box[3] + cy)):
                    continue
                d = min((math.hypot(px + cx - tgt[0], py + cy - tgt[1]) for p, (px, py) in pads.items()
                         if nets_of(ref).get(p) in mine_nets), default=rad)
                if best is None or d < best[0] - 1e-6:
                    best = (d, cx, cy, r)
        if best is not None and rad > best[0] + 2.0:
            break
    return best


todo = [r for r in sorted(fps) if r not in where]
for _ in range(8):
    left = []
    for ref in todo:
        tgt = partner_target(ref)
        if tgt is None:
            left.append(ref); continue
        b = best_spot(ref, tgt)
        if b is None:
            print("WARNING: no room for", ref); left.append(ref); continue
        put(ref, b[1], b[2], b[3])
    if not left or left == todo:
        todo = left
        break
    todo = left
for ref in todo:
    print("NOTE: free placement for", ref)
    place_near(ref, (50.0, 95.0))

nover = 0
for i in range(len(occupied)):
    for j in range(i + 1, len(occupied)):
        a, b = occupied[i], occupied[j]
        if a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]:
            nover += 1
            print(f"  overlap: {owner[i]} x {owner[j]}")
print("courtyard overlaps:", nover)


# ------------------------------------------------------------ 7) vias: exposed pads, GND pins
def add_via(x, y, net="GND", d=0.6, h=0.3):
    v = pcbnew.PCB_VIA(board); v.SetPosition(P(x, y)); v.SetWidth(mm(d)); v.SetDrill(mm(h))
    v.SetNet(netobjs[net]); board.Add(v)
    vias.append((x, y, d / 2))


def add_track(a, b, w, net="GND", layer=pcbnew.F_Cu):
    t = pcbnew.PCB_TRACK(board); t.SetStart(P(*a)); t.SetEnd(P(*b)); t.SetWidth(mm(w))
    t.SetLayer(layer); t.SetNet(netobjs[net]); board.Add(t)
    tracks.append((a, b, w / 2, net))


vias, tracks = [], []
pads_all = []          # (ref, net, x0, y0, x1, y1)
for ref, fp in fps.items():
    for p in fp.Pads():
        bb = p.GetBoundingBox()
        x0, y0 = tomm(pcbnew.VECTOR2I(bb.GetX(), bb.GetY()))
        pads_all.append((ref, p.GetNetname(), x0, y0, x0 + pcbnew.ToMM(bb.GetWidth()), y0 + pcbnew.ToMM(bb.GetHeight()),
                         p.GetNumber()))
for fp in board.GetFootprints():
    if fp.GetReference().startswith(("MB", "H")):
        for p in fp.Pads():
            bb = p.GetBoundingBox(); x0, y0 = tomm(pcbnew.VECTOR2I(bb.GetX(), bb.GetY()))
            pads_all.append((fp.GetReference(), "", x0, y0, x0 + pcbnew.ToMM(bb.GetWidth()),
                             y0 + pcbnew.ToMM(bb.GetHeight()), ""))


def d_pt_rect(px, py, r):
    dx = max(r[2] - px, 0, px - r[4]); dy = max(r[3] - py, 0, py - r[5])
    return math.hypot(dx, dy)


def d_seg_rect(a, b, r):
    # sampled: segments here are short (dog-bones, EP ties)
    n = max(2, int(math.dist(a, b) / 0.05))
    return min(d_pt_rect(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n, r) for i in range(n + 1))


def ep_pads(fp):
    pads = list(fp.Pads())
    if len(pads) < 6:
        return []
    big = max(pads, key=lambda p: p.GetBoundingBox().GetWidth() * p.GetBoundingBox().GetHeight())
    num = big.GetNumber()
    return [p for p in pads if p.GetNumber() == num and p.GetNetname() == "GND"]


# exposed pads: via field (0.45/0.2 mm)
nep = 0
for ref, fp in fps.items():
    eps = ep_pads(fp)
    if not eps:
        continue
    bb = eps[0].GetBoundingBox()
    for p in eps[1:]:
        bb.Merge(p.GetBoundingBox())
    x0, y0 = tomm(pcbnew.VECTOR2I(bb.GetX(), bb.GetY()))
    w_, h_ = pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())
    nx, ny = max(1, int((w_ - 0.1) / 0.85) + 1), max(1, int((h_ - 0.1) / 0.85) + 1)
    nx, ny = min(nx, 3), min(ny, 3)
    for i in range(nx):
        for j in range(ny):
            x = x0 + w_ / 2 + (i - (nx - 1) / 2) * min(0.85, (w_ - 0.45) / max(nx - 1, 1))
            y = y0 + h_ / 2 + (j - (ny - 1) / 2) * min(0.85, (h_ - 0.45) / max(ny - 1, 1))
            add_via(x, y, d=0.45, h=0.2); nep += 1
    # GND pins next to the exposed pad: a short stub straight in (skipping the corner pins)
    for p in fp.Pads():
        if p.GetNetname() != "GND" or p in eps or p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
            continue
        q = tomm(p.GetPosition())
        tx = min(max(q[0], x0), x0 + w_); ty = min(max(q[1], y0), y0 + h_)
        if abs(tx - q[0]) > 1e-3 and abs(ty - q[1]) > 1e-3:
            continue
        ok = all(d_seg_rect(q, (tx, ty), r) >= 0.2 + 0.075 for r in pads_all if r[0] == ref and r[1] not in ("GND", ""))
        if ok:
            add_track(q, (tx, ty), 0.15)
print("exposed-pad vias:", nep)

# the 7 strip links across the right tab: straight from J5 pin k to J4 pin k, between the mouse-bite holes
for k in range(1, 8):
    net = pinnet[("J5", str(k))]
    w = 0.25 if net.startswith("/") else 0.5
    add_track(padpos[("J5", str(k))], padpos[("J4", str(k))], w, net)
    if net == "GND":
        add_track(padpos[("J5", str(k))], padpos[("J4", str(k))], w, net, layer=pcbnew.B_Cu)

for ref, a_, b_ in TIE:
    add_track(padpos[(ref, a_)], padpos[(ref, b_)], 0.2, pinnet[(ref, a_)])

# chains: every node pre-routed along its line, pad to pad (the router only joins the switch pins to the lanes)
padsize = {(r[0], r[6]): min(r[4] - r[2], r[5] - r[3]) for r in pads_all}
npre = 0
for segs, items in CHAINS:
    offs, acc = [], 0.0
    for a, d, L in segs:
        offs.append(acc); acc += L

    def along(p):
        best = None
        for (a, d, L), o in zip(segs, offs):
            t = max(0.0, min(L, (p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]))
            q = (a[0] + d[0] * t, a[1] + d[1] * t)
            dd = math.dist(p, q)
            if best is None or dd < best[0]:
                best = (dd, o + t)
        return best[1]
    refs = {it[1] for it in items}
    nodes = {n for it in items for n in it[2:] if n != "GND"}
    for net in nodes:
        pads = sorted(((along(padpos[k]), k) for k in padpos if k[0] in refs and pinnet.get(k) == net), key=lambda t: t[0])
        for (s1, k1), (s2, k2) in zip(pads, pads[1:]):
            path = [padpos[k1]] + [a for (a, d, L), o in zip(segs, offs) if s1 < o < s2] + [padpos[k2]]
            w = 0.35 if min(padsize.get(k1, 1), padsize.get(k2, 1)) >= 0.45 else 0.25
            for p1, p2 in zip(path, path[1:]):
                add_track(p1, p2, w, net)
            npre += 1
print("chain links pre-routed:", npre)

nfan = 0
for ref, pin, q, v in FAN:
    add_track(q, v, 0.15, pinnet[(ref, pin)])
    add_via(*v, net=pinnet[(ref, pin)], d=0.4, h=0.2)
    nfan += 1
print("switch control fan-out vias:", nfan)

# pins walled in by RF risers: U11 pin 1 (LS = VDD) sits between the RF2 riser and the RF1 riser round the corner.
# A via straight out of the pin, shifted away from the RF neighbour, lets +3V3 come in from In2/B.Cu.
for ref, pin, nb in (("U11", "1", "2"),):
    q, r_ = padpos[(ref, pin)], padpos[(ref, nb)]
    c = where[ref][:2]
    n = (q[0] - c[0], q[1] - c[1])
    n = (1.0, 0.0) if abs(n[0]) > abs(n[1]) else (0.0, 1.0)
    n = (n[0] * (1 if q[0] > c[0] else -1), n[1] * (1 if q[1] > c[1] else -1))
    lat = (q[0] - r_[0], q[1] - r_[1]); L = math.hypot(*lat); lat = (lat[0] / L, lat[1] / L)
    v = (q[0] + n[0] * 1.15 + lat[0] * 0.45, q[1] + n[1] * 1.15 + lat[1] * 0.45)
    add_track(q, (q[0] + n[0] * 0.55, q[1] + n[1] * 0.55), 0.25, pinnet[(ref, pin)])
    add_track((q[0] + n[0] * 0.55, q[1] + n[1] * 0.55), v, 0.25, pinnet[(ref, pin)])
    add_via(*v, net=pinnet[(ref, pin)], d=0.45, h=0.2)
    print(f"escape via {ref}.{pin} at {v[0]:.2f}, {v[1]:.2f}")


def via_ok(x, y, rv, ref, own=None, clr=0.25):
    if x < 0.6 or y < 0.6 or x > W - 0.6 or y > H - 0.6 or SLOT_Y0 - 0.6 < y < SLOT_Y1 + 0.6:
        return False
    for r in pads_all:
        if r[1] == "GND" and r[0] == ref:
            continue
        if d_pt_rect(x, y, r) < rv + (clr if r[1] else 0.3):
            return False
    for (vx, vy, vr) in vias:
        if math.hypot(vx - x, vy - y) < vr + rv + 0.25:
            return False
    return True


def dogbones():
    made, miss = 0, []
    for ref, fp in sorted(fps.items()):
        if ref.startswith(("J", "TP")):
            continue
        eps = ep_pads(fp)
        cx, cy = where[ref][0], where[ref][1]
        for p in fp.Pads():
            if p.GetNetname() != "GND" or p in eps or p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            if eps and len(list(fp.Pads())) > 8:
                continue                      # QFN GND pins: tied to the exposed pad above
            q = tomm(p.GetPosition())
            bb = p.GetBoundingBox(); hw, hh = pcbnew.ToMM(bb.GetWidth()) / 2, pcbnew.ToMM(bb.GetHeight()) / 2
            out = (q[0] - cx, q[1] - cy)
            dirs = sorted([(1, 0), (-1, 0), (0, 1), (0, -1), (0.707, 0.707), (-0.707, 0.707), (0.707, -0.707),
                           (-0.707, -0.707)], key=lambda d: -(d[0] * out[0] + d[1] * out[1]))
            done = False
            for gap in (0.3, 0.55, 0.85):
                for d in dirs:
                    r = abs(d[0]) * hw + abs(d[1]) * hh + gap + 0.3
                    v = (q[0] + d[0] * r, q[1] + d[1] * r)
                    if not via_ok(v[0], v[1], 0.3, ref):
                        continue
                    w = min(0.3, 2 * min(hw, hh) - 0.05)
                    if any(d_seg_rect(q, v, rr) < w / 2 + 0.2 for rr in pads_all if rr[1] != "GND" or rr[0] != ref
                           if not (rr[1] == "GND" and rr[0] == ref)):
                        continue
                    add_track(q, v, w); add_via(*v); made += 1; done = True
                    break
                if done:
                    break
            if not done:
                miss.append(f"{ref}.{p.GetNumber()}")
    print(f"GND dog-bones: {made}; no room: {' '.join(miss) or 'none'}")


dogbones()

# ------------------------------------------------------------ 8) zones: GND on every layer, strip and main apart
def zone(layer, y0, y1, prio=0, name=""):
    z = pcbnew.ZONE(board); z.SetLayer(layer); z.SetNet(netobjs["GND"])
    ol = z.Outline(); ol.NewOutline()
    for (x, y) in ((0, y0), (W, y0), (W, y1), (0, y1)):
        ol.Append(mm(OX + x), mm(OY + y))
    z.SetAssignedPriority(prio); z.SetZoneName(name)
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    z.SetMinThickness(mm(0.2))
    try:
        z.SetLocalClearance(mm(0.25))
    except Exception:
        pass
    board.Add(z)


for lay_, nm in ((pcbnew.F_Cu, "F"), (pcbnew.In1_Cu, "In1"), (pcbnew.In2_Cu, "In2"), (pcbnew.B_Cu, "B")):
    zone(lay_, 0.0, SLOT_Y0 - 0.4, 0, f"GND_STRIP_{nm}")
    zone(lay_, SLOT_Y1 + 0.4, H, 0, f"GND_MAIN_{nm}")

# ------------------------------------------------------------ 9) silk: lamp names, connectors, title
def text(s, x, y, size=1.0, angle=0, th=0.15, layer=pcbnew.F_SilkS, just=None):
    t = pcbnew.PCB_TEXT(board); t.SetText(s); t.SetPosition(P(x, y)); t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size))); t.SetTextThickness(mm(th))
    if angle:
        t.SetTextAngleDegrees(angle)
    if just == "left":
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
    board.Add(t)


for n in range(48):
    row, i = divmod(n, 24)
    text(NAMES[n], LAMP_X0 + i * LAMP_DX, (3.5, 12.0)[row], 1.0, 90)
text("COAXOLOTL", 90.0, 8.0, 2.0, 0, 0.3)
text("rev 0.1  z2labs", 90.0, 11.5, 1.0)
text("CERN-OHL-S-2.0", 90.0, 14.0, 1.0)
text("ANT", 6.0, YC - 7.0, 1.2)
text("RTL-SDR", 93.0, YC - 7.0, 1.2)
for k, y in lane_y.items():
    text(f"F{k}", X0 - 0.3 - 1.4, y, 0.8)

# ------------------------------------------------------------ save + project rules
# lamp strip and its drivers: references on the fab layer (the strip is the front panel; JLC needs no silk refs)
for ref, fp in fps.items():
    if where.get(ref, (0, 99))[1] < SLOT_Y0:
        fp.Reference().SetLayer(pcbnew.F_Fab)

filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
out = os.path.join(PRJ, "coaxolotl.kicad_pcb")
board.Save(out)
import shutil
shutil.copy(out, os.path.join(PRJ, "coaxolotl_placed.kicad_pcb"))

RF_NETS = sorted({n for n in netpins if n.startswith(("Net-(U6-", "Net-(U7-", "Net-(U8-RF", "Net-(U9-", "Net-(U10-",
                                                    "Net-(U11-RF", "Net-(U12-", "Net-(U13-RF", "Net-(J2-", "Net-(J3-",
                                                    "Net-(C1", "Net-(C2", "Net-(C3", "Net-(C4", "Net-(C5", "Net-(C6",
                                                    "Net-(C7"))
                  or n.endswith("RF_BANK")} - {"Net-(C30-Pad1)"})
pro = os.path.join(PRJ, "coaxolotl.kicad_pro")
pj = json.load(open(pro, encoding="utf-8"))
pj["net_settings"] = json.loads(json.dumps(PRO_ORIG.get("net_settings", {})))
ns = pj["net_settings"]
classes = ns.setdefault("classes", [])
base = next(c for c in classes if c["name"] == "Default")
base.update(clearance=0.2, track_width=0.2, via_diameter=0.6, via_drill=0.3)
rf = next((c for c in classes if c["name"] == "RF"), None)
if rf is None:
    rf = dict(base); rf["name"] = "RF"; classes.append(rf)
rf.update(clearance=0.2, track_width=0.35, via_diameter=0.6, via_drill=0.3)
pats = [p for p in ns.get("netclass_patterns", []) if p["netclass"] != "RF"]
pats += [{"netclass": "RF", "pattern": n} for n in RF_NETS]
ns["netclass_patterns"] = pats
bd = pj.setdefault("board", {}).setdefault("design_settings", {}).setdefault("rules", {})
bd.update(min_through_hole_diameter=0.2, min_via_diameter=0.4, min_via_annular_width=0.075,
          min_copper_edge_clearance=0.3, min_clearance=0.15, min_track_width=0.15, min_hole_to_hole=0.25)
json.dump(pj, open(pro, "w", encoding="utf-8"), indent=2)
open(os.path.join(PRJ, "coaxolotl.kicad_dru"), "w", encoding="utf-8").write(
    "(version 1)\n"
    "# edge-mount SMA: the centre and ground pads run to the board edge by design\n"
    "(rule \"SMA edge\"\n  (condition \"A.memberOfFootprint('J2') || A.memberOfFootprint('J3') || B.memberOfFootprint('J2') || B.memberOfFootprint('J3')\")\n"
    "  (constraint edge_clearance (min 0mm)))\n")
json.dump({r: list(v) for r, v in where.items()}, open(os.path.join(PRJ, "placement.json"), "w"), indent=0)
print("RF nets:", len(RF_NETS))
print("placed", len(where), "of", len(fps), "footprints ->", out)
