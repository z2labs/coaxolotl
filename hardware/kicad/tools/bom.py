#!/usr/bin/env python3
"""Grouped BOM from the generated schematic: hardware/bom.csv.

Runs kicad-cli (python-bom XML export) on the root sheet, so the BOM always
matches the schematic. Rows group identical value + footprint + DNP state.
"""
import csv
import pathlib
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

KICAD = pathlib.Path(__file__).resolve().parent.parent
OUT = KICAD.parent / "bom.csv"


def refkey(r):
    m = re.match(r"([A-Za-z#]+)(\d+)", r)
    return (m.group(1), int(m.group(2))) if m else (r, 0)


def main():
    with tempfile.TemporaryDirectory() as d:
        xml = pathlib.Path(d) / "bom.xml"
        subprocess.run(["kicad-cli", "sch", "export", "python-bom", "-o", str(xml),
                        str(KICAD / "coaxolotl.kicad_sch")], check=True, capture_output=True)
        root = ET.parse(xml).getroot()
    groups = {}
    for c in root.find("components"):
        ref = c.get("ref")
        if ref.startswith("#"):
            continue
        props = {p.get("name"): p.get("value") for p in c.findall("property")}
        fields = {f.get("name"): f.text or "" for f in c.findall("fields/field")}
        dnp = "dnp" in props
        key = (c.findtext("value"), c.findtext("footprint") or "", dnp)
        g = groups.setdefault(key, {"refs": [], "mpn": set(), "sheet": set(), "note": set()})
        g["refs"].append(ref)
        g["sheet"].add(props.get("Sheetname", ""))
        if fields.get("MPN"):
            g["mpn"].add(fields["MPN"])
        if fields.get("Note"):
            g["note"].add(f"{ref}: {fields['Note']}")
    rows = []
    for (value, fp, dnp), g in groups.items():
        refs = sorted(g["refs"], key=refkey)
        rows.append([" ".join(refs), len(refs), value, fp.split(":")[-1], "; ".join(sorted(g["mpn"])),
                     "DNP" if dnp else "", ", ".join(sorted(g["sheet"])), "; ".join(sorted(g["note"]))])
    rows.sort(key=lambda r: refkey(r[0].split()[0]))
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["refs", "qty", "value", "footprint", "mpn", "dnp", "sheet", "note"])
        w.writerows(rows)
    print(f"wrote {OUT} ({sum(r[1] for r in rows)} parts, {len(rows)} lines)")


if __name__ == "__main__":
    main()
