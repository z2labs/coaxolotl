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
PENDING = {"LED_SER", "LED_SCK", "LED_RCK", "VLED_TODO"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pending", nargs="*", default=sorted(PENDING))
    a = ap.parse_args()

    build.main()
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
    for sheet_fn in (build.sheet_power, build.sheet_mcu):
        sh = sheet_fn(lib)
        flags |= sh.flags
        for p in sh.parts:
            for pin, want in p.nets.items():
                got = seen.get((p.ref, pin))
                if want is None:
                    if got and not got.startswith("unconnected-") and len(nets.get(got, [])) > 1:
                        errors.append(f"{p.ref}.{pin}: meant NC, KiCad has net {got}")
                    continue
                want_n = want.lstrip("+") if want == "+3V3" else want
                if got is None or (got != want and got != want_n and got.split("/")[-1] != want):
                    errors.append(f"{p.ref}.{pin}: want {want}, KiCad has {got}")

    for name, nodes in sorted(nets.items()):
        real = [n for n in nodes if not n[0].startswith("#")]
        types = [t for _, _, t in nodes]
        if name.startswith("unconnected-"):
            continue
        if len(real) < 2 and name not in a.pending:
            warns.append(f"single-pin net {name}: {real}")
        if "power_in" in types and "power_out" not in types and name not in flags:
            errors.append(f"net {name}: power input(s) without a driver or PWR_FLAG")
        outs = [n for n in nodes if n[2] == "output"]
        if len(outs) > 1:
            errors.append(f"net {name}: multiple outputs {outs}")

    print(f"netlist: {len(nets)} nets, {len(seen)} pin connections")
    for w in warns:
        print("WARN ", w)
    for e in errors:
        print("ERROR", e)
    pend = [n for n in a.pending if n in nets]
    if pend:
        print("pending (end on a sheet not drawn yet):", ", ".join(pend))
    print("OK" if not errors else f"{len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
