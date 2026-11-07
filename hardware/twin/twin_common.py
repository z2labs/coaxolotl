"""Shared twin plumbing (after fg23-cw-trainer/twin): board netlist from the .kicad_pcb (what gets fabricated),
node graph, value parsing, report."""
import re, sys, os


class Board:
    """pad -> net map plus part values/positions, read from the fabricated .kicad_pcb."""
    def __init__(self, path):
        import pcbnew
        self.path = path
        b = pcbnew.LoadBoard(path)
        self.b = b
        self.pads = {}     # (ref, padnum) -> net name ('' = unconnected)
        self.val = {}      # ref -> value string
        self.fp = {}       # ref -> footprint object
        self.ep = {}       # ref -> pad number of the largest pad (exposed pad)
        for fp in b.GetFootprints():
            r = fp.GetReference(); self.val[r] = fp.GetValue(); self.fp[r] = fp
            big = (0, None)
            for p in fp.Pads():
                n = p.GetNumber()
                if not n:
                    continue
                net = p.GetNetname()
                if (r, n) in self.pads and self.pads[(r, n)] and not net:
                    continue
                self.pads[(r, n)] = net
                bb = p.GetBoundingBox()
                a = bb.GetWidth() * bb.GetHeight()
                if a > big[0]:
                    big = (a, n)
            self.ep[r] = big[1]

    def refs(self):
        return set(self.val)


class Netlist:
    """Same interface as Board, read from a KiCad s-expression netlist (pre-layout run)."""
    def __init__(self, path):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
        from sexpr import parse, find, find1
        t = parse(open(path, encoding="utf-8").read())
        self.path, self.pads, self.val, self.fp, self.ep = path, {}, {}, {}, {}
        for c in find(find1(t, "components"), "comp"):
            self.val[find1(c, "ref")[1]] = find1(c, "value")[1]
        for n in find(find1(t, "nets"), "net"):
            name = find1(n, "name")[1]
            if name.startswith("unconnected-"):
                name = ""
            for node in find(n, "node"):
                self.pads[(find1(node, "ref")[1], find1(node, "pin")[1])] = name

    def refs(self):
        return set(self.val)


def parse_value(v):
    """'4k7' 4700, '22k1' 22100, '0R', '100', '5m' 0.005, '1M' -> ohms (first token); None if not a resistor value"""
    t = v.split()[0] if v.split() else v
    m = re.fullmatch(r"(\d+)([RkKmM])(\d*)", t)
    if not m:
        m2 = re.fullmatch(r"(\d+(?:\.\d+)?)", t)
        return float(m2.group(1)) if m2 else None
    a, u, b = m.groups()
    mult = {"R": 1, "k": 1e3, "K": 1e3, "m": 1e-3, "M": 1e6}[u]
    return float(f"{a}.{b or 0}") * mult


def parse_cap(v):
    """'100n' 1e-7, '4u7' 4.7e-6, '22u 25V', '470u', '3n3', '47p' -> farads"""
    t = v.split()[0] if v.split() else v
    m = re.fullmatch(r"(\d+)([pnuµ])(\d*)", t)
    if not m:
        return None
    a, u, b = m.groups()
    return float(f"{a}.{b or 0}") * {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6}[u]


class Nodes:
    """union-find over nets; conductors merged. node(ref,pad) -> node id"""
    def __init__(self, pads, vals, conductor):
        self.pads, self.vals = pads, vals
        nets = {n for n in pads.values() if n}
        self.u = {n: n for n in nets}
        self.parts = {}
        for (r, n), net in pads.items():
            self.parts.setdefault(r, {})[n] = net
        for r, pp in self.parts.items():
            for (a, c) in conductor(r, vals.get(r, ""), pp):
                if a and c:
                    self._un(a, c)

    def _f(self, x):
        while self.u[x] != x:
            self.u[x] = self.u[self.u[x]]; x = self.u[x]
        return x

    def _un(self, a, b):
        self.u[self._f(a)] = self._f(b)

    def node(self, rp):
        net = self.pads.get(rp)
        if net is None:
            raise KeyError(rp)
        return self._f(net) if net else ("NC", rp)

    def refs_on(self, node):
        return {r for (r, n), net in self.pads.items() if net and self._f(net) == node}

    def two_pin(self):
        for r, pp in self.parts.items():
            if len(pp) == 2:
                yield r, list(pp.items())


class Report:
    def __init__(self, title):
        self.title, self.rows = title, []

    def add(self, sev, group, msg):
        self.rows.append((sev, group, msg))

    def counts(self):
        c = {"PASS": 0, "WARN": 0, "FAIL": 0}
        for s, _, _ in self.rows:
            c[s] = c.get(s, 0) + 1
        return c

    def fails(self):
        return [r for r in self.rows if r[0] == "FAIL"]

    def show(self, quiet_pass=False, out=sys.stdout):
        print(f"== {self.title}", file=out)
        for s, g, m in self.rows:
            if quiet_pass and s == "PASS":
                continue
            print(f"  {s:4} [{g}] {m}", file=out)
        c = self.counts()
        print(f"  -> {c['PASS']} pass, {c['WARN']} warn, {c['FAIL']} FAIL", file=out)
