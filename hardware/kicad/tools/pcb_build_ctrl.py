"""Build the Coaxolotl CONTROL board (2 layers, no RF) with KiCad's pcbnew API: outline, stack-up, placement, GND vias,
zones. Shared placement helpers are copied from pcb_build.py (the 4-layer RF board).

    set COAX_VARIANT=ctrl
    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" hardware/kicad/tools/pcb_build_ctrl.py

Outline (mm, x to the right, y down, origin top-left):
    y   0 - 32   lamp strip, as on the RF board (2 rows x 24 lamps, 6x 74HC595, 12 arrays, J5)
    y  32 - 34   routed slot with mouse bites; the 7 strip signals cross the right tab (J5 -> J4)
    y  34 - H    control board: RTL-SDR SMA (air launch) on the right edge with the bias-T, power and hold-up,
                 MCU, and J6 - the plated hole row that hands the RF switch controls to a later RF board.

Stack-up: 2 layers, 1.6 mm. F.Cu parts + routing + GND pour, B.Cu routing + GND pour. Every part on the top side.
"""
import os, sys, math, json, re
import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PRJ = os.path.normpath(os.path.join(HERE, "..", "..", "kicad_ctrl"))
LIBDIR = os.path.normpath(os.path.join(HERE, ".."))            # coaxolotl.pretty lives with the RF board
FPDIR = os.environ.get("KICAD_FP", r"C:\Program Files\KiCad\10.0\share\kicad\footprints")
OX, OY = 50.0, 40.0
W, H = 100.0, 70.0
STRIP_Y1, SLOT_Y0, SLOT_Y1 = 32.0, 32.0, 34.0
SLOT_X0, SLOT_X1 = 5.0, 79.0
YJ = 54.0                    # RTL-SDR SMA axis (right edge)
EDGE = 0.8
GAP = 0.5
DOWN, UP, LEFT, RIGHT = (0, 1), (0, -1), (-1, 0), (1, 0)


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
board.SetCopperLayerCount(2)
ds = board.GetDesignSettings()
ds.SetBoardThickness(mm(1.6))


def edge_seg(a, b):
    s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(P(*a)); s.SetEnd(P(*b)); s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(0.1)); board.Add(s)


def edge_arc(a, m, b):
    s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_ARC)
    s.SetArcGeometry(P(*a), P(*m), P(*b)); s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(0.1)); board.Add(s)


NOTCH = 0.95                 # J3 air-launch notch: the footprint draws its walls, the outline leaves the mouth open
for a, b in (((0, 0), (W, 0)), ((W, 0), (W, YJ - NOTCH)), ((W, YJ + NOTCH), (W, H)), ((W, H), (0, H)),
             ((0, H), (0, 0))):
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
    own = os.path.join(LIBDIR, lib + ".pretty")
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



# ------------------------------------------------------------ 1) connectors
put("J3", W, YJ, 180)                     # BWSMA-KE-P001 air launch, body out of the right edge
# J6: the RF control hole row along the bottom-left, pin 1 left
put("J6", 8.0, H - 5.0, orient("J6", {"10": RIGHT}))
BLOCK.append((5.0, H - 10.5, 8.0 + 9 * 2.54 + 3.0, H - 2.5))     # the hole row and its pin names

# ------------------------------------------------------------ 2) lamp strip
LAMP_X0, LAMP_DX = 5.0, 3.2
ROW_Y = (8.0, 16.0)
NAMES = ["THRU", "LF", "160/80", "60-30", "20-10", "6/4m", "2m/AIR", "70/23",
         "SYNC", "SYNC", "SYNC", "B2", "B1", "B0", "LNA", "QUIET", "CLR", "CHK", "CHK", "CHK", "CHK", "CHK", "CHK", "RX",
         "0", "1", "OK", "ERR", "SYNC", "CHK", "GAP", "RUNT", "TMO", "LNA", "BYP", "AUTO", "FMN", "I-LO", "I-OK",
         "I-HI", "TRIP", "RUN", "HOLD", "LOW", "QUIET", "DROP", "GLT", "RST"]
