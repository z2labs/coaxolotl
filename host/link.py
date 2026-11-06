"""Coaxolotl link: one object that keys frames and reads the box's replies.

Two backends with the same API:

  RtlLink  one RTL-SDR kept open. The bias-T keys the frame, the tuner sits on
           the beacon (36.75 MHz) all the time, so nothing retunes between
           transactions. This is what the GUI and your own scripts use.
  SimLink  no hardware: the frames go through the real firmware logic
           (firmware/build/sim), the reply packet it builds is turned into a
           synthetic capture and decoded by the same demodulator. The box
           keeps its state between transactions (the whole edge history is
           replayed each time), so seq, trip and panel mode behave.

    from link import RtlLink
    from coaxolotl import Frame
    with RtlLink(index=0) as box:
        t = box.transact(Frame(bank=6, lna=True))
        print(t.heard.reply.summary() if t.heard.reply else t.heard.note)
"""
from __future__ import annotations

import pathlib
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple, Union

import backchannel as bc
from coaxolotl import ConfigFrame, Frame, Timing, play

ROOT = pathlib.Path(__file__).resolve().parent.parent
SIM = ROOT / "firmware" / "build" / "sim"

AnyFrame = Union[Frame, ConfigFrame]
# capture after the last edge: reply delay + reply + margin for LFOSC skew
LISTEN_S = (bc.REPLY_DELAY_MS + bc.REPLY_MS + 400) / 1000.0


@dataclass
class Transaction:
    frame: Optional[AnyFrame]
    late: List[float] = field(default_factory=list)     # per-edge lateness, ms
    heard: Optional[bc.Heard] = None                    # None = did not listen
    t: float = field(default_factory=time.time)

    @property
    def jitter_ms(self) -> float:
        return (max(self.late) - min(self.late)) if self.late else 0.0

    @property
    def reply(self) -> Optional[bc.Reply]:
        return self.heard.reply if self.heard else None


class LinkError(RuntimeError):
    pass


class RtlLink:
    """An RTL-SDR with the box on its bias-T."""

    def __init__(self, index: int = 0, fs: float = 2.4e6, beacon_hz: float = bc.BEACON_HZ,
                 gain: float = 20.0, timing: Timing = Timing()):
        self.index, self.fs, self.beacon_hz, self.gain, self.timing = index, fs, beacon_hz, gain, timing
        self.sdr = None
        self._lock = threading.Lock()

    def open(self) -> "RtlLink":
        try:
            from rtlsdr import RtlSdr  # type: ignore
        except ImportError as e:
            raise LinkError("pyrtlsdr missing: pip install pyrtlsdr (and librtlsdr / "
                            "rtlsdr.dll with bias-T support on the PATH)") from e
        try:
            sdr = RtlSdr(device_index=self.index)
        except Exception as e:  # pyrtlsdr raises LibUSBError, IOError, ...
            raise LinkError(f"cannot open RTL-SDR #{self.index}: {e}") from e
        if not hasattr(sdr, "set_bias_tee"):
            sdr.close()
            raise LinkError("this pyrtlsdr/librtlsdr has no set_bias_tee(); install the "
                            "rtl-sdr-blog build of librtlsdr")
        sdr.sample_rate = self.fs
        sdr.center_freq = self.beacon_hz
        sdr.gain = self.gain
        self.sdr = sdr
        return self

    def close(self) -> None:
        if self.sdr is not None:
            self.sdr.close()
            self.sdr = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def set_gain(self, gain: float) -> None:
        self.gain = gain
        if self.sdr is not None:
            self.sdr.gain = gain

    def set_beacon(self, hz: float) -> None:
        self.beacon_hz = hz
        if self.sdr is not None:
            self.sdr.center_freq = hz

    def bias(self, on: bool) -> None:
        with self._lock:
            self._need().set_bias_tee(bool(on))

    def transact(self, frame: AnyFrame, listen: bool = True,
                 abort: Optional[Callable[[], bool]] = None) -> Transaction:
        """Key one frame; with listen, read and decode the reply."""
        with self._lock:
            sdr = self._need()
            late = play(lambda on: sdr.set_bias_tee(bool(on)), frame.edges(self.timing))
            if abort and abort():
                return Transaction(frame, late)
            heard = self._capture(LISTEN_S) if listen else None
        return Transaction(frame, late, heard)

    def listen(self, seconds: float = 3.0) -> Transaction:
        """Record without sending, e.g. for the HELLO after the first power-up."""
        with self._lock:
            self._need()
            return Transaction(None, [], self._capture(seconds))

    def _capture(self, seconds: float) -> bc.Heard:
        n = int(self.fs * seconds) // 16384 * 16384 + 16384
        return bc.demodulate(self.sdr.read_samples(n), self.fs)

    def _need(self):
        if self.sdr is None:
            raise LinkError("not connected")
        return self.sdr


