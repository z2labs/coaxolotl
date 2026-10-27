"""Hand-drawn schematic helpers on top of schgen.Sheet.

Rules enforced here (docs: AI PCB Dev schematic style guide):
  * power and ground only as power symbols (rails point up, GND down)
  * wires inside a block, hierarchical labels between sheets
  * orthogonal wires, horizontal text, junction dots added automatically,
    4-way junctions reported as errors
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

import schgen as g
from schgen import Sym, uid

U = 2.54
INTENT = (0, 0, 194)       # design-intent text colour


def on_grid(v):
    return abs(v / 1.27 - round(v / 1.27)) < 1e-3


class DSheet(g.Sheet):
    stubs = False

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.segs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        self.pinpts: Dict[Tuple[float, float], str] = {}
        self.byref: Dict[str, g.Part] = {}
        self.errors: List[str] = []
        self.hier: Dict[str, str] = {}

    # -- parts ------------------------------------------------------------
    def add(self, p: g.Part):
        super().add(p)
        self.byref[p.ref] = p
        for num in self.lib.pins(p.lib_id):
            x, y, _, _ = self.pin_xy(p.lib_id, p.x, p.y, p.rot, num, p.mirror)
            self.pinpts[(round(x, 3), round(y, 3))] = f"{p.ref}.{num}"
        return p

    def P(self, ref, num):
        p = self.byref[ref]
        x, y, ox, oy = self.pin_xy(p.lib_id, p.x, p.y, p.rot, num, p.mirror)
        return round(x, 3), round(y, 3), round(ox), round(oy)

    def pt(self, ref, num):
        return self.P(ref, num)[:2]

    # -- wires --------------------------------------------------------------
    def w(self, *pts):
        pts = [(round(x, 3), round(y, 3)) for x, y in pts]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if (x1, y1) == (x2, y2):
                continue
            if x1 != x2 and y1 != y2:
                self.errors.append(f"diagonal wire {(x1, y1)}-{(x2, y2)}")
            for v in (x1, y1, x2, y2):
                if not on_grid(v):
                    self.errors.append(f"off-grid wire point {v} in {(x1, y1)}-{(x2, y2)}")
            self.segs.append(((x1, y1), (x2, y2)))
        return pts[-1]

    def pw(self, ref, num, *pts):
        """wire starting at a pin; pts may use None to keep x or y: (x,None)."""
        cur = self.pt(ref, num)
        out = [cur]
        for x, y in pts:
            cur = (cur[0] if x is None else x, cur[1] if y is None else y)
            out.append(cur)
        return self.w(*out)

    # -- power ------------------------------------------------------------
    def rail(self, net, x, y):
        lib_id = {"+3V3": "power:+3V3", "GND": "power:GND"}.get(net, f"coaxolotl:{net}")
        self.pwr_n += 1
        self._instance(lib_id, f"#PWR{self.pwr_n:03d}", net, x, y, 0, pins=["1"],
                       in_bom=False, on_board=False, show_ref=False,
                       place={"Value": (x, y - 3.81 if net != "GND" else y + 3.81, None)})

    def rail_pin(self, ref, num, net, up=U):
        x, y, ox, oy = self.P(ref, num)
        gnd = net == "GND"
        d = 1 if gnd else -1
        if oy == d:                       # pin already points the right way
            end = self.w((x, y), (x, y + d * up))
        else:
            end = self.w((x, y), (x + ox * U, y + oy * U), (x + ox * U, y + oy * U + d * up))
        self.rail(net, *end)
        return end

    def gnd(self, ref, num):
        return self.rail_pin(ref, num, "GND")

    def flag(self, x, y, net):
        self.flags.add(net)
        self.pwr_n += 1
        self._instance("power:PWR_FLAG", f"#FLG{self.pwr_n:03d}", "PWR_FLAG", x, y, 0, pins=["1"],
                       in_bom=False, on_board=False, show_ref=False,
                       place={"Value": (x, y - 3.81, None)})

    # -- labels -----------------------------------------------------------
    def label(self, net, x, y, side="R"):
        """local net label (same sheet only); text reads away from the wire end."""
        self.items.append([Sym("label"), net, [Sym("at"), x, y, 0 if side == "R" else 180],
                           [Sym("fields_autoplaced")],
                           [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]],
                            [Sym("justify"), Sym("left" if side == "R" else "right"), Sym("bottom")]],
                           [Sym("uuid"), uid()]])

    def hlabel(self, net, x, y, side="R", shape="bidirectional"):
        """side R: label to the right of (x,y), text reads away from the wire."""
        self.hier[net] = shape
        angle = 0 if side == "R" else 180
        self.items.append([Sym("hierarchical_label"), net, [Sym("shape"), Sym(shape)],
                           [Sym("at"), x, y, angle], [Sym("fields_autoplaced")],
                           [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]],
                            [Sym("justify"), Sym("left" if side == "R" else "right")]],
                           [Sym("uuid"), uid()]])

    def nc(self, ref, num):
        self.no_connect(*self.pt(ref, num))

    # -- text -------------------------------------------------------------
    def heading(self, s, x, y):
        self.items.append([Sym("text"), s, [Sym("at"), x, y, 0],
                           [Sym("effects"), [Sym("font"), [Sym("size"), 2.54, 2.54], Sym("bold"), Sym("italic")],
                            [Sym("justify"), Sym("left"), Sym("bottom")]], [Sym("uuid"), uid()]])

    def intent(self, s, x, y, size=1.27):
        self.items.append([Sym("text"), s, [Sym("at"), x, y, 0],
                           [Sym("effects"), [Sym("font"), [Sym("size"), size, size], Sym("italic"),
                                             [Sym("color"), *INTENT, 1]],
                            [Sym("justify"), Sym("left"), Sym("bottom")]], [Sym("uuid"), uid()]])

    def frame(self, x, y, w, h, title):
        self.items.append([Sym("rectangle"), [Sym("start"), x, y], [Sym("end"), x + w, y + h],
                           [Sym("stroke"), [Sym("width"), 0.2], [Sym("type"), Sym("dash")]],
                           [Sym("fill"), [Sym("type"), Sym("none")]], [Sym("uuid"), uid()]])
        self.heading(title, x + 1.27, y - 1.27)

    # -- junctions --------------------------------------------------------
    def finish(self):
        ends = defaultdict(int)
        for a, b in self.segs:
            ends[a] += 1
            ends[b] += 1
        pts = set(ends) | {p for p in self.pinpts
                           if any((x1 == x2 == p[0] and min(y1, y2) < p[1] < max(y1, y2)) or
                                  (y1 == y2 == p[1] and min(x1, x2) < p[0] < max(x1, x2))
                                  for (x1, y1), (x2, y2) in self.segs)}
        for p in pts:
            interior = 0
            for (x1, y1), (x2, y2) in self.segs:
                if p in ((x1, y1), (x2, y2)):
                    continue
                if x1 == x2 == p[0] and min(y1, y2) < p[1] < max(y1, y2):
                    interior += 1
                if y1 == y2 == p[1] and min(x1, x2) < p[0] < max(x1, x2):
                    interior += 1
            degree = ends[p] + 2 * interior + (1 if p in self.pinpts else 0)
            if degree >= 3:
                self.items.append([Sym("junction"), [Sym("at"), *p], [Sym("diameter"), 0],
                                   [Sym("color"), 0, 0, 0, 0], [Sym("uuid"), uid()]])
            if degree >= 4:
                self.errors.append(f"4-way junction at {p} (style: max 3 wires per node)")
        # KiCad connects pins only at wire ends: split wires at every pin and
        # junction that lies on them, then emit the wire items
        cuts = set(self.pinpts) | {p for p in pts}
        for (x1, y1), (x2, y2) in self.segs:
            if x1 == x2:
                inner = sorted(p[1] for p in cuts if p[0] == x1 and min(y1, y2) < p[1] < max(y1, y2))
                ys = [min(y1, y2)] + inner + [max(y1, y2)]
                for a, b in zip(ys, ys[1:]):
                    self.wire(x1, a, x1, b)
            else:
                inner = sorted(p[0] for p in cuts if p[1] == y1 and min(x1, x2) < p[0] < max(x1, x2))
                xs = [min(x1, x2)] + inner + [max(x1, x2)]
                for a, b in zip(xs, xs[1:]):
                    self.wire(a, y1, b, y1)
        return self.errors
