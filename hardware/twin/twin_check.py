"""Layer 1 - pad -> net of the fabricated control board (.kicad_pcb) against spec_ctrl.py (written from data sheets
and docs, never from our schematic). Also lamp mapping, lamp colours, silk labels and the snap-off strip link.

usage: twin_check.py <board.kicad_pcb> [--selftest] [-q]
exit: 0 clean, 1 FAIL, 2 missing input, 3 selftest did not catch an injected fault
"""
import copy, math, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from twin_common import PcbFile, Report
import spec_ctrl as S

GND = "GND"


# ---------------------------------------------------------------- values
def ohms(v):
    """'10k/5%' 1e4, '0R39/1%' 0.39, '2k2x4/5%(CAY16)' 2200, '680R' 680, '1M/1%' 1e6"""
    m = re.match(r"(\d+)([RkKM])(\d*)", v or "")
    if not m:
        return None
    a, u, b = m.groups()
    return float(f"{a}.{b or 0}") * {"R": 1, "k": 1e3, "K": 1e3, "M": 1e6}[u]


def farads(v):
    """'100nF/16V' 1e-7, '100pF/50V/C0G' 1e-10, '0.1F/5.5V' 0.1, '4u7' 4.7e-6"""
    m = re.match(r"(\d+(?:\.\d+)?)([pnuµ]?)(\d*)F?", v or "")
    if not m or not m.group(0):
        return None
    a, u, b = m.groups()
    x = float(a) if not b else float(f"{a}.{b}")
    return x * {"": 1, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6}[u]


def close(a, b, tol=0.011):
    return a is not None and b is not None and abs(a - b) <= tol * abs(b)


# ---------------------------------------------------------------- board view
class View:
    def __init__(self, B):
        self.B = B
        self.parts = {}
        for (r, n), net in B.pads.items():
            self.parts.setdefault(r, {})[n] = net

    def rp(self, x):
        """(ref, function name or pad number) -> (ref, pad)"""
        r, f = x
        tab = S.PINTABLES.get(r)
        if tab and f in tab:
            return r, tab[f]
        if r.startswith("D") and f in S.DIODE:
            return r, S.DIODE[f]
        return r, f

    def net(self, x):
        return self.B.pads.get(self.rp(x))

    def two(self, prefix):
        """two-pin parts whose refdes is prefix+digits -> (ref, {net_a, net_b} as tuple)"""
        for r, pp in self.parts.items():
            if re.fullmatch(prefix + r"\d+", r) and len(pp) == 2:
                yield r, tuple(pp.values())

    def between(self, prefix, a, b):
        return [r for r, (x, y) in self.two(prefix) if {x, y} == {a, b} and a and b]

    def refs_on(self, net):
        return {r for (r, n), x in self.B.pads.items() if x and x == net}


