"""Minimal KiCad schematic generator (KiCad 7 file format, opens in 8/9).

Style: every symbol pin gets a short wire stub ending in a net label, a
power port, or a no-connect flag. Parts are placed in labelled functional
blocks. Connectivity is defined in Python, so it can be diffed and reviewed,
and the generated sheets pass kicad-cli ERC.
"""
from __future__ import annotations

import math
import pathlib
import re
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

KICAD_SYM_DIRS = [pathlib.Path("/usr/share/kicad/symbols")]
GRID = 1.27
STUB = 2.54

# --------------------------------------------------------------------------
# S-expressions


class Sym(str):
    """Bare (unquoted) atom."""


def parse(text: str):
    tok = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+')
    stack: list = [[]]
    for m in tok.finditer(text):
        t = m.group(0)
        if t == "(":
            stack.append([])
        elif t == ")":
            done = stack.pop()
            stack[-1].append(done)
        elif t.startswith('"'):
            stack[-1].append(t[1:-1].replace('\\"', '"').replace("\\\\", "\\"))
        else:
            stack[-1].append(Sym(t))
    return stack[0][0]


def dump(x, indent=0) -> str:
    pad = "  " * indent
    if isinstance(x, list):
        if not x:
            return "()"
        simple = all(not isinstance(e, list) for e in x)
        if simple:
            return "(" + " ".join(dump(e) for e in x) + ")"
        head = []
        rest = []
        for e in x:
            (rest if (rest or isinstance(e, list)) else head).append(e)
        out = "(" + " ".join(dump(e) for e in head)
        for e in rest:
            out += "\n" + pad + "  " + dump(e, indent + 1)
        return out + "\n" + pad + ")"
    if isinstance(x, Sym):
        return str(x)
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, (int, float)):
        s = f"{x:.4f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    s = str(x).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def find(node, key):
    for e in node:
        if isinstance(e, list) and e and e[0] == key:
            return e
    return None


def find_all(node, key):
    return [e for e in node if isinstance(e, list) and e and e[0] == key]


def uid() -> str:
    return str(_uuid.uuid4())


# --------------------------------------------------------------------------
# symbol libraries


class Library:
    def __init__(self, extra: List[pathlib.Path] = ()):
        self.files: Dict[str, pathlib.Path] = {}
        for d in KICAD_SYM_DIRS:
            for f in d.glob("*.kicad_sym"):
                self.files[f.stem] = f
        for f in extra:
            self.files[f.stem] = f
        self.cache: Dict[str, list] = {}
        self.raw: Dict[str, Dict[str, list]] = {}

    def _load(self, lib: str) -> Dict[str, list]:
        if lib not in self.raw:
            tree = parse(self.files[lib].read_text(encoding="utf-8"))
            self.raw[lib] = {e[1]: e for e in find_all(tree, "symbol")}
        return self.raw[lib]

    def get(self, lib_id: str) -> list:
        """Flattened symbol named 'Lib:Name', ready for lib_symbols."""
        if lib_id in self.cache:
            return self.cache[lib_id]
        lib, name = lib_id.split(":", 1)
        syms = self._load(lib)
        node = syms[name]
        ext = find(node, "extends")
        if ext:
            parent = self._flatten_children(syms, ext[1], name)
            props = {p[1]: p for p in find_all(node, "property")}
            out = [Sym("symbol"), name]
            for e in parent[2:]:
                if isinstance(e, list) and e[0] == "property" and e[1] in props:
                    out.append(props.pop(e[1]))
                elif isinstance(e, list) and e[0] == "extends":
                    continue
                else:
                    out.append(e)
            for p in props.values():
                out.insert(2, p)
            node = out
        node = [node[0], lib_id] + [e for e in node[2:] if not (isinstance(e, list) and e[0] == "extends")]
        self.cache[lib_id] = node
        return node

    def _flatten_children(self, syms, parent_name, new_name):
        p = syms[parent_name]
        if find(p, "extends"):
            p = self._flatten_children(syms, find(p, "extends")[1], parent_name)
        out = [p[0], new_name]
        for e in p[2:]:
            if isinstance(e, list) and e[0] == "symbol":
                sub = list(e)
                sub[1] = sub[1].replace(parent_name, new_name, 1)
                out.append(sub)
            else:
                out.append(e)
        return out

    def pins(self, lib_id: str) -> Dict[str, dict]:
        """number -> {x, y, angle, name, type} in symbol (y-up) coordinates, unit 1."""
        res = {}
        node = self.get(lib_id)

        def walk(n, unit):
            for e in n:
                if isinstance(e, list) and e and e[0] == "symbol":
                    m = re.search(r"_(\d+)_(\d+)$", e[1])
                    walk(e, int(m.group(1)) if m else unit)
                elif isinstance(e, list) and e and e[0] == "pin":
                    at = find(e, "at")
                    num = find(e, "number")[1]
                    name = find(e, "name")[1]
                    if unit in (0, 1):
                        res[num] = dict(x=float(at[1]), y=float(at[2]), angle=float(at[3]),
                                        name=name, type=str(e[1]))
        walk(node, 0)
        return res