rot_led = orient("D7", {"1": DOWN})
LABEL_BASE = (ROW_Y[0] - 1.9, ROW_Y[1] - 1.9)          # text starts 0.4 mm above the lamp courtyard
LABEL_BOXES = []
for n in range(48):
    row, i = divmod(n, 24)
    x = LAMP_X0 + i * LAMP_DX
    L = 0.85 * len(NAMES[n]) + 0.3
    LABEL_BOXES.append((x - 0.75, LABEL_BASE[row] - L - 0.2, x + 0.75, LABEL_BASE[row] + 0.2))
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
put("J5", 82.55, 29.0, rot_hdr)
put("J4", 82.55, 37.0, rot_hdr)
BLOCK.append((80.5, 28.0, 99.5, 38.0))                 # the J5 -> J4 link traces


# ------------------------------------------------------------ 3) holes: mounting + mouse bites
HOLES = [(3.5, 37.5), (3.5, H - 3.5 - 9.0), (96.5, H - 3.5), (96.5, 43.0)]
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

# ------------------------------------------------------------ 4) soft anchors
FAN = []
CLEAR_AROUND = {"U5": 2.4}
SOFT = {
    "L7": (86.0, 61.0, 0), "C10": (67.0, 59.0, 0),
    "U5": (40.0, 46.0, 0), "J1": (28.0, 40.0, 90),
    "U1": (52.0, 56.0, 0), "U4": (46.0, 63.0, 0), "U2": (58.0, 43.0, 0), "U3": (68.0, 43.0, 0),
    "C9": (53.0, 64.0, 90), "R7": (68.0, 48.0, 0), "D1": (93.0, 48.0, 0),
    "L6": (88.0, 49.0, 90), "L5": (93.0, 54.0, 0), "R24": (84.0, 49.0, 90),
}
for ref, (x, y, r) in SOFT.items():
    if ref not in fps:
        continue
    place_near(ref, (x, y), (r,))
    if ref in CLEAR_AROUND:
        m = CLEAR_AROUND[ref]
        bx = occupied[owner.index(ref)]
        BLOCK.append((bx[0] - m, bx[1] - m, bx[2] + m, bx[3] + m))

# QFN20 MCU: a staggered fan-out via per signal pin
FANOUT_LATE = {"U5": "1 2 5 6 7 8 9 10 11 13 14 15 16 17 18 19 20"}
for ref, pins in FANOUT_LATE.items():
    c = where[ref][:2]
    sides = {}
    for pin in pins.split():
        q = padpos[(ref, pin)]
        dx, dy = q[0] - c[0], q[1] - c[1]
        n = (1.0 if dx > 0 else -1.0, 0.0) if abs(dx) > abs(dy) else (0.0, 1.0 if dy > 0 else -1.0)
        sides.setdefault(n, []).append((pin, q))
    for n, lst in sides.items():
        lst.sort(key=lambda t: t[1][0] * n[1] - t[1][1] * n[0])
        for i, (pin, q) in enumerate(lst):
            dist = 1.0 if i % 2 == 0 else 1.8
            v = (q[0] + n[0] * dist, q[1] + n[1] * dist)
            FAN.append((ref, pin, q, v))
            BLOCK.append((v[0] - 0.45, v[1] - 0.45, v[0] + 0.45, v[1] + 0.45))

# ------------------------------------------------------------ 5) everything else next to its partner pad
RAILS = {"GND", "+3V3", "VCOAX", "VMAIN", "VHOLD", "VLED", "LNA_VCC"}
PARTNER = {
    "C41": ("U14", "16"), "C42": ("U15", "16"), "C43": ("U16", "16"), "C44": ("U17", "16"), "C45": ("U18", "16"),
    "C46": ("U19", "16"), "C14": ("U5", "4"), "C15": ("U5", "4"), "C6": ("U4", "2"), "C13": ("U4", "2"),
    "C9": ("U4", "3"), "C12": ("U4", "3"), "C1": ("U1", "1"), "C3": ("U1", "3"), "C4": ("U2", "1"),
    "R1": ("U1", "1"), "D1": ("U1", "1"), "C8": ("FB1", "2"),
    "R30": ("U5", "6"), "R14": ("U2", "3"), "R20": ("U14", "13"),
    "R9": ("U1", "3"), "FB1": ("U1", "3"), "R2": ("U1", "1"), "D3": ("R9", "2"), "D2": ("R2", "2"),
}
PARTNER = {k: v for k, v in PARTNER.items() if k in fps}


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
    place_near(ref, (50.0, 60.0))

