#!/usr/bin/env python3
"""Coaxolotl host keyer: talk to the mast-head box through the RTL-SDR bias-T.

The bias-T is the whole link. A frame is 15 short power gaps:
  short gap (30 ms) = 0, long gap (70 ms) = 1, 100 ms of power between,
  at least 400 ms of steady power before the frame.
  bits = sync 1 0 1 | bank[2:0] lna quiet clr | inverse of those 6 bits

The box answers every frame on the same coax (back-channel, backchannel.py):
an ACK/NAK with the switch state, VCOAX, LNA current and supercap voltage,
keyed as a clock at 36.75 MHz that the dongle itself receives.

Examples
  coaxolotl.py bands
  coaxolotl.py send --band 2m --lna                    # pyrtlsdr, device 0
  coaxolotl.py send --band 2m --lna --listen           # ... and read the reply
  coaxolotl.py listen --seconds 3                      # e.g. HELLO after power-up
  coaxolotl.py decode capture.cu8 --fs 2.4e6           # rtl_sdr capture at 36.75 MHz
  coaxolotl.py send --bank 3 --backend rtl_biast -d 1
  coaxolotl.py send --bank 6 --lna --backend dry-run | firmware/build/sim

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

import backchannel as bc

ROOT = pathlib.Path(__file__).resolve().parent.parent
BANDPLAN = ROOT / "bandplan.json"

SYNC = (1, 0, 1)


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
        p = self.payload
        data = [(p >> i) & 1 for i in range(5, -1, -1)]
        return list(SYNC) + data + [b ^ 1 for b in data]

    def edges(self, timing: Timing = Timing(), t0: float = 0.0) -> List[Tuple[float, int]]:
        """(t_ms, level) list: power on, idle, then one gap per bit; ends powered."""
        timing.check()
        t = t0
        ev = [(t, 1)]
        t += timing.pre
        for b in self.bits():
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

def open_rtlsdr(index: int):
    try:
        from rtlsdr import RtlSdr  # type: ignore
    except ImportError:
        raise SystemExit("pyrtlsdr missing: pip install pyrtlsdr  (or use --backend rtl_biast)")
    sdr = RtlSdr(device_index=index)
    if not hasattr(sdr, "set_bias_tee"):
        sdr.close()
        raise SystemExit("this pyrtlsdr/librtlsdr has no set_bias_tee(); update or use rtl_biast")
    return sdr


def backend_pyrtlsdr(index: int) -> Tuple[Callable[[bool], None], Callable[[], None]]:
    sdr = open_rtlsdr(index)
    return (lambda on: sdr.set_bias_tee(bool(on))), sdr.close


def tune_for_reply(sdr, a: argparse.Namespace) -> None:
    sdr.sample_rate = a.fs
    sdr.center_freq = a.beacon_hz
    sdr.gain = a.gain


def hear(samples, a: argparse.Namespace) -> int:
    h = bc.demodulate(samples, a.fs)
    where = f"{(a.beacon_hz + h.offset_hz) / 1e6:.4f} MHz ({h.offset_hz / 1e3:+.1f} kHz)"
    if h.reply is None:
        print(f"no reply: {h.note}  (best bin {where}, on/off {h.snr_db:.1f} dB)", file=sys.stderr)
        return 1
    print(h.reply.summary())
    print(f"  heard at {where}, on/off {h.snr_db:.1f} dB, chip {h.chip_ms:.2f} ms, "
          f"t={h.t_ms:.0f} ms, raw {h.reply.raw.hex(' ')}", file=sys.stderr)
    return 0 if h.reply.kind != "NAK" else 2


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

    timing = parse_timing(a.timing)
    frame = Frame(bank=bank, lna=a.lna, quiet=a.quiet, clr=a.clr)
    edges: List[Tuple[float, int]] = []
    t = 0.0
    for _ in range(a.repeat):
        fe = frame.edges(timing, t0=t)
        edges += fe if not edges else fe[1:]
        t = edges[-1][0] + timing.on

    print(f"frame F{bank} lna={int(a.lna)} quiet={int(a.quiet)} clr={int(a.clr)} "
          f"payload=0x{frame.payload:02X} bits={''.join(map(str, frame.bits()))} "
          f"({edges[-1][0] / 1000:.2f} s)", file=sys.stderr)

    if a.backend == "dry-run":
        print("# t_ms level   (coaxolotl dry-run; pipe into firmware/build/sim)")
        for t_ms, lvl in edges:
            print(f"{t_ms:.1f} {lvl}")
        return 0

    if a.listen:
        if a.backend != "pyrtlsdr":
            raise SystemExit("--listen needs the pyrtlsdr backend (it reads samples)")
        sdr = open_rtlsdr(a.device)
        try:
            tune_for_reply(sdr, a)
            late = play(lambda on: sdr.set_bias_tee(bool(on)), edges)
            report(late)
            samples = sdr.read_samples(int(a.fs * (bc.REPLY_DELAY_MS + bc.REPLY_MS + 400) / 1000))
        finally:
            sdr.close()
        return hear(samples, a)

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


def cmd_listen(a: argparse.Namespace) -> int:
    sdr = open_rtlsdr(a.device)
    try:
        tune_for_reply(sdr, a)
        if a.bias:
            sdr.set_bias_tee(True)
        samples = sdr.read_samples(int(a.fs * a.seconds))
    finally:
        sdr.close()
    return hear(samples, a)


def cmd_decode(a: argparse.Namespace) -> int:
    import numpy as np
    raw = pathlib.Path(a.file).read_bytes()
    if a.file.endswith(".cf32"):
        samples = np.frombuffer(raw, dtype=np.complex64)
    else:                                   # rtl_sdr: interleaved unsigned 8-bit I/Q
        u = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 127.4
        samples = (u[0::2] + 1j * u[1::2]) / 128.0
    return hear(samples, a)


def add_rx_args(s: argparse.ArgumentParser) -> None:
    s.add_argument("--fs", type=float, default=2.4e6, help="sample rate for the reply (default 2.4e6)")
    s.add_argument("--beacon-hz", type=float, default=bc.BEACON_HZ,
                   help=f"tuning for the reply (default {bc.BEACON_HZ / 1e6:.2f} MHz)")
    s.add_argument("--gain", type=float, default=20.0, help="tuner gain in dB while listening")


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
    s.add_argument("--backend", choices=["pyrtlsdr", "rtl_biast", "dry-run"], default="pyrtlsdr")
    s.add_argument("-d", "--device", type=int, default=0, help="RTL-SDR index")
    s.add_argument("--rtl-biast", default="rtl_biast", help="rtl_biast executable")
    s.add_argument("--repeat", type=int, default=1, help="send the frame N times")
    s.add_argument("--timing", help="gap0,gap1,on,pre in ms (default 30,70,100,400)")
    s.add_argument("--listen", action="store_true", help="read the box's reply afterwards")
    add_rx_args(s)
    s.set_defaults(fn=cmd_send)

    s = sub.add_parser("listen", help="capture and decode a reply (e.g. HELLO after power-up)")
    s.add_argument("-d", "--device", type=int, default=0, help="RTL-SDR index")
    s.add_argument("--seconds", type=float, default=3.0)
    s.add_argument("--bias", action="store_true", help="switch the bias-T on first")
    add_rx_args(s)
    s.set_defaults(fn=cmd_listen)

    s = sub.add_parser("decode", help="decode a reply from a capture file (.cu8 or .cf32)")
    s.add_argument("file")
    add_rx_args(s)
    s.set_defaults(fn=cmd_decode)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
