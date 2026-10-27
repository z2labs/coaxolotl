#!/usr/bin/env python3
"""Coaxolotl host keyer: talk to the mast-head box through the RTL-SDR bias-T.

The bias-T is the whole link. A frame is 15 short power gaps:
  short gap (30 ms) = 0, long gap (70 ms) = 1, 100 ms of power between,
  at least 400 ms of steady power before the frame.
  state frame:  sync 1 0 1 | bank[2:0] lna quiet clr | inverse of those 6 bits
  config frame: sync 0 1 0 | panel[1:0] 0 0 0 0      | inverse of those 6 bits

Examples
  coaxolotl.py bands
  coaxolotl.py send --band 2m --lna                    # pyrtlsdr, device 0
  coaxolotl.py send --bank 3 --backend rtl_biast -d 1
  coaxolotl.py send --bank 6 --lna --backend dry-run | firmware/build/sim
  coaxolotl.py panel bands                             # lamps: full | bands | off

Library use (keep your own RtlSdr handle open while streaming):
  from coaxolotl import Frame, play
  play(lambda on: sdr.set_bias_tee(on), Frame(bank=6, lna=True).edges())
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
BANDPLAN = ROOT / "bandplan.json"

SYNC = (1, 0, 1)          # state frame
SYNC_CONFIG = (0, 1, 0)   # config frame
PANEL_MODES = {"full": 0, "bands": 1, "off": 2}


@dataclass(frozen=True)
class Timing:
    """Link timing in ms. Must stay inside the firmware windows (config.h)."""
    gap0: float = 30.0
    gap1: float = 70.0
    on: float = 100.0
    pre: float = 400.0

    def check(self) -> None:
        if not (8 <= self.gap0 and self.gap1 >= 1.8 * self.gap0 and self.gap1 <= 120):
            raise ValueError("gaps must satisfy 8 <= gap0, gap1 >= 1.8*gap0, gap1 <= 120 ms")
        if not (40 <= self.on <= 250):
            raise ValueError("on-time must be 40..250 ms")
        if self.pre < 330:
            raise ValueError("pre-frame idle must be >= 330 ms")


@dataclass(frozen=True)
class Frame:
    bank: int
    lna: bool = False
    quiet: bool = False
    clr: bool = False

    @property
    def payload(self) -> int:
        if not 0 <= self.bank <= 7:
            raise ValueError("bank must be 0..7")
        return (self.bank << 3) | (int(self.lna) << 2) | (int(self.quiet) << 1) | int(self.clr)

    def bits(self) -> List[int]:
        return _bits(SYNC, self.payload)

    def edges(self, timing: Timing = Timing(), t0: float = 0.0) -> List[Tuple[float, int]]:
        """(t_ms, level) list: power on, idle, then one gap per bit; ends powered."""
        return _edges(self.bits(), timing, t0)


@dataclass(frozen=True)
class ConfigFrame:
    """Box settings that are not part of the RF state; the box keeps them in flash.

    panel: 0 = full (every lamp), 1 = bands (F0..F7 only), 2 = off (PWR lamp only).
    """
    panel: int = 0

    @property
    def payload(self) -> int:
        if self.panel not in (0, 1, 2):
            raise ValueError("panel must be 0 (full), 1 (bands) or 2 (off)")
        return self.panel << 4

    def bits(self) -> List[int]:
        return _bits(SYNC_CONFIG, self.payload)

    def edges(self, timing: Timing = Timing(), t0: float = 0.0) -> List[Tuple[float, int]]:
        return _edges(self.bits(), timing, t0)


def _bits(sync: Tuple[int, int, int], payload: int) -> List[int]:
    data = [(payload >> i) & 1 for i in range(5, -1, -1)]
    return list(sync) + data + [b ^ 1 for b in data]


def _edges(bits: List[int], timing: Timing, t0: float) -> List[Tuple[float, int]]:
    timing.check()
    t = t0
    ev = [(t, 1)]
    t += timing.pre
    for b in bits:
        ev.append((t, 0))
        t += timing.gap1 if b else timing.gap0
        ev.append((t, 1))
        t += timing.on
    return ev


# --------------------------------------------------------------------------
# band plan

def load_bands() -> list:
    return json.loads(BANDPLAN.read_text(encoding="utf-8"))["branches"]


def find_band(name: str) -> dict:
    bands = load_bands()
    key = name.lower().replace(" ", "")
    exact = [b for b in bands if b["name"].lower() == key]
    if exact:
        return exact[0]
    hits = [b for b in bands if key in b["name"].lower() or key in b["bands"].lower()]
    if len(hits) != 1:
        names = ", ".join(b["name"] for b in bands)
        raise SystemExit(f"band '{name}' matches {len(hits)} branches; use one of: {names}")
    return hits[0]


# --------------------------------------------------------------------------
# backends: a callable taking True/False

def backend_pyrtlsdr(index: int) -> Tuple[Callable[[bool], None], Callable[[], None]]:
    try:
        from rtlsdr import RtlSdr  # type: ignore
    except ImportError:
        raise SystemExit("pyrtlsdr missing: pip install pyrtlsdr  (or use --backend rtl_biast)")
    sdr = RtlSdr(device_index=index)
    if not hasattr(sdr, "set_bias_tee"):
        sdr.close()
        raise SystemExit("this pyrtlsdr/librtlsdr has no set_bias_tee(); update or use rtl_biast")
    return (lambda on: sdr.set_bias_tee(bool(on))), sdr.close


def backend_rtl_biast(index: int, exe: str) -> Tuple[Callable[[bool], None], Callable[[], None]]:
    path = shutil.which(exe)
    if not path:
        raise SystemExit(f"'{exe}' not found (rtl-sdr-blog drivers provide rtl_biast)")

    def set_bias(on: bool) -> None:
        subprocess.run([path, "-d", str(index), "-b", "1" if on else "0"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return set_bias, (lambda: None)


# --------------------------------------------------------------------------
# player

def play(set_bias: Callable[[bool], None], edges: Iterable[Tuple[float, int]],
         spin_ms: float = 2.0) -> List[float]:
    """Drive the bias-T on an absolute schedule. Returns per-edge lateness (ms).

    Constant latency of the backend cancels out (both edges of a gap are
    late by the same amount); only its jitter matters.
    """
    late = []
    t0 = time.perf_counter()
    for t_ms, lvl in edges:
        target = t0 + t_ms / 1000.0
        while True:
            left = target - time.perf_counter()
            if left <= 0:
                break
            if left > spin_ms / 1000.0:
                time.sleep(left - spin_ms / 1000.0)
        set_bias(bool(lvl))
        late.append((time.perf_counter() - target) * 1000.0)
    return late


def report(late: List[float]) -> None:
    jitter = max(late) - min(late)
    print(f"edges: {len(late)}  latency mean {statistics.mean(late):.1f} ms  "
          f"jitter (max-min) {jitter:.1f} ms", file=sys.stderr)
    if jitter > 6.0:
        print("WARNING: jitter > 6 ms - frame may be rejected; try the pyrtlsdr backend, "
              "a quieter host, or --timing 40,90,120,400", file=sys.stderr)


# --------------------------------------------------------------------------
# CLI

def parse_timing(s: Optional[str]) -> Timing:
    if not s:
        return Timing()
    vals = [float(x) for x in s.split(",")]
    if len(vals) != 4:
        raise SystemExit("--timing needs gap0,gap1,on,pre in ms")
    return Timing(*vals)


def cmd_bands(_: argparse.Namespace) -> int:
    for b in load_bands():
        lna = "LNA ok " if b["lna_ok"] else "no LNA "
        fmn = "FM-notch" if b["fm_notch"] else ""
        print(f"F{b['id']}  {b['name']:<8} {b['filter']:<24} {lna} {fmn:<8} {b['bands']}")
    return 0


def cmd_send(a: argparse.Namespace) -> int:
    bank = a.bank
    if a.band is not None:
        b = find_band(a.band)
        bank = b["id"]
        if a.lna and not b["lna_ok"]:
            print(f"note: F{bank} {b['name']} forbids the LNA; the box will light AUTO "
                  "and stay in bypass", file=sys.stderr)
    if bank is None:
        raise SystemExit("give --bank N or --band NAME")

    frame = Frame(bank=bank, lna=a.lna, quiet=a.quiet, clr=a.clr)
    desc = f"frame F{bank} lna={int(a.lna)} quiet={int(a.quiet)} clr={int(a.clr)}"
    return transmit(a, frame, desc)


def cmd_panel(a: argparse.Namespace) -> int:
    frame = ConfigFrame(panel=PANEL_MODES[a.mode])
    return transmit(a, frame, f"config panel={a.mode}")


def transmit(a: argparse.Namespace, frame, desc: str) -> int:
    timing = parse_timing(a.timing)
    edges: List[Tuple[float, int]] = []
    t = 0.0
    for _ in range(a.repeat):
        fe = frame.edges(timing, t0=t)
        edges += fe if not edges else fe[1:]
        t = edges[-1][0] + timing.on

    print(f"{desc} payload=0x{frame.payload:02X} bits={''.join(map(str, frame.bits()))} "
          f"({edges[-1][0] / 1000:.2f} s)", file=sys.stderr)

    if a.backend == "dry-run":
        print("# t_ms level   (coaxolotl dry-run; pipe into firmware/build/sim)")
        for t_ms, lvl in edges:
            print(f"{t_ms:.1f} {lvl}")
        return 0

    if a.backend == "pyrtlsdr":
        set_bias, close = backend_pyrtlsdr(a.device)
    else:
        set_bias, close = backend_rtl_biast(a.device, a.rtl_biast)
    try:
        late = play(set_bias, edges)
    finally:
        close()
    report(late)
    return 0


def link_args(s: argparse.ArgumentParser) -> None:
    s.add_argument("--backend", choices=["pyrtlsdr", "rtl_biast", "dry-run"], default="pyrtlsdr")
    s.add_argument("-d", "--device", type=int, default=0, help="RTL-SDR index")
    s.add_argument("--rtl-biast", default="rtl_biast", help="rtl_biast executable")
    s.add_argument("--repeat", type=int, default=1, help="send the frame N times")
    s.add_argument("--timing", help="gap0,gap1,on,pre in ms (default 30,70,100,400)")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Coaxolotl bias-T keyer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("bands", help="list the filter bank").set_defaults(fn=cmd_bands)

    s = sub.add_parser("send", help="send one command frame")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--bank", type=int, choices=range(8), help="branch 0..7")
    g.add_argument("--band", help="branch by name, e.g. 2m, THRU, 160")
    s.add_argument("--lna", action="store_true", help="request the LNA")
    s.add_argument("--quiet", action="store_true", help="no animations / RUN lamp (least EMI)")
    s.add_argument("--clr", action="store_true", help="clear sticky lamps, retry a tripped LNA")
    link_args(s)
    s.set_defaults(fn=cmd_send)

    pnl = sub.add_parser("panel", help="lamp mode, kept in the box's flash")
    pnl.add_argument("mode", choices=list(PANEL_MODES),
                     help="full = every lamp, bands = F0..F7 only, off = PWR lamp only")
    link_args(pnl)
    pnl.set_defaults(fn=cmd_panel)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