# ---------------------------------------------------------------- checks
def check(B):
    V, rep = View(B), Report("twin_check - control board, layer 1 (pad -> net vs data sheets and docs)")
    ok = lambda c, g, m: rep.add("PASS" if c else "FAIL", g, m)

    def need(x):
        n = V.net(x)
        if n is None:
            rep.add("FAIL", "pad", f"{x[0]}.{V.rp(x)[1]} ({x[1]}) does not exist on the board")
        return n

    def res(a, b, val, g, msg):
        na, nb = need(a) if isinstance(a, tuple) else a, need(b) if isinstance(b, tuple) else b
        rs = V.between("R", na, nb)
        good = [r for r in rs if val is None or close(ohms(B.val.get(r)), val)]
        ok(bool(good), g, f"{msg}: " + (f"{good[0]} {B.val[good[0]]}" if good else
                                        f"none between {na} and {nb} (found {[(r, B.val[r]) for r in rs]})"))

    def cap(a, b, val, g, msg):
        rs = V.between("C", a, b)
        good = [r for r in rs if close(farads(B.val.get(r)), val, 0.05)]
        ok(bool(good), g, f"{msg}: " + (f"{good[0]} {B.val[good[0]]}" if good else
                                        f"none between {a} and {b} (found {[(r, B.val[r]) for r in rs]})"))

    for a in S.A:
        k = a[0]
        if k == "gnd":
            nets = [need(x) for x in a[1]]
            ok(all(n == GND for n in nets), k, f"{a[2]}: {nets}")
        elif k == "same":
            nets = [need(x) for x in a[1]]
            ok(all(nets) and len(set(nets)) == 1, k, f"{a[2]}: {sorted(set(map(str, nets)))}")
        elif k == "diff":
            nets = [need(x) for x in a[1]]
            ok(all(nets) and len(set(nets)) == len(nets), k, f"{a[2]}: {nets}")
        elif k == "nc":
            n = need(a[1]); ok(n == "", k, f"{a[2]}: {n!r}")
        elif k == "pullup":
            res(a[1], a[2], a[3], k, a[4])
        elif k == "pulldown":
            res(a[1], GND, a[2], k, a[3])
        elif k == "res_gnd":
            res(a[1], GND, a[2], k, a[3])
        elif k in ("res_between", "thru_r"):
            res(a[1], a[2], a[3], k, a[4])
        elif k == "cap_gnd":
            cap(need(a[1]), GND, a[2], k, a[3])
        elif k == "cap_between":
            cap(need(a[1]), need(a[2]), a[3], k, a[4])
        elif k == "divider":
            res(a[1], a[2], a[3], k, a[5] + " (top)")
            res(a[2], GND, a[4], k, a[5] + " (bottom)")
        elif k == "only":
            n = need(a[1]); got = V.refs_on(n) - {a[1][0]} if n else set()
            ok(got == a[2], k, f"{a[3]}: {sorted(got)}")
        elif k == "part":
            ok(a[2] in B.val.get(a[1], ""), k, f"{a[3]}: {a[1]} = {B.val.get(a[1])!r}")
        elif k == "value":
            v = B.val.get(a[1], "").replace(" ", "")
            ok(v.lower().startswith(a[2].lower()), k, f"{a[3]}: {a[1]} = {v!r}")
        elif k == "walk":
            src, dst, hops = need(a[1]), need(a[2]), a[3]
            seen, front = {src}, {src}
            for _ in range(hops):
                front = {(y if x == f else x) for f in front for r, (x, y) in V.two("R") if f in (x, y)
                         and f not in (GND, "")} - seen
                seen |= front
            ok(bool(src) and dst in seen, k, f"{a[4]}: {src} -> {dst}")
        elif k == "chain":
            cur, path, good = need(a[1]), [], True
            for r in a[2]:
                pp = V.parts.get(r, {})
                nets = list(pp.values())
                if len(nets) != 2 or cur not in nets or nets[0] == nets[1]:
                    good = False; path.append(f"{r}?"); break
                cur = nets[1] if nets[0] == cur else nets[0]; path.append(r)
            ok(good and cur == need(a[3]), k, f"{a[4]}: {' -> '.join(path)} ends on {cur}")
        elif k == "parallel":
            pa, pb = V.parts.get(a[1], {}), V.parts.get(a[2], {})
            ok(set(pa.values()) == set(pb.values()) and len(set(pa.values())) == 2 and close(ohms(B.val.get(a[1])), a[3]),
               k, f"{a[4]}: {a[1]} {B.val.get(a[1])} on {sorted(pa.values())}, {a[2]} on {sorted(pb.values())}")
        elif k == "led":
            kn, an, rail = need((a[1], "K")), need((a[1], "A")), need(a[2])
            ok(kn == GND and bool(V.between("R", an, rail)), k, f"{a[3]}: K {kn}, A {an}")
        elif k == "lamps":
            lamps(B, V, rep)
        elif k == "strip_link":
            strip_link(B, V, rep)
        elif k == "silk_lamps":
            silk_lamps(B, rep)
        elif k == "silk_j6":
            silk_j6(B, rep)
        else:
            rep.add("FAIL", "spec", f"unknown assertion {k}")
    for p in S.PHYSICAL:
        rep.add("WARN", "physical", p)
    return rep


