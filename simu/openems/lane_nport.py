#!/usr/bin/env python3
"""Layout N-port of filter-bank lanes with openEMS (z2 twin kicad_sim), for co-simulation with the part models.

Every pad of every part in the chosen lanes gets a lumped port (pad to the reference plane under it, on the pad
edge facing the part's other pad, i.e. under the part body), plus the switch pins at both lane ends. The parts
themselves are not in the model: bank_sim.py --layout connects their models across the port pairs, so the field
simulation runs once and the parts can be swapped and swept afterwards. A shunt part's GND pad gets a port too:
its self-impedance is the dog-bone + via to In1.

Runs on office-z170 (openEMS z2 build, ~/z2/env.sh):
    . ~/z2/env.sh && python3 lane_nport.py coaxolotl.kicad_pcb --lanes 6 --out f6 [--fmax 1.5e9] [--variant ...]

Writes <out>/<board>.s<N>p (kicad_sim) and <out>/ports.json (port number -> "REF.PAD").
Two machines on one N-port: the plain run on one (excitations 1, 2, ...), --exc 20-11 on the other into its own <out>;
copy the excN folders together and run --collect --log <plain run log>.
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.expanduser("~/z2/twin"))
if os.environ.get("Z2_TWIN"):            # a newer kicad_sim.py than the machine's own (all machines on one version)
    sys.path.insert(0, os.path.expanduser(os.environ["Z2_TWIN"]))
import kicad_sim as ks

# lane k: parts (refs) and the switch pins at its ends (U8 RF(k+1), U11 RF(8-k)) - from the netlist (bank_sim.py)
SWITCH_PINS = {0: ("24", "19"), 1: ("2", "17"), 2: ("4", "15"), 3: ("6", "13"), 4: ("13", "6"), 5: ("15", "4"),
               6: ("17", "2"), 7: ("19", "24")}


def twin_version():
    """the kicad_sim.py actually imported: path, date, md5 - compare across machines before a run (and in results)"""
    import hashlib, time
    f = os.path.abspath(ks.__file__)
    return dict(path=f, mtime=time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(f))),
                md5=hashlib.md5(open(f, "rb").read()).hexdigest())


def lane_parts(k):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
    try:
        import bank_sim
        comps, pins = bank_sim.load_netlist()
        return bank_sim.branch_parts(k, comps, pins)[2]
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--lanes", default="6", help="comma list of lane numbers")
    ap.add_argument("--parts", help="JSON {lane: [refs]} (when the netlist is not next to this script)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fmin", type=float, default=10e6)
    ap.add_argument("--fmax", type=float, default=1.5e9)
    ap.add_argument("--margin", type=float, default=2.5)
    ap.add_argument("--variant", default=None)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--nf", type=int, default=601)
    ap.add_argument("--lane-only", action="store_true",
                    help="only the part pads (window = the lane itself, a fraction of the cells); the switch risers"
                         " are then left to the circuit model")
    ap.add_argument("--coarse", type=float, default=1.0, help="mesh coarsening (kicad_sim): >1 faster")
    ap.add_argument("--exc", help="split run: only these excitations, in this order (e.g. 20-11 on a second machine);"
                                  " each finished one leaves <out>/excN/done")
    ap.add_argument("--collect", action="store_true",
                    help="no simulation: S-matrix from the finished excN/sim folders (done marker, or rc=0 in --log)")
    ap.add_argument("--log", help="with --collect: the log of a plain run in the same <out> (excitations 1..k finished)")
    a = ap.parse_args()
    ks._setup_python()
    from CSXCAD.KiCadImport import KiCadBoard
    from CSXCAD import FabStackups
    from crop import crop
    b = KiCadBoard(a.board)
    FabStackups.Apply(b, FabStackups.Find(fab="JLCPCB", name="JLC04161H-7628")[0])
    top = b.copper_layers[0]
    pads = {(s.ref, str(s.pad)): s for s in b.shapes if s.kind == "pad" and s.layer == top}
    lanes = [int(x) for x in a.lanes.split(",")]
    given = json.load(open(a.parts)) if a.parts else {}
    sel = []
    for k in lanes:
        refs = given.get(str(k)) or lane_parts(k)
        assert refs, f"no parts for lane {k}"
        p8, p11 = SWITCH_PINS[k]
        if not a.lane_only:
            sel.append((("U8", p8), "switch"))
        for r in refs:
            for (rr, pn) in sorted(pads):
                if rr == r:
                    sel.append(((rr, pn), "part"))
        if not a.lane_only:
            sel.append((("U11", p11), "switch"))
    pts = [pads[k].pts for k, _ in sel]
    import numpy as np
    allp = np.vstack(pts)
    (x0, y0), (x1, y1) = allp.min(0) - a.margin, allp.max(0) + a.margin
    crop(b, x0, y0, x1, y1)
    pads = {(s.ref, str(s.pad)): s for s in b.shapes if s.kind == "pad" and s.layer == top}

    def centre(s):
        return s.pts.mean(0)

    ports, names = [], []
    for i, ((ref, pn), kind) in enumerate(sel):
        s = pads[(ref, pn)]
        c = centre(s)
        if kind == "part":
            other = [q for (rr, qn), q in pads.items() if rr == ref and qn != pn][0]
            d = centre(other) - c                      # port under the part body
        else:
            body = np.vstack([q.pts for (rr, _), q in pads.items() if rr == ref]).mean(0)
            d = c - body                               # switch pin: the edge facing away from the package
        side = ("+x" if d[0] > 0 else "-x") if abs(d[0]) > abs(d[1]) else ("+y" if d[1] > 0 else "-y")
        net = s.net
        if (net or "").upper() == "GND":
            s.net = "GNDPAD_%s_%s" % (ref, pn)        # the plane under the pad is the reference, not "own net"
        p = ks.make_port(b, s, i + 1, side=side)
        s.net = net
        ports.append(p)
        names.append(f"{ref}.{pn}")
    os.makedirs(a.out, exist_ok=True)
    json.dump(dict(board=os.path.basename(a.board), lanes=lanes, window=[float(x0), float(y0), float(x1), float(y1)],
                   ports=names, simulator=twin_version()), open(os.path.join(a.out, "ports.json"), "w"), indent=1)
    print(f"{len(ports)} ports, window {x1 - x0:.1f} x {y1 - y0:.1f} mm", flush=True)
    print("simulator:", twin_version(), flush=True)
    if a.exc or a.collect:
        split(a, b, ports)
    else:
        ks.simulate(a.board, None, a.fmin, a.fmax, a.out, ports=ports, board_obj=b, variant=a.variant,
                    threads=a.threads, nf=a.nf, coarse=a.coarse)


def _excs(spec, n):
    out = []
    for part in spec.split(","):
        if "-" in part:
            i, j = (int(x) for x in part.split("-"))
            out += list(range(i, j + 1)) if i <= j else list(range(i, j - 1, -1))
        else:
            out.append(int(part))
    assert all(1 <= e <= n for e in out), out
    return out


def split(a, b, ports):
    """Excitations of ks.simulate one by one (same mesh: the model is deterministic), so two machines can share one
    N-port; --collect rebuilds the port objects and reads the finished runs."""
    import numpy as np
    n = len(ports)
    for i, p in enumerate(ports):
        p["number"] = i + 1
        p.setdefault("z0", 50.0)
    f = np.linspace(a.fmin, a.fmax, a.nf)
    if not a.collect:
        for e in _excs(a.exc, n):
            cdir = os.path.join(a.out, "exc%d" % e)
            if os.path.exists(os.path.join(cdir, "done")):
                continue
            FDTD, CSX, pobj, meta = ks.build(b, ports, a.fmin, a.fmax, e, coarse=a.coarse)
            os.makedirs(cdir, exist_ok=True)
            xml = os.path.join(cdir, "model.xml")
            FDTD.Write2XML(xml)
            print("excitation %d/%d: mesh %s (%.2f MCells), engine %s" % (e, n, meta["mesh"], meta["mcells"], a.variant), flush=True)
            r = ks.run_engine(xml, os.path.join(cdir, "sim"), a.variant, a.threads)
            print("  rc=%s %.1f s, %s timesteps" % (r["rc"], r["wall_s"], r["timesteps"]), flush=True)
            if r["rc"] != 0:
                raise RuntimeError("openEMS failed in " + cdir)
            open(os.path.join(cdir, "done"), "w").write("rc=0\n")
        return
    k = sum(1 for line in open(a.log) if "rc=0" in line) if a.log else 0
    S = np.zeros((len(f), n, n), complex)
    missing = []
    for e in range(1, n + 1):
        cdir = os.path.join(a.out, "exc%d" % e)
        if not (e <= k or os.path.exists(os.path.join(cdir, "done"))):
            missing.append(e); continue
        _, _, pobj, _ = ks.build(b, ports, a.fmin, a.fmax, e, coarse=a.coarse)
        for p, pd in zip(pobj, ports):
            p.CalcPort(os.path.join(cdir, "sim"), f, ref_impedance=pd.get("z0", 50.0))
        inc = pobj[e - 1].uf_inc
        for i, p in enumerate(pobj):
            S[:, i, e - 1] = p.uf_ref / inc
    if missing:
        sys.exit("not finished: excitations %s" % missing)
    src = os.path.splitext(os.path.basename(a.board))[0]
    ks.write_touchstone(os.path.join(a.out, "%s.s%dp" % (src, n)), f, S,
                        comment="openEMS-z2labs kicad_sim via lane_nport --collect, %s\nports: %s" % (
                            os.path.basename(a.board), ", ".join("%d=%s" % (p["number"], p["ref"]) for p in ports)))
    print("wrote", os.path.join(a.out, "%s.s%dp" % (src, n)))


if __name__ == "__main__":
    main()
