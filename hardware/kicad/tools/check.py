#!/usr/bin/env python3
"""Connectivity check for the generated schematic (works with KiCad 7's CLI,
which has no ERC; KiCad 8/9 users should also run `kicad-cli sch erc`).

1. Exports the netlist with kicad-cli and compares it pin-by-pin with the
   intent in build.py, which proves the generated stubs/labels connect as
   meant.
2. Flags single-pin nets, power inputs without a driver, and output conflicts.

    python3 hardware/kicad/tools/check.py [--pending NET ...]
"""
import argparse
import collections
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build  # noqa: E402
import schgen as g  # noqa: E402

KICAD = build.OUT
# nets that legitimately end on a sheet not drawn yet
PENDING: set = set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pending", nargs="*", default=sorted(PENDING))
    a = ap.parse_args()

    _, style_errors = build.main()
    with tempfile.TemporaryDirectory() as td:
        net_file = pathlib.Path(td) / "n.net"
        subprocess.run(["kicad-cli", "sch", "export", "netlist", "-o", str(net_file),
                        str(KICAD / f"{build.PROJECT}.kicad_sch")], check=True, capture_output=True)
        tree = g.parse(net_file.read_text())

    nets = {}
    for n in g.find_all(g.find(tree, "nets"), "net"):
        name = g.find(n, "name")[1].lstrip("/")
        nodes = [(g.find(x, "ref")[1], g.find(x, "pin")[1],
                  (g.find(x, "pintype") or [None, "?"])[1]) for x in g.find_all(n, "node")]
        nets[name] = nodes

    # pin -> net as KiCad sees it
    seen = {}
    for name, nodes in nets.items():
        for ref, pin, _ in nodes:
            seen[(ref, pin)] = name

    lib = g.Library(extra=[KICAD / "coaxolotl.kicad_sym"])
    errors, warns = [], []
    flags = set()
    want = collections.defaultdict(set)          # intended net -> {(ref, pin)}
    for sh in build.all_sheets(lib):
        flags |= sh.flags
        for p in sh.parts:
            for pin, net in p.nets.items():
                if net is None:
                    got = seen.get((p.ref, pin))
                    if got and len(nets.get(got, [])) > 1:
                        errors.append(f"{p.ref}.{pin}: meant NC, KiCad has net {got}")
                    continue
                want[net].add((p.ref, pin))
    got_groups = {name: {(r, pn) for r, pn, _ in nodes if not r.startswith("#")} for name, nodes in nets.items()}
    for net, pins in sorted(want.items()):
        kicad = {seen.get(rp) for rp in pins}
        if len(kicad) != 1 or None in kicad:
            errors.append(f"net {net}: pins split over KiCad nets {sorted(map(str, kicad))}")
            continue
        kname = kicad.pop()
        extra = got_groups[kname] - pins
        if extra:
            errors.append(f"net {net}: KiCad net {kname} also has {sorted(extra)} (short?)")

    for name, nodes in sorted(nets.items()):
        real = [n for n in nodes if not n[0].startswith("#")]
        types = [t for _, _, t in nodes]
        if name.startswith("unconnected-"):
            continue
        if len(real) < 2 and name.split("/")[-1] not in a.pending:
            warns.append(f"single-pin net {name}: {real}")
        if "power_in" in types and "power_out" not in types and name not in flags:
            errors.append(f"net {name}: power input(s) without a driver or PWR_FLAG")
        outs = [n for n in nodes if n[2] == "output"]
        if len(outs) > 1:
            errors.append(f"net {name}: multiple outputs {outs}")

    print(f"netlist: {len(nets)} nets, {len(seen)} pin connections")
    for w in warns:
        print("WARN ", w)
    errors += [f"style: {e}" for e in style_errors]
    for e in errors:
        print("ERROR", e)
    pend = [n for n in nets if n.split("/")[-1] in a.pending]
    if pend:
        print("pending (end on a sheet not drawn yet):", ", ".join(pend))
    print("OK" if not errors else f"{len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