def lamps(B, V, rep):
    vmain = V.net((S.U_IDEAL, "VOUT"))
    vled = {y if x == vmain else x for r, (x, y) in V.two("FB") if vmain in (x, y)}
    bad = []
    for n in range(48):
        d = f"D{7 + n}"
        reg, q = S.lamp_output(n)
        an, kn, qn = V.net((d, "A")), V.net((d, "K")), V.net((reg, q))
        if an not in vled:
            bad.append(f"lamp {n} {d}: anode on {an}, not the ferrite-fed lamp rail"); continue
        hit = None
        for (r, p), x in B.pads.items():
            if x == kn and re.fullmatch(r"RN\d+", r):
                other = {a: b for a, b in S.RN_PAIRS}
                other.update({b: a for a, b in S.RN_PAIRS})
                hit = (r, p, B.pads.get((r, other[p])))
        if not hit:
            bad.append(f"lamp {n} {d}: cathode {kn} reaches no resistor array"); continue
        r, p, far = hit
        if far != qn:
            bad.append(f"lamp {n} {S.LAMPS[n]} {d}: via {r}.{p} lands on {far}, expected {reg}.{q} ({qn})")
        if not close(ohms(B.val.get(r)), S.LAMP_R):
            bad.append(f"lamp {n} {d}: {r} = {B.val.get(r)}, expected 2.2 k")
        if S.COLOUR[n] and S.COLOUR[n] not in B.val.get(d, "").upper():
            bad.append(f"lamp {n} {S.LAMPS[n]} {d}: colour {B.val.get(d)!r}, docs/panel.md says {S.COLOUR[n]}")
    for b in bad:
        rep.add("FAIL", "lamps", b)
    if not bad:
        rep.add("PASS", "lamps", "48 lamps: anode on the lamp rail, cathode -> 2.2 k -> the register output hal_panel "
                                 "drives, colour as docs/panel.md")


def strip_link(B, V, rep):
    j4 = {p: x for (r, p), x in B.pads.items() if r == "J4"}
    j5 = {p: x for (r, p), x in B.pads.items() if r == "J5"}
    rep.add("PASS" if j4 and j4 == j5 else "FAIL", "strip",
            f"snap-off links J4/J5 wired 1:1 ({len(j4)} pins)" if j4 == j5 else
            f"J4 and J5 differ: {[(p, j4.get(p), j5.get(p)) for p in sorted(set(j4) | set(j5)) if j4.get(p) != j5.get(p)]}")
    inner = {x for (r, p), x in B.pads.items() if r in S.STRIP_CORE and x}
    outer = {x for (r, p), x in B.pads.items() if r not in S.STRIP_CORE and r not in ("J4", "J5") and x}
    cross = inner & outer
    miss = cross - set(j4.values())
    rep.add("PASS" if not miss else "FAIL", "strip",
            f"every net crossing the break line is on the link: {sorted(cross)}" if not miss else
            f"nets cross the break line without a link pin: {sorted(miss)}")


def _nearest(B, xy, cands):
    return min(cands, key=lambda t: math.hypot(t[1] - xy[0], t[2] - xy[1]))


def silk_lamps(B, rep):
    texts = [t for t in B.texts if t[3] == "F.SilkS"]
    bad = []
    for n in range(48):
        d = f"D{7 + n}"
        if d not in B.at:
            bad.append(f"{d} missing"); continue
        t = _nearest(B, B.at[d][:2], texts)
        dist = math.hypot(t[1] - B.at[d][0], t[2] - B.at[d][1])
        name = S.LAMPS[n]
        if not (t[0] == name or (len(t[0]) >= 2 and name.startswith(t[0]))) or dist > 6:
            bad.append(f"lamp {n} {d}: nearest silk {t[0]!r} ({dist:.1f} mm), expected {name!r}")
    for b in bad:
        rep.add("FAIL", "silk", b)
    if not bad:
        rep.add("PASS", "silk", "48 lamp labels match the bandplan / frame / status names")


def silk_j6(B, rep):
    texts = [t for t in B.texts if t[3] == "F.SilkS"]
    bad = []
    for i, want in enumerate(S.J6_SILK):
        xy = B.padxy.get(("J6", str(i + 1)))
        if not xy:
            bad.append(f"J6.{i + 1} missing"); continue
        t = _nearest(B, xy, texts)
        if t[0] != want:
            bad.append(f"J6.{i + 1}: nearest silk {t[0]!r}, expected {want!r}")
    for b in bad:
        rep.add("FAIL", "silk", b)
    if not bad:
        rep.add("PASS", "silk", "J6 pin labels match the docs/hardware.md table")


# ---------------------------------------------------------------- selftest: injected faults must be caught
def _swap(B, a, b):
    B.pads[a], B.pads[b] = B.pads[b], B.pads[a]


def _set(B, rp, net):
    B.pads[rp] = net


def _swap_text(B, a, b):
    ia = next(i for i, t in enumerate(B.texts) if t[0] == a)
    ib = next(i for i, t in enumerate(B.texts) if t[0] == b)
    ta, tb = B.texts[ia], B.texts[ib]
    B.texts[ia], B.texts[ib] = (ta[0],) + tb[1:], (tb[0],) + ta[1:]


