"""JLC CPL entries that jlc_rotfix cannot fit: the air-launch SMA (J2/J3) has its own signal pad, so the EasyEDA land
pattern of C496550 does not match and the geometry-only fit is ambiguous (the legs are symmetric).

EasyEDA footprint of BWSMA-KE-P001: legs centred on the origin, flange (board edge) at local x = +2.70, body towards +x.
Our footprint: origin on the board edge, board inward +x. So the JLC origin sits 2.70 mm inside the board edge and the
JLC body must point out of the board: J2 (left edge, ours rot 0) -> JLC rot 180; J3 (right edge, ours rot 180) -> JLC 0.

    python3 cpl_override.py <jlc dir>        rewrites <name>_cpl_jlc*.csv in place (reads <name>_pos_kicad.csv)
"""
import csv, glob, math, os, sys

D = sys.argv[1]
pos = {r["Ref"]: r for r in csv.DictReader(open(glob.glob(os.path.join(D, "*_pos_kicad.csv"))[0], encoding="utf-8"))}
OV = {}
for ref in ("J2", "J3"):
    if ref not in pos:
        continue
    x, y, rot = float(pos[ref]["PosX"]), float(pos[ref]["PosY"]), float(pos[ref]["Rot"]) % 360
    a = math.radians(rot)                       # our +x (board inward) in board coordinates (y up in the .pos file)
    jx, jy = x + 2.70 * math.cos(a), y + 2.70 * math.sin(a)
    OV[ref] = (f"{jx:.4f}mm", f"{jy:.4f}mm", f"{(rot + 180) % 360:.0f}")
for f in glob.glob(os.path.join(D, "*_cpl_jlc*.csv")):
    rows = list(csv.reader(open(f, encoding="utf-8")))
    for r in rows[1:]:
        if r and r[0] in OV:
            r[1], r[2], r[4] = OV[r[0]]
    with open(f, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
for k, v in OV.items():
    print(k, v)