nover = 0
for i in range(len(occupied)):
    for j in range(i + 1, len(occupied)):
        a, b = occupied[i], occupied[j]
        if a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]:
            nover += 1
            print(f"  overlap: {owner[i]} x {owner[j]}")
print("courtyard overlaps:", nover)


# ------------------------------------------------------------ 6) vias: exposed pads, GND pins, strip links, fan-out
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
    if len(pads) < 6 or fp.GetReference().startswith("J"):     # connectors: GND legs are not an exposed pad
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

# lamp cathodes: each drops to the inner layers through its own via, half a column to the right of the lamp, on the
# same spot in every column (never under a lamp name), so the strip wiring stays off the label rows
for n in range(48):
    d = f"D{7 + n}"
    q = padpos[(d, "1")]
    v = (q[0] + LAMP_DX / 2, q[1] + 0.35)
    add_track(q, (q[0] + 0.35, q[1] + 0.35), 0.25, pinnet[(d, "1")])
    add_track((q[0] + 0.35, q[1] + 0.35), v, 0.25, pinnet[(d, "1")])
    add_via(*v, net=pinnet[(d, "1")])

nfan = 0
for ref, pin, q, v in FAN:
    add_track(q, v, 0.15, pinnet[(ref, pin)])
    add_via(*v, net=pinnet[(ref, pin)], d=0.4, h=0.2)
    nfan += 1
print("fan-out vias (switch control, MCU):", nfan)



def via_ok(x, y, rv, ref, own=None, clr=0.25):
    if any(b[0] - rv < x < b[2] + rv and b[1] - rv < y < b[3] + rv for b in LABEL_BOXES):
        return False
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


# ------------------------------------------------------------ 7) zones: GND on both layers, strip and main apart
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


for lay_, nm in ((pcbnew.F_Cu, "F"), (pcbnew.B_Cu, "B")):
    zone(lay_, 0.0, SLOT_Y0 - 0.4, 0, f"GND_STRIP_{nm}")
    zone(lay_, SLOT_Y1 + 0.4, H, 0, f"GND_MAIN_{nm}")

# ------------------------------------------------------------ 8) silk + keep-outs
def text(s, x, y, size=1.0, angle=0, th=0.15, layer=pcbnew.F_SilkS, just=None):
    t = pcbnew.PCB_TEXT(board); t.SetText(s); t.SetPosition(P(x, y)); t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size))); t.SetTextThickness(mm(th))
    if angle:
        t.SetTextAngleDegrees(angle)
    if just == "left":
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
    board.Add(t)


for n in range(48):          # lamp names: one baseline per row, reading upward from just above the lamp
    row, i = divmod(n, 24)
    text(NAMES[n], LAMP_X0 + i * LAMP_DX, LABEL_BASE[row], 1.0, 90, just="left")
for (x0, y0, x1, y1) in ((0.0, SLOT_Y0 - 0.6, 80.6, SLOT_Y1 + 0.6),):
    ko = pcbnew.ZONE(board); ko.SetIsRuleArea(True)
    ko.SetDoNotAllowVias(True); ko.SetDoNotAllowTracks(True); ko.SetDoNotAllowPads(False)
    ko.SetDoNotAllowZoneFills(True); ko.SetDoNotAllowFootprints(False)
    ls_ = pcbnew.LSET(); [ls_.AddLayer(l) for l in (pcbnew.F_Cu, pcbnew.B_Cu)]
    ko.SetLayerSet(ls_); ko.SetZoneName("SLOT_KEEPOUT")
    ol = ko.Outline(); ol.NewOutline()
    for (x, y) in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        ol.Append(mm(OX + x), mm(OY + y))
    board.Add(ko)