WITNESSES = [
    ("INA180A3 IN+/IN- swapped", lambda B: _swap(B, ("U20", "3"), ("U20", "4")), "INA180A3 IN"),
    ("INA180A2 IN+/IN- swapped", lambda B: _swap(B, ("U3", "3"), ("U3", "4")), "INA180A2 IN+"),
    ("LM66100 CE to GND", lambda B: _set(B, ("U1", "3"), GND), "CE tied"),
    ("TPS2553 EN/FAULT swapped", lambda B: _swap(B, ("U2", "3"), ("U2", "4")), "TPS2553 EN"),
    ("J6 SW_V1/SW_V2 swapped", lambda B: _swap(B, ("J6", "3"), ("J6", "4")), "SW_V1"),
    ("595 SHCP/STCP swapped on U16", lambda B: _swap(B, ("U16", "11"), ("U16", "12")), "LED_SCK"),
    ("lamp D30 reversed", lambda B: _swap(B, ("D30", "1"), ("D30", "2")), "lamp 23"),
    ("coax shunt bypassed", lambda B: [_set(B, rp, B.pads[("R35", "2")]) for rp in
                                       (("L7", "1"), ("R35", "1"), ("U20", "3"))], "shunt"),
    ("load ahead of the coax shunt", lambda B: _set(B, ("R1", "1"), B.pads[("R35", "1")]), "every load is measured"),
    ("register chain broken", lambda B: _set(B, ("U17", "14"), ""), "chain"),
    ("beacon tap to ground", lambda B: _set(B, ("C47", "2"), GND), "DC block"),
    ("FAULT pull-up 10 k", lambda B: B.val.__setitem__("R6", "10k/5%"), "FAULT open drain"),
    ("C2D pull-up missing", lambda B: _set(B, ("R34", "1"), ""), "bootloader"),
    ("lamp order swapped inside RN3", lambda B: _swap(B, ("RN3", "5"), ("RN3", "6")), "via RN3"),
    ("lamp 36 fitted red", lambda B: B.val.__setitem__("D43", "RED(0603)"), "colour"),
    ("lamp labels swapped", lambda B: _swap_text(B, "THRU", "LF"), "nearest silk"),
    ("J6 labels swapped", lambda B: _swap_text(B, "V1", "V2"), "J6.3"),
    ("strip link pins crossed", lambda B: _swap(B, ("J5", "4"), ("J5", "5")), "J4 and J5 differ"),
    ("LED_CH3 brought across", lambda B: _set(B, ("R13", "2"), B.pads[("U16", "9")]), "cross the break line"),
    ("A3 replaced by A2 on the coax monitor", lambda B: B.val.__setitem__("U20", "INA180A2IDBVR(SOT23-5)"), "100 V/V"),
    ("SENSE divider 22 k -> 10 k", lambda B: B.val.__setitem__("R4", "10k/1%"), "SENSE (P0.2) (bottom)"),
    ("MCP1700 in/out swapped", lambda B: _swap(B, ("U4", "2"), ("U4", "3")), "LDO"),
]


def selftest(B0):
    base = {m for s, g, m in check(B0).rows if s == "FAIL"}
    missed = []
    for name, mut, key in WITNESSES:
        B = copy.deepcopy(B0)
        mut(B)
        new = [m for s, g, m in check(B).rows if s == "FAIL" and m not in base]
        hit = [m for m in new if key in m]
        print(f"  {'caught' if hit else 'MISSED'}  {name}" + (f"  <- {hit[0][:110]}" if hit else f"  (new FAILs: {new[:2]})"))
        if not hit:
            missed.append(name)
    return missed


def main(argv):
    args = [a for a in argv if not a.startswith("-")]
    if not args or not os.path.exists(args[0]):
        print("usage: twin_check.py <board.kicad_pcb> [--selftest] [-q]"); return 2
    B = PcbFile(args[0])
    rc = 0
    if "--selftest" in argv:
        print("== selftest (injected faults)")
        missed = selftest(B)
        print(f"  -> {len(WITNESSES) - len(missed)}/{len(WITNESSES)} caught")
        if missed:
            rc = 3
    rep = check(B)
    rep.show(quiet_pass="-q" in argv)
    return rc or (1 if rep.fails() else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
