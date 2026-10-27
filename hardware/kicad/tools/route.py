"""Route the Coaxolotl board with srouter (srouter docs/USAGE.md: DSN -> srouter -> SES, layer roles per pass).

    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" hardware/kicad/tools/route.py

Input : hardware/kicad/coaxolotl_placed.kicad_pcb (written by pcb_build.py)
Output: hardware/kicad/coaxolotl.kicad_pcb (routed), hardware/kicad/route/<pass>.{dsn,ses,log,json}

GND is never routed: In1 is the solid GND plane, F/In2/B carry GND fills, every GND pad has its own via.
  pass 1 rf     the RF nets alone, F.Cu only (CPWG over the In1 plane), vias almost forbidden
  pass 2 power  coax DC, hold-up, LNA supply, +3V3: In2 + B.Cu open, F.Cu at a cost
  pass 3 rest   control and lamp nets: In2 + B.Cu open, F.Cu at a higher cost
Style: oct + smoothing (USAGE.md known limits). After the passes: via drill repair and zone refill.
"""
import os, sys, re, json, shutil, subprocess, time, fnmatch
import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PRJ = os.path.normpath(os.path.join(HERE, ".."))
OUT = os.path.join(PRJ, "route")
SR = os.environ.get("SROUTER", r"C:\Users\RF\Documents\GitHub\srouter-feat\target\release\srouter.exe")
BOARD = os.path.join(PRJ, "coaxolotl.kicad_pcb")
PLACED = os.path.join(PRJ, "coaxolotl_placed.kicad_pcb")
os.makedirs(OUT, exist_ok=True)

pro = json.load(open(os.path.join(PRJ, "coaxolotl.kicad_pro"), encoding="utf-8"))
PATS = pro["net_settings"]["netclass_patterns"]
RF = [p["pattern"] for p in PATS if p["netclass"] == "RF"]
POWER = [p["pattern"] for p in PATS if p["netclass"] == "POWER"]
assert RF and POWER, "net classes missing in coaxolotl.kicad_pro: run pcb_build.py first"
RF_W, RF_CLR = 0.25, 0.2       # routed at the QFN pin width, widened to RF_FAT afterwards
RF_FAT = 0.35                  # 50 ohm CPWG on JLC04161H-7628 (0.21 mm to In1) with the 0.25 mm pour gap
PWR_W = 0.4
VIA_PAD_INSET = -0.12          # keep-out = pad grown by 0.12 mm: the via ring ends >= 0.15 mm off the pad (JLC DFM)


def pad_min():
    b = pcbnew.LoadBoard(PLACED)
    small = {}
    for fp in b.GetFootprints():
        for p in fp.Pads():
            n = p.GetNetname()
            if n and p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
                s = p.GetSize(pcbnew.F_Cu)
                small[n] = min(small.get(n, 99), pcbnew.ToMM(min(s.x, s.y)))
    return small


def net_rules():
    small = pad_min()
    out = [{"match": n, "width_mm": RF_W, "clearance_mm": RF_CLR} for n in RF]
    for g in POWER:
        ds = [d for n, d in small.items() if fnmatch.fnmatchcase(n, g)]
        w = max(0.2, min([PWR_W] + [round(d * 0.95, 2) for d in ds]))
        if ds and min(ds) < 0.3:
            w = 0.25
        out.append({"match": g, "width_mm": w, "clearance_mm": 0.2})
    return out


PASSES = [
    ("pass1_rf", RF, ["In2.Cu", "B.Cu"], ["-via", "50000", "-it", "80"]),
    ("pass2_power", POWER, [], ["-top", "F.Cu:3", "-via", "3000", "-it", "60"]),
    ("pass3_strip", ["Net-(D*-K)", "Net-(RN*", "/Panel/LED_CH*", "/MCU/LED_*", "Net-(J1-*"], [], ["-top", "F.Cu:2", "-via", "1200", "-it", "70"]),
    ("pass4_rest", ["*"], [], ["-top", "F.Cu:4", "-via", "1500", "-it", "25"]),
]