# --------------------------------------------------------------------------
# geometry


def rot(x, y, deg):
    a = math.radians(deg)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def snap(v):
    return round(v / GRID) * GRID


def dir_to_angle(dx, dy):
    """sch direction (y down) -> label angle"""
    if abs(dx) > abs(dy):
        return 0 if dx > 0 else 180
    return 270 if dy > 0 else 90


# --------------------------------------------------------------------------
# sheet


POWER_PORTS = {"GND": ("power:GND", -1), "+3V3": ("power:+3V3", +1)}


@dataclass
class Part:
    ref: str
    lib_id: str
    value: str
    x: float
    y: float
    rot: int = 0
    footprint: str = ""
    nets: Dict[str, Optional[str]] = field(default_factory=dict)  # pin -> net / None(NC)
    fields: Dict[str, str] = field(default_factory=dict)
    dnp: bool = False


class Sheet:
    def __init__(self, lib: Library, project: str, title: str, file: str, paper="A3",
                 rev="0.1", company="z2labs", comment="", globals_=()):
        self.lib, self.project, self.title, self.file = lib, project, title, file
        self.paper, self.rev, self.company, self.comment = paper, rev, company, comment
        self.uuid = uid()
        self.sheet_uuid = uid()          # uuid of this sheet's block in the parent
        self.globals_ = set(globals_)
        self.zone = ""                   # default 'Zone' field for parts added next
        self.place: Dict[str, str] = {}  # ref -> 'Place' field (satellite of REF.PIN / edge)
        self.items: List[list] = []
        self.parts: List[Part] = []
        self.used_libs: Dict[str, list] = {}
        self.global_nets: set = set()
        self.flags: set = set()
        self.path = None  # set by Project
        self.pwr_n = 0
        self.sheets: List[Tuple["Sheet", float, float, float, float]] = []

    # -- primitives -------------------------------------------------------
    def wire(self, x1, y1, x2, y2):
        self.items.append([Sym("wire"), [Sym("pts"), [Sym("xy"), x1, y1], [Sym("xy"), x2, y2]],
                           [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("default")]],
                           [Sym("uuid"), uid()]])

    def label(self, net, x, y, angle, glob=False):
        just = {0: "left", 180: "right", 90: "left", 270: "right"}[angle]
        eff = [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]],
               [Sym("justify"), Sym(just), Sym("bottom")] if not glob else [Sym("justify"), Sym(just)]]
        if glob:
            self.global_nets.add(net)
            self.items.append([Sym("global_label"), net, [Sym("shape"), Sym("bidirectional")],
                               [Sym("at"), x, y, angle], [Sym("fields_autoplaced")], eff,
                               [Sym("uuid"), uid()],
                               [Sym("property"), "Intersheetrefs", "${INTERSHEET_REFS}",
                                [Sym("at"), x, y, 0],
                                [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]], Sym("hide")]]])
        else:
            self.items.append([Sym("label"), net, [Sym("at"), x, y, angle], [Sym("fields_autoplaced")],
                               eff, [Sym("uuid"), uid()]])

    def no_connect(self, x, y):
        self.items.append([Sym("no_connect"), [Sym("at"), x, y], [Sym("uuid"), uid()]])

    def text(self, s, x, y, size=1.27, bold=False):
        font = [Sym("font"), [Sym("size"), size, size]]
        if bold:
            font.append(Sym("bold"))
        self.items.append([Sym("text"), s, [Sym("at"), x, y, 0],
                           [Sym("effects"), font, [Sym("justify"), Sym("left"), Sym("bottom")]],
                           [Sym("uuid"), uid()]])

    # -- graphics (block diagram) ------------------------------------------
    def poly(self, pts, width=0.3, dash=False, color=None):
        stroke = [Sym("stroke"), [Sym("width"), width], [Sym("type"), Sym("dash" if dash else "solid")]]
        if color:
            stroke.append([Sym("color"), *color, 1])
        self.items.append([Sym("polyline"), [Sym("pts")] + [[Sym("xy"), x, y] for x, y in pts],
                           stroke, [Sym("fill"), [Sym("type"), Sym("none")]], [Sym("uuid"), uid()]])

    def arrow(self, pts, width=0.3, dash=False, color=None, head=1.6):
        self.poly(pts, width, dash, color)
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        L = math.hypot(x2 - x1, y2 - y1) or 1
        ux, uy = (x2 - x1) / L, (y2 - y1) / L
        bx, by = x2 - ux * head, y2 - uy * head
        px, py = -uy * head * 0.45, ux * head * 0.45
        tri = [(x2, y2), (bx + px, by + py), (bx - px, by - py), (x2, y2)]
        stroke = [Sym("stroke"), [Sym("width"), width], [Sym("type"), Sym("solid")]]
        if color:
            stroke.append([Sym("color"), *color, 1])
        self.items.append([Sym("polyline"), [Sym("pts")] + [[Sym("xy"), x, y] for x, y in tri],
                           stroke, [Sym("fill"), [Sym("type"), Sym("outline")]], [Sym("uuid"), uid()]])

    def block(self, x, y, w, h, title, lines=(), fill=(235, 235, 235)):
        self.items.append([Sym("rectangle"), [Sym("start"), x, y], [Sym("end"), x + w, y + h],
                           [Sym("stroke"), [Sym("width"), 0.35], [Sym("type"), Sym("solid")]],
                           [Sym("fill"), [Sym("type"), Sym("color")], [Sym("color"), *fill, 1]],
                           [Sym("uuid"), uid()]])
        n = 1 + len(lines)
        y0 = y + h / 2 - (n - 1) * 1.25
        self.ctext(title, x + w / 2, y0, 1.6, bold=True)
        for i, ln in enumerate(lines):
            self.ctext(ln, x + w / 2, y0 + 2.5 * (i + 1), 1.15)

    def ctext(self, s, x, y, size=1.27, bold=False, left=False):
        font = [Sym("font"), [Sym("size"), size, size]]
        if bold:
            font.append(Sym("bold"))
        eff = [Sym("effects"), font]
        if left:
            eff.append([Sym("justify"), Sym("left")])
        self.items.append([Sym("text"), s, [Sym("at"), x, y, 0], eff, [Sym("uuid"), uid()]])

    def box(self, x, y, w, h, title, notes: List[str] = ()):
        self.items.append([Sym("rectangle"), [Sym("start"), x, y], [Sym("end"), x + w, y + h],
                           [Sym("stroke"), [Sym("width"), 0.15], [Sym("type"), Sym("dash")]],
                           [Sym("fill"), [Sym("type"), Sym("none")]], [Sym("uuid"), uid()]])
        self.text(title, x + 1.27, y + 3.0, size=2.0, bold=True)
        for i, n in enumerate(notes):
            self.text(n, x + 1.27, y + h - 1.27 - (len(notes) - 1 - i) * 2.2, size=1.27)

    # -- symbols ----------------------------------------------------------
    def _use(self, lib_id):
        if lib_id not in self.used_libs:
            self.used_libs[lib_id] = self.lib.get(lib_id)

    def _instance(self, lib_id, ref, value, x, y, angle, footprint="", fields=None,
                  pins=(), dnp=False, in_bom=True, on_board=True, show_ref=True, place=None):
        self._use(lib_id)
        fields = fields or {}
        hide_ref = [] if show_ref else [Sym("hide")]
        node = [Sym("symbol"), [Sym("lib_id"), lib_id], [Sym("at"), x, y, angle], [Sym("unit"), 1],
                [Sym("in_bom"), Sym("yes" if in_bom else "no")],
                [Sym("on_board"), Sym("yes" if on_board else "no")],
                [Sym("dnp"), Sym("yes" if dnp else "no")], [Sym("uuid"), uid()]]
        props = [("Reference", ref, 2.54, not show_ref), ("Value", value, -2.54, False),
                 ("Footprint", footprint, 0, True), ("Datasheet", fields.pop("Datasheet", "~"), 0, True)]
        props += [(k, v, 0, True) for k, v in fields.items()]
        place = place or {}
        for name, val, dy, hidden in props:
            eff = [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]]]
            px, py, just = place.get(name, (x + 3.81, y + dy if dy else y, None))
            if just:
                eff.append([Sym("justify"), Sym(just)])
            if hidden:
                eff.append(Sym("hide"))
            fa = 90 if angle in (90, 270) else 0      # keep field text horizontal on screen
            node.append([Sym("property"), name, val, [Sym("at"), px, py, fa], eff])
        for p in pins:
            node.append([Sym("pin"), p, [Sym("uuid"), uid()]])
        node.append(("INSTANCES", ref))
        self.items.append(node)
        return node

    def pin_xy(self, lib_id, x, y, angle, num):
        p = self.lib.pins(lib_id)[num]
        rx, ry = rot(p["x"], p["y"], angle)
        # outward direction: opposite of the pin's own direction
        ox, oy = rot(-math.cos(math.radians(p["angle"])), -math.sin(math.radians(p["angle"])), angle)
        return x + rx, y - ry, ox, -oy

    def power(self, net, x, y, outward):
        lib_id, body = POWER_PORTS[net]
        dx, dy = outward
        if body < 0:   # GND: body below origin
            a = {(0, 1): 0, (0, -1): 180, (1, 0): 90, (-1, 0): 270}[(round(dx), round(dy))]
        else:          # +3V3: body above origin
            a = {(0, -1): 0, (0, 1): 180, (-1, 0): 90, (1, 0): 270}[(round(dx), round(dy))]
        self.pwr_n += 1
        self._instance(lib_id, f"#PWR{self.pwr_n:03d}", net, x, y, a, pins=["1"],
                       in_bom=False, on_board=False, show_ref=False)

    def pwr_flag(self, net, x, y):
        self.flags.add(net)
        self.pwr_n += 1
        self._instance("power:PWR_FLAG", f"#FLG{self.pwr_n:03d}", "PWR_FLAG", x, y, 0, pins=["1"],
                       in_bom=False, on_board=False, show_ref=False)
        p = self.lib.pins("power:PWR_FLAG")["1"]
        self.attach(net, x + p["x"], y - p["y"], 0, 1)

    def attach(self, net, px, py, ox, oy, stub=STUB):
        ex, ey = px + ox * stub, py + oy * stub
        self.wire(px, py, ex, ey)
        if net is None:
            self.items.pop()
            self.no_connect(px, py)
        elif net in POWER_PORTS:
            self.power(net, ex, ey, (ox, oy))
        else:
            self.label(net, ex, ey, dir_to_angle(ox, oy), glob=net in self.globals_)

    def add(self, p: Part):
        if self.zone:
            p.fields.setdefault("Zone", self.zone)
        if p.ref in self.place:
            p.fields.setdefault("Place", self.place[p.ref])
        pins = self.lib.pins(p.lib_id)
        missing = set(pins) - set(p.nets)
        if missing:
            raise ValueError(f"{p.ref}: unconnected pins {sorted(missing)} (map them or set None)")
        extra = set(p.nets) - set(pins)
        if extra:
            raise ValueError(f"{p.ref}: unknown pins {sorted(extra)}")
        pts = [self.pin_xy(p.lib_id, p.x, p.y, p.rot, n)[:2] for n in pins]
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        if len(pins) == 2 and abs(xs[0] - xs[1]) < 0.1:          # vertical 2-pin
            place = {"Reference": (p.x + 2.54, p.y - 1.27, "left"),
                     "Value": (p.x + 2.54, p.y + 1.27, "left")}
        elif len(pins) == 2:                                       # horizontal 2-pin
            place = {"Reference": (p.x, p.y - 3.81, None), "Value": (p.x, p.y + 3.81, None)}
        else:                                                      # IC
            vert = any(abs(self.pin_xy(p.lib_id, p.x, p.y, p.rot, n)[3]) > 0.5 for n in pins)
            if vert:   # pins on top/bottom too: text at the right edge, above/below the pin rows
                place = {"Reference": (max(xs), min(ys) - 2.54, "left"),
                         "Value": (max(xs), max(ys) + 2.54, "left")}
            else:
                place = {"Reference": (p.x, min(ys) - 5.08, None), "Value": (p.x, max(ys) + 5.08, None)}
        self._instance(p.lib_id, p.ref, p.value, p.x, p.y, p.rot, p.footprint, dict(p.fields),
                       pins=list(pins), dnp=p.dnp, place=place)
        for num in pins:
            px, py, ox, oy = self.pin_xy(p.lib_id, p.x, p.y, p.rot, num)
            self.attach(p.nets[num], px, py, ox, oy)
        self.parts.append(p)

    def subsheet(self, child: "Sheet", x, y, w=40.0, h=20.0):
        self.sheets.append((child, x, y, w, h))

    # -- output -----------------------------------------------------------
    def render(self, root_uuid: str, path: str, project: str, page: str, pages: Dict[str, str]):
        out = [Sym("kicad_sch"), [Sym("version"), 20230121], [Sym("generator"), Sym("eeschema")],
               [Sym("uuid"), self.uuid], [Sym("paper"), self.paper],
               [Sym("title_block"), [Sym("title"), self.title], [Sym("date"), "2026-10-26"],
                [Sym("rev"), self.rev], [Sym("company"), self.company],
                [Sym("comment"), 1, self.comment],
                [Sym("comment"), 2, "Generated by hardware/kicad/tools/build.py - edit the Python, not this file"]]]
        out.append([Sym("lib_symbols")] + list(self.used_libs.values()))
        for it in self.items:
            if it and isinstance(it[-1], tuple):
                _, ref = it[-1]
                it = it[:-1] + [[Sym("instances"), [Sym("project"), project,
                                                     [Sym("path"), path, [Sym("reference"), ref],
                                                      [Sym("unit"), 1]]]]]
            out.append(it)
        for child, x, y, w, h in self.sheets:
            out.append([Sym("sheet"), [Sym("at"), x, y], [Sym("size"), w, h], [Sym("fields_autoplaced")],
                        [Sym("stroke"), [Sym("width"), 0.1524], [Sym("type"), Sym("solid")]],
                        [Sym("fill"), [Sym("color"), 0, 0, 0, 0.0]], [Sym("uuid"), child.sheet_uuid],
                        [Sym("property"), "Sheetname", child.title, [Sym("at"), x, y - 0.7, 0],
                         [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]],
                          [Sym("justify"), Sym("left"), Sym("bottom")]]],
                        [Sym("property"), "Sheetfile", child.file, [Sym("at"), x, y + h + 0.6, 0],
                         [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]],
                          [Sym("justify"), Sym("left"), Sym("top")]]],
                        [Sym("instances"), [Sym("project"), project,
                                            [Sym("path"), "/" + root_uuid, [Sym("page"), pages[child.file]]]]]])
        if path == "/" + root_uuid:
            out.append([Sym("sheet_instances"), [Sym("path"), "/", [Sym("page"), "1"]]])
        return dump(out) + "\n"
