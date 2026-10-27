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
RF_W, RF_CLR = 0.35, 0.2
PWR_W = 0.4
VIA_PAD_INSET = 0.17


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
    ("pass3_rest", ["*"], [], ["-top", "F.Cu:4", "-via", "1500", "-it", "80"]),
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
            hx, hy = max(0.01, (x1 - x0) / 2 - VIA_PAD_INSET), max(0.01, (y1 - y0) / 2 - VIA_PAD_INSET)
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


def main():
    shutil.copy(PLACED, BOARD)
    rules_nets = net_rules()
    summary = []
    b = pcbnew.LoadBoard(BOARD)
    for name, only, closed, args in PASSES:
        dsn, ses, rep, rj = (os.path.join(OUT, name + e) for e in (".dsn", ".ses", "_report.json", "_rules.json"))
        export_dsn(b, dsn, closed)
        json.dump({"ignore_nets": ["GND"], "nets": rules_nets, "only": "|".join(only), "smooth": True,
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
        b = pcbnew.LoadBoard(BOARD)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(BOARD, b)
    print("routed ->", BOARD, summary)


if __name__ == "__main__":
    main()