class SimLink:
    """The firmware logic in a subprocess instead of a box (no hardware)."""

    def __init__(self, fs: float = 2.4e6, timing: Timing = Timing(), i_ma: int = 101,
                 vc_mv: int = 4280, scap_mv: int = 3920, sim: pathlib.Path = SIM):
        self.fs, self.timing = fs, timing
        self.i_ma, self.vc_mv, self.scap_mv = i_ma, vc_mv, scap_mv
        self.sim = sim
        self.edges: List[Tuple[float, int]] = []
        self.t_ms = 0.0
        self.bias_on = False
        self.offset_hz = 0.0
        self._lock = threading.Lock()

    def open(self) -> "SimLink":
        if not self.sim.exists():
            raise LinkError(f"{self.sim} not built: make -C firmware sim")
        return self

    def close(self) -> None:
        pass

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def set_gain(self, gain: float) -> None:
        pass

    def set_beacon(self, hz: float) -> None:
        pass

    def bias(self, on: bool) -> None:
        with self._lock:
            self._edge(bool(on))

    def transact(self, frame: AnyFrame, listen: bool = True,
                 abort: Optional[Callable[[], bool]] = None) -> Transaction:
        with self._lock:
            if not self.bias_on:                   # power the box, let it boot
                self._edge(True)
                self.t_ms += 2000.0
            self.t_ms += 500.0                     # idle since the last activity
            start = self.t_ms
            for t, lvl in frame.edges(self.timing)[1:]:
                self.edges.append((start + t, lvl))
            self.t_ms = self.edges[-1][0]
            heard = self._run(after=start) if listen else None
            self.t_ms += LISTEN_S * 1000.0
            return Transaction(frame, [0.0, 0.0], heard)

    def listen(self, seconds: float = 3.0) -> Transaction:
        with self._lock:
            start = self.t_ms
            self.t_ms += seconds * 1000.0
            return Transaction(None, [], self._run(after=start - 1.0))

    def _edge(self, on: bool) -> None:
        if on != self.bias_on or not self.edges:
            self.edges.append((self.t_ms, int(on)))
            self.bias_on = on
        self.t_ms += 1.0

    def _run(self, after: float) -> bc.Heard:
        if not self.edges or not self.edges[0][1]:
            return bc.Heard(None, 0.0, 0.0, 0.0, 0.0, "box not powered")
        text = "\n".join(f"{t:.1f} {lvl}" for t, lvl in self.edges)
        tail = max(500.0, self.t_ms - self.edges[-1][0] + 1500.0)
        out = subprocess.run([str(self.sim), "--chips", "--tail", f"{tail:.0f}",
                              "--ilna", str(self.i_ma), "--vc", str(self.vc_mv),
                              "--scap", str(self.scap_mv)],
                             input=text, capture_output=True, text=True, check=True).stdout
        pkt = None
        for line in out.splitlines():
            if "REPLY" not in line:
                continue
            t = float(line.split("]")[0].strip("[ ms"))
            if t + self.edges[0][0] >= after:
                pkt = bytes(int(h, 16) for h in line.split("REPLY", 1)[1].split("chips=")[0].split())
        if pkt is None:
            return bc.Heard(None, 0.0, 0.0, 0.0, 0.0, "no beacon")
        # through the real demodulator, with the carrier off-centre like a real MCU
        self.offset_hz = -0.004 * bc.BEACON_HZ
        return bc.demodulate(bc.synth(pkt, self.fs, self.offset_hz, snr_db=0, tail_ms=300), self.fs)