def keepouts(board):
    """vias off SMD pads (JLC DFM): a via keep-out on the closed In1 GND plane over every SMD pad, inset so a
    0.6 mm via ring ends clear of the pad - srouter blocks vias through every keep-out layer"""
    out = []
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            bb = p.GetBoundingBox()
            x0, y0, x1, y1 = (pcbnew.ToMM(v) for v in (bb.GetX(), bb.GetY(), bb.GetRight(), bb.GetBottom()))
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            hx, hy = max(0.01, (x1 - x0) / 2 - VIA_PAD_INSET), max(0.01, (y1 - y0) / 2 - VIA_PAD_INSET)   # negative inset grows it
            pts = [(cx - hx, cy - hy), (cx + hx, cy - hy), (cx + hx, cy + hy), (cx - hx, cy + hy)]
            out.append('    (keepout "" (polygon In1.Cu 0 ' + " ".join(f"{x * 1000:.0f} {-y * 1000:.0f}" for x, y in pts) + "))")
    return "\n".join(out)


def export_dsn(board, path, closed):
    assert pcbnew.ExportSpecctraDSN(board, path), "DSN export failed"
    t = open(path, encoding="utf-8").read()
    t = t.replace("    (boundary", keepouts(board) + "\n    (boundary", 1)
    for l in closed:
        t = re.sub(r"\(layer " + re.escape(l) + r"\s*\(type \w+\)", f"(layer {l} (type power)", t)
    open(path, "w", encoding="utf-8").write(t)


def import_ses(board, ses):
    assert pcbnew.ImportSpecctraSES(board, ses), "SES import failed: " + ses
    for t in board.GetTracks():   # the import resets every via drill to the default
        if t.GetClass() == "PCB_VIA":
            w = pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu))
            t.SetDrill(pcbnew.FromMM(0.2 if w < 0.5 else 0.3))


def main(repair_only=False):
    if not repair_only:
        shutil.copy(PLACED, BOARD)
    rules_nets = net_rules()
    summary = []
    b = pcbnew.LoadBoard(BOARD)
    if repair_only:
        b = cleanup(b)
    else:
        for name, only, closed, args in PASSES:
            b = run_pass(b, name, only, closed, args, rules_nets, summary)
    # repair: connections srouter reports done but KiCad sees open (a terminal dropped by smoothing or by the tidy)
    for rnd in range(2):
        open_nets = unconnected_nets()
        print(f"repair {rnd + 1}: open nets {open_nets}", flush=True)
        if not open_nets:
            break
        rf = [n for n in open_nets if n in RF]
        other = [n for n in open_nets if n not in RF]
        if rf:
            b = run_pass(b, f"repair{rnd + 1}_rf", rf, ["In2.Cu", "B.Cu"], ["-via", "50000", "-it", "40"],
                         rules_nets, summary, ign_rf=False)
        if other:
            b = run_pass(b, f"repair{rnd + 1}", other, [], ["-top", "F.Cu:3", "-via", "1500", "-it", "40"],
                         rules_nets, summary)
        b = cleanup(b)
    print("RF widened:", fatten_rf(b))
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(BOARD, b)
    print("routed ->", BOARD, summary)


def drc_json():
    cli = r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"
    out = os.path.join(OUT, "drc_unconnected.json")
    subprocess.run([cli, "pcb", "drc", "--format", "json", "-o", out, BOARD], capture_output=True)
    return json.load(open(out, encoding="utf-8"))


def _delete_uuids(path, uuids):
    """delete via / segment blocks by uuid in the board text (pcbnew Remove()+Save() is not safe on KiCad 10)"""
    t = open(path, encoding="utf-8").read()
    n = 0
    for u in uuids:
        k = t.find(f'(uuid "{u}")')
        if k < 0:
            continue
        a = max(t.rfind("(via", 0, k), t.rfind("(segment", 0, k), t.rfind("(arc", 0, k))
        depth, e = 0, a
        while True:
            ch = t[e]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            e += 1
        ls = t.rfind("\n", 0, a) + 1
        le = t.find("\n", e)
        t = t[:ls] + t[le + 1:] if t[ls:a].strip() == "" and t[e + 1:le].strip() == "" else t[:a] + t[e + 1:]
        n += 1
    open(path, "w", encoding="utf-8").write(t)
    return n


