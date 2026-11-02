"""Grow small vias for the JLC annular-ring check (Danger at 0.10 mm, Warning at 0.125 mm): drill 0.3 with a pad under
0.6 mm -> drill 0.2; a ring under 0.15 mm -> pad 0.5 mm where DRC stays clean, else 0.45 mm. Vias inside an SMD pad
(exposed-pad thermal vias) keep their size.

    kicad-python ringfix.py board.kicad_pcb
"""
import sys, json, subprocess, pcbnew
CLI = r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"
p = sys.argv[1]
mm, FM = pcbnew.ToMM, pcbnew.FromMM


def key(v):
    return (round(mm(v.GetPosition().x), 3), round(mm(v.GetPosition().y), 3))


def drc_bad(path):
    out = path + ".ring.json"
    subprocess.run([CLI, "pcb", "drc", "--severity-error", "--format", "json", "-o", out, path], capture_output=True)
    d = json.load(open(out, encoding="utf-8"))
    pts = set()
    for x in d["violations"]:
        for i in x["items"]:
            if i["description"].startswith("Via"):
                pts.add((round(i["pos"]["x"], 3), round(i["pos"]["y"], 3)))
    return pts, len(d["violations"]), len(d["unconnected_items"])


b = pcbnew.LoadBoard(p)
base, nv0, nu0 = drc_bad(p)
inpad = set()
for f in b.GetFootprints():
    for pad in f.Pads():
        if pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
            for v in b.GetTracks():
                if v.GetClass() == "PCB_VIA" and pad.HitTest(v.GetPosition()):
                    inpad.add(key(v))
want = {}
for v in b.GetTracks():
    if v.GetClass() != "PCB_VIA" or key(v) in inpad:
        continue
    d, h = mm(v.GetWidth(pcbnew.F_Cu)), mm(v.GetDrillValue())
    if h > 0.25 and d < 0.6:
        v.SetDrill(FM(0.2)); h = 0.2
    if (d - h) / 2 < 0.149:
        want[key(v)] = d; v.SetWidth(FM(0.5))
pcbnew.ZONE_FILLER(b).Fill(b.Zones()); pcbnew.SaveBoard(p, b)
for rnd in range(3):
    bad, nv, nu = drc_bad(p)
    bad -= base
    hit = [k for k in want if k in bad]
    print(f"ringfix round {rnd}: {len(want)} grown, {len(hit)} back down, errors {nv} (before {nv0}), unconnected {nu}")
    if not hit:
        break
    b = pcbnew.LoadBoard(p)
    for v in b.GetTracks():
        if v.GetClass() == "PCB_VIA" and key(v) in hit:
            v.SetWidth(FM(0.45 if rnd == 0 else min(0.45, want[key(v)])))   # last resort: the original size
    pcbnew.ZONE_FILLER(b).Fill(b.Zones()); pcbnew.SaveBoard(p, b)
