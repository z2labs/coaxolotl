"""JLC CPL entries that jlc_rotfix cannot fit: the air-launch SMA (J2/J3) has its own signal pad, so the EasyEDA land
pattern of C496550 does not match and the geometry-only fit is ambiguous (the legs are symmetric).

EasyEDA footprint of BWSMA-KE-P001: legs centred on the origin, flange (board edge) at local x = +2.70, body towards +x.
Our footprint: origin on the board edge, board inward +x. So the JLC origin sits 2.70 mm inside the board edge and the
JLC body must point out of the board: J2 (left edge, ours rot 0) -> JLC rot 180; J3 (right edge, ours rot 180) -> JLC 0.

Diode polarity: jlc_rotfix fits the EasyEDA land pattern by pad NUMBER. KiCad diodes and LEDs have pad 1 = cathode,
but some EasyEDA parts number the anode 1 (KT-0603R C2286 and KT-0603Y C2287 do, KT-0603G C12624 does not), so the
fit turns those parts round. For every D* part whose EasyEDA symbol (easyeda_cache/<LCSC>.json) names pin 1 "A",
the JLC rotation gets +180. CATHODE_NOT_PAD1 overrides parts whose symbol and land pattern disagree (the land
pattern's cathode mark wins: JLC places by it).

    python3 cpl_override.py <jlc dir>        rewrites <name>_cpl_jlc*.csv in place (reads <name>_pos_kicad.csv)
"""
import csv, glob, math, os, re, sys

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
import json


def pin1_is_anode(lcsc):
    f = os.path.join(D, "easyeda_cache", lcsc + ".json")
    if not os.path.exists(f):
        return None
    j = json.load(open(f, encoding="utf-8"))
    r = j.get("result", j)
    for s in r.get("dataStr", {}).get("shape", []):
        if s.startswith("P~"):
            g = s.split("^^")
            num = s.split("~")[3]
            name = g[3].split("~")[4] if len(g) > 3 else ""
            if num == "1":
                return name.strip().upper() in ("A", "ANODE", "+")
    return None


lcsc_of = {}
bomf = glob.glob(os.path.join(D, "*_bom_jlc.csv"))
for row in (csv.DictReader(open(bomf[0], encoding="utf-8")) if bomf else []):
    for d in row["Designator"].split(","):
        lcsc_of[d.strip()] = row["LCSC Part #"]
# EasyEDA parts whose symbol pin names disagree with their own land pattern: the cathode mark (chamfered silk
# corner) of LED0603-R-RD_ORANGE (XL-1608UOC-06, C965800) sits at pad 2 although its symbol calls pin 1 K; the
# identical land pattern of KT-0603Y (C2287) has the mark at pad 2 too and calls pin 2 K. JLC places by the land
# pattern's mark, so the mark decides: value = True when the marked cathode is NOT pad 1.
CATHODE_NOT_PAD1 = {"C965800": True}
FLIP = set()
for ref, code in lcsc_of.items():
    if re.fullmatch(r"D\d+", ref) and CATHODE_NOT_PAD1.get(code, pin1_is_anode(code)):
        FLIP.add(ref)
for f in glob.glob(os.path.join(D, "*_cpl_jlc*.csv")):
    rows = list(csv.reader(open(f, encoding="utf-8")))
    for r in rows[1:]:
        if r and r[0] in OV:
            r[1], r[2], r[4] = OV[r[0]]
        elif r and r[0] in FLIP and r[4] != "":
            r[4] = f"{(float(r[4]) + 180) % 360:g}"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
for k, v in OV.items():
    print(k, v)
print("diode cathode mark not on pad 1 in EasyEDA, rotated 180:", ", ".join(sorted(FLIP, key=lambda s: int(s[1:]))) or "none")
