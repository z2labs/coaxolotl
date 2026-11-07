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


# ---------------------------------------------------------------- pure-Python .kicad_pcb reader (no pcbnew needed)
def sexpr(text):
    """minimal s-expression parser: lists of str tokens (quoted strings unquoted)"""
    tok = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')
    st, cur = [], []
    for m in tok.finditer(text):
        t = m.group(0)
        if t == "(":
            st.append(cur); cur = []
        elif t == ")":
            done = cur; cur = st.pop(); cur.append(done)
        else:
            cur.append(t[1:-1].replace('\\"', '"') if t[0] == '"' else t)
    return cur[0]


def _kids(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def _kid(node, key):
    k = _kids(node, key)
    return k[0] if k else None


class PcbFile:
    """pad -> net, values, pad positions and silk texts straight from the .kicad_pcb text (what gets fabricated)."""
    def __init__(self, path=None, text=None):
        import math
        t = sexpr(text if text is not None else open(path, encoding="utf-8").read())
        self.path, self.pads, self.val, self.at, self.padxy, self.layer = path, {}, {}, {}, {}, {}
        self.padsize, self.padtype, self.edges = {}, {}, []   # (ref,pad) -> (w,h); 'smd'/'thru_hole'/...; Edge.Cuts
        self.texts = []          # (text, x, y, layer)
        for fp in _kids(t, "footprint"):
            props = {p[1]: p[2] for p in _kids(fp, "property") if len(p) > 2}
            r = props.get("Reference", "?")
            self.val[r] = props.get("Value", "")
            a = _kid(fp, "at"); fx, fy = float(a[1]), float(a[2]); rot = float(a[3]) if len(a) > 3 else 0.0
            self.at[r] = (fx, fy, rot)
            self.layer[r] = _kid(fp, "layer")[1]
            c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
            for p in _kids(fp, "pad"):
                n = p[1]
                if not n:
                    continue
                net = _kid(p, "net")
                net = net[-1] if net else ""
                if net.startswith("unconnected-"):
                    net = ""
                if (r, n) in self.pads and self.pads[(r, n)] and not net:
                    continue
                self.pads[(r, n)] = net
                pa = _kid(p, "at"); px, py = float(pa[1]), float(pa[2])
                self.padxy[(r, n)] = (fx + px * c + py * s, fy - px * s + py * c)
                sz = _kid(p, "size"); prot = float(pa[3]) if len(pa) > 3 else 0.0
                w, h = float(sz[1]), float(sz[2])
                if round((prot - 0) / 90) % 2:            # pad angle is absolute in the file
                    w, h = h, w
                self.padsize[(r, n)] = (w, h); self.padtype[(r, n)] = p[2]
        self.holes = []          # (ref, x, y, drill) for np_thru_hole pads
        for fp in _kids(t, "footprint"):
            a = _kid(fp, "at")
            for p in _kids(fp, "pad"):
                if p[2] == "np_thru_hole":
                    d = _kid(p, "drill")
                    r = {q[1]: q[2] for q in _kids(fp, "property") if len(q) > 2}.get("Reference", "")
                    self.holes.append((r, float(a[1]), float(a[2]), float(d[1])))
        for k in ("gr_line", "gr_arc"):
            for g in _kids(t, k):
                if _kid(g, "layer")[1] == "Edge.Cuts":
                    st, en = _kid(g, "start"), _kid(g, "end")
                    self.edges.append(((float(st[1]), float(st[2])), (float(en[1]), float(en[2]))))
        for g in _kids(t, "gr_text"):
            a = _kid(g, "at")
            self.texts.append((g[1], float(a[1]), float(a[2]), _kid(g, "layer")[1]))

    def refs(self):
        return set(self.val)