def cleanup(b):
    """co-located same-net vias collapse to one; dangling vias and track stubs that DRC reports go (the router's
    spare fan-out vias, the tidy's leftovers)"""
    T = lambda v: (pcbnew.ToMM(v.x), pcbnew.ToMM(v.y))
    pcbnew.SaveBoard(BOARD, b)
    vias = [t for t in b.GetTracks() if t.GetClass() == "PCB_VIA"]
    dup, seen = [], {}
    for v in vias:
        k = (v.GetNetCode(), round(T(v.GetPosition())[0], 3), round(T(v.GetPosition())[1], 3))
        if k in seen:
            dup.append(v.m_Uuid.AsString())
        else:
            seen[k] = v
    merged = _delete_uuids(BOARD, dup)
    removed = 0
    for _ in range(3):
        d = drc_json()
        spots = [(x["type"], x["items"][0]["pos"]["x"], x["items"][0]["pos"]["y"])
                 for x in d["violations"] if x["type"] in ("via_dangling", "track_dangling")]
        if not spots:
            break
        b = pcbnew.LoadBoard(BOARD)
        kill = []
        for t in b.GetTracks():
            if t.GetNetname() == "GND":
                continue
            for typ, x, y in spots:
                if typ == "via_dangling" and t.GetClass() == "PCB_VIA":
                    q = T(t.GetPosition())
                    if abs(q[0] - x) < 0.01 and abs(q[1] - y) < 0.01:
                        kill.append(t.m_Uuid.AsString()); break
                if typ == "track_dangling" and t.GetClass() != "PCB_VIA":
                    a_, c_ = T(t.GetStart()), T(t.GetEnd())
                    if any(abs(p[0] - x) < 0.01 and abs(p[1] - y) < 0.01
                           for p in (a_, c_, ((a_[0] + c_[0]) / 2, (a_[1] + c_[1]) / 2))):
                        kill.append(t.m_Uuid.AsString()); break
        removed += _delete_uuids(BOARD, kill)
    print(f"cleanup: {merged} duplicate vias, {removed} dangling items removed", flush=True)
    return pcbnew.LoadBoard(BOARD)


def unconnected_nets():
    cli = r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"
    out = os.path.join(OUT, "drc_unconnected.json")
    subprocess.run([cli, "pcb", "drc", "--format", "json", "-o", out, BOARD], capture_output=True)
    d = json.load(open(out, encoding="utf-8"))
    nets = set()
    for u in d.get("unconnected_items", []):
        for it in u["items"]:
            m = re.search(r"\[([^\]]+)\]", it["description"])
            if m and m.group(1) != "GND":
                n = m.group(1)
                if n.startswith("Net-("):          # DRC prints the unescaped name; the netlist keeps {slash}
                    n = n.replace("/", "{slash}")
                nets.add(n)
    return sorted(nets)


def run_pass(b, name, only, closed, args, rules_nets, summary, ign_rf=None):
    if True:
        dsn, ses, rep, rj = (os.path.join(OUT, name + e) for e in (".dsn", ".ses", "_report.json", "_rules.json"))
        export_dsn(b, dsn, closed)
        ign = ["GND"] + (RF if (name != "pass1_rf" if ign_rf is None else ign_rf) else [])
        json.dump({"ignore_nets": ign, "nets": rules_nets, "only": "|".join(only), "smooth": True,
                   "style": "oct", "pad_entry": True}, open(rj, "w"), indent=1)
        cmd = [SR, "-de", dsn, "-do", ses, "-rules", rj, "-report", rep] + args
        print(">>", " ".join(cmd), flush=True)
        t0 = time.time()
        logf = os.path.join(OUT, name + ".log")
        with open(logf, "w", encoding="utf-8") as fh:
            p = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True)
        log = open(logf, encoding="utf-8", errors="replace").read()
        bad = [l.strip() for l in log.splitlines() if l.strip().startswith("!")]
        print(f"   {name}: exit {p.returncode}, {time.time() - t0:.0f} s, unrouted: {len(bad)}", flush=True)
        for l in bad[:40]:
            print("   ", l)
        if p.returncode != 0 or not os.path.exists(ses):
            print(log[-3000:]); sys.exit(1)
        import_ses(b, ses)
        summary.append((name, len(bad)))
        pcbnew.SaveBoard(BOARD, b)
        return pcbnew.LoadBoard(BOARD)