# no via may land on a lamp name (silk_dfm would push the text off its grid)
for (x0, y0, x1, y1) in LABEL_BOXES:
    ko = pcbnew.ZONE(board); ko.SetIsRuleArea(True)
    ko.SetDoNotAllowVias(True); ko.SetDoNotAllowTracks(False); ko.SetDoNotAllowPads(False)
    ko.SetDoNotAllowZoneFills(False); ko.SetDoNotAllowFootprints(False)
    ls_ = pcbnew.LSET(); [ls_.AddLayer(l) for l in (pcbnew.F_Cu, pcbnew.B_Cu)]
    ko.SetLayerSet(ls_)
    ol = ko.Outline(); ol.NewOutline()
    for (x, y) in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        ol.Append(mm(OX + x), mm(OY + y))
    board.Add(ko)
text("COAXOLOTL", 90.0, 8.0, 2.0, 0, 0.3)
text("ctrl rev 0.1  z2labs", 90.0, 11.5, 1.0)
text("CERN-OHL-S-2.0", 90.0, 14.0, 1.0)
text("RTL-SDR", 97.5, YJ - 8.0, 0.8, 90)
text("RF CTRL", padpos[("J6", "1")][0] - 1.0, H - 9.6, 0.8, just="left")
for k, nm in enumerate(["3V3", "LNA", "V1", "V2", "V3", "PTH", "OUT", "FMN", "GND", "GND"]):
    q = padpos[("J6", str(k + 1))]
    text(nm, q[0], q[1] - 2.0, 0.7, th=0.12)

# ------------------------------------------------------------ save + project rules
for ref, fp in fps.items():
    if where.get(ref, (0, 99))[1] < SLOT_Y0:
        fp.Reference().SetLayer(pcbnew.F_Fab)

pcbnew.ZONE_FILLER(board).Fill(board.Zones())
out = os.path.join(PRJ, "coaxolotl.kicad_pcb")
board.Save(out)
import shutil
shutil.copy(out, os.path.join(PRJ, "coaxolotl_placed.kicad_pcb"))

pro = os.path.join(PRJ, "coaxolotl.kicad_pro")
pj = json.load(open(pro, encoding="utf-8"))
pj["net_settings"] = json.loads(json.dumps(PRO_ORIG.get("net_settings", {})))
ns = pj["net_settings"]
classes = ns.setdefault("classes", [])
base = next(c for c in classes if c["name"] == "Default")
base.update(clearance=0.15, track_width=0.2, via_diameter=0.6, via_drill=0.3)
for c in classes:
    c["clearance"] = 0.15
bd = pj.setdefault("board", {}).setdefault("design_settings", {}).setdefault("rules", {})
bd.update(min_through_hole_diameter=0.2, min_via_diameter=0.4, min_via_annular_width=0.075,
          min_copper_edge_clearance=0.3, min_clearance=0.15, min_track_width=0.15, min_hole_to_hole=0.25)
json.dump(pj, open(pro, "w", encoding="utf-8"), indent=2)
open(os.path.join(PRJ, "coaxolotl.kicad_dru"), "w", encoding="utf-8").write(
    "(version 1)\n"
    "# edge SMA: only the connector's own pads may touch the board edge and the milled notch\n"
    "(rule \"SMA edge pads\"\n  (condition \"(A.Type == 'Pad' && A.memberOfFootprint('J3')) || (B.Type == 'Pad' && B.memberOfFootprint('J3'))\")\n"
    "  (constraint edge_clearance (min 0mm))\n  (severity ignore))\n")
json.dump({r: list(v) for r, v in where.items()}, open(os.path.join(PRJ, "placement.json"), "w"), indent=0)
print("placed", len(where), "of", len(fps), "footprints ->", out)