# ------------------------------------------------------------ RF tracks to 50 ohm width where the clearance allows
def _d_pt_seg(p, a, b):
    vx, vy = b[0] - a[0], b[1] - a[1]
    L = vx * vx + vy * vy
    k = 0 if L == 0 else max(0, min(1, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / L))
    return ((a[0] + k * vx - p[0]) ** 2 + (a[1] + k * vy - p[1]) ** 2) ** 0.5


def _d_seg_seg(a, b, c, d):
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    if (cross(a, b, c) * cross(a, b, d) < 0) and (cross(c, d, a) * cross(c, d, b) < 0):
        return 0.0
    return min(_d_pt_seg(a, c, d), _d_pt_seg(b, c, d), _d_pt_seg(c, a, b), _d_pt_seg(d, a, b))


def _d_seg_rect(a, b, r):
    x0, y0, x1, y1 = r
    if (x0 <= a[0] <= x1 and y0 <= a[1] <= y1) or (x0 <= b[0] <= x1 and y0 <= b[1] <= y1):
        return 0.0
    cs = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return min(_d_seg_seg(a, b, cs[i], cs[(i + 1) % 4]) for i in range(4))


def fatten_rf(b, clr=RF_CLR + 0.01, end=0.7):
    T = lambda v: (pcbnew.ToMM(v.x), pcbnew.ToMM(v.y))
    rf = set(RF)
    obst = []          # (net, kind, geom, half width)
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if not p.IsOnLayer(pcbnew.F_Cu):
                continue
            bb = p.GetBoundingBox()
            obst.append((p.GetNetname(), "r", (pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY()),
                                               pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())), 0))
    tr = []
    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA":
            obst.append((t.GetNetname(), "c", T(t.GetPosition()), pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2))
        elif t.GetLayer() == pcbnew.F_Cu:
            obst.append((t.GetNetname(), "s", (T(t.GetStart()), T(t.GetEnd())), pcbnew.ToMM(t.GetWidth()) / 2))
            if t.GetNetname() in rf:
                tr.append(t)

    def ok(net, a, c, hw):
        for (n, k, g, h) in obst:
            if n == net:
                continue
            if k == "r":
                d = _d_seg_rect(a, c, g)
            elif k == "c":
                d = _d_pt_seg(g, a, c) - h
            else:
                d = _d_seg_seg(a, c, g[0], g[1]) - h
            if d < hw + clr:
                return False
        return True

    n_full = n_part = 0
    for t in tr:
        net = t.GetNetname(); a, c = T(t.GetStart()), T(t.GetEnd())
        if pcbnew.ToMM(t.GetWidth()) >= RF_FAT - 1e-3:
            continue
        L = ((c[0] - a[0]) ** 2 + (c[1] - a[1]) ** 2) ** 0.5
        if L < 2 * end + 0.3:
            pieces = [(a, c)]
        else:          # pin escapes stay thin where they must, the middle goes wide
            u = ((c[0] - a[0]) / L, (c[1] - a[1]) / L)
            p1 = (a[0] + u[0] * end, a[1] + u[1] * end); p2 = (c[0] - u[0] * end, c[1] - u[1] * end)
            pieces = [(a, p1), (p1, p2), (p2, c)]
        fat = [ok(net, x, y, RF_FAT / 2) for x, y in pieces]
        if not any(fat):
            continue
        for (x, y), f in zip(pieces, fat):
            if f:
                obst.append((net, "s", (x, y), RF_FAT / 2))     # other RF nets see the new copper
        if all(fat) or len(pieces) == 1:
            t.SetWidth(pcbnew.FromMM(RF_FAT)); n_full += 1
            continue
        lay = t.GetLayer(); w0 = t.GetWidth(); ni = t.GetNet()
        b.Remove(t)
        for (x, y), f in zip(pieces, fat):
            s = pcbnew.PCB_TRACK(b)
            s.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x[0]), pcbnew.FromMM(x[1])))
            s.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(y[0]), pcbnew.FromMM(y[1])))
            s.SetWidth(pcbnew.FromMM(RF_FAT) if f else w0); s.SetLayer(lay); s.SetNet(ni); b.Add(s)
        n_part += 1
    return f"{n_full} segments full width, {n_part} with thin ends"


if __name__ == "__main__":
    main(repair_only="--repair" in sys.argv)
