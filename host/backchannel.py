"""Coaxolotl back-channel: the box answers every frame on the coax.

After a frame (good or bad), and once after boot, the box keys a square wave
from its MCU onto the coax: 24.5 MHz / 2 = 12.25 MHz, which the RTL-SDR hears
on the 3rd harmonic at 36.75 MHz. It is on-off keyed, Manchester coded, with
5 ms chips (one bit = 2 chips, 100 bit/s). The reply starts once the link has
been idle for 300 ms after the host's last gap and lasts about 0.73 s.

  chips:  preamble 8 x '1' | sync 0x2D | 7 bytes MSB first | one 'on' chip
          bit 1 = on,off   bit 0 = off,on

  byte 0  kind[7:6] err[5:3] seq[2:0]
  byte 1  bank[7:5] lna_req lna_path fmn auto quiet
  byte 2  trip low hold drop glt rst lna_en 0
  byte 3  VCOAX / 20 mV     byte 4  I_LNA / mA     byte 5  VSCAP / 20 mV
  byte 6  CRC-8 (poly 0x07, init 0xFF) over bytes 0..5

The packet layout must match firmware/src/beacon.h. The demodulator does not
need to know the carrier exactly: the MCU oscillator is only good to +/-2 %
(+/-0.7 MHz at 36.75 MHz), so it looks for the one bin in the capture that
switches on and off, then reads the chips from that bin's envelope.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

CARRIER_HZ = 24.5e6 / 2           # PCA CEX0, BCN_PCA_CPH = 1
HARMONIC = 3
BEACON_HZ = CARRIER_HZ * HARMONIC  # 36.75 MHz nominal
CHIP_MS = 5.0
REPLY_DELAY_MS = 300.0             # T_IDLE_MIN after the host's last gap

SYNC = 0x2D
PRE_BITS = 8
LEN = 7
NCHIPS = (PRE_BITS + 8 + 8 * LEN) * 2 + 1
REPLY_MS = NCHIPS * CHIP_MS

KINDS = {1: "HELLO", 2: "ACK", 3: "NAK"}
ERRS = {0: "-", 1: "SYNC", 2: "CHK", 3: "GAP", 4: "RUNT", 5: "TMO", 6: "DROP"}
MV_LSB = 20


def crc8(data: Sequence[int]) -> int:
    c = 0xFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def chips(pkt: Sequence[int]) -> List[int]:
    """On-air chip sequence for a 7-byte packet (mirror of bcn_chip())."""
    bits = [1] * PRE_BITS + [(SYNC >> (7 - i)) & 1 for i in range(8)]
    for byte in pkt:
        bits += [(byte >> (7 - i)) & 1 for i in range(8)]
    out: List[int] = []
    for b in bits:
        out += [1, 0] if b else [0, 1]
    return out + [1]


@dataclass(frozen=True)
class Reply:
    kind: str
    err: str
    seq: int
    bank: int
    lna_req: bool
    lna_path: bool
    fmn: bool
    auto: bool
    quiet: bool
    trip: bool
    low: bool
    hold: bool
    drop: bool
    glt: bool
    rst: bool
    lna_en: bool
    vcoax_v: float
    i_lna_ma: int
    vscap_v: float
    raw: bytes

    @classmethod
    def parse(cls, pkt: Sequence[int]) -> "Reply":
        p = bytes(pkt)
        if len(p) != LEN:
            raise ValueError(f"reply must be {LEN} bytes")
        if crc8(p[:6]) != p[6]:
            raise ValueError("reply CRC mismatch")
        kind = p[0] >> 6
        if kind not in KINDS:
            raise ValueError(f"unknown reply kind {kind}")
        s, f = p[1], p[2]
        return cls(
            kind=KINDS[kind], err=ERRS.get((p[0] >> 3) & 7, "?"), seq=p[0] & 7,
            bank=s >> 5, lna_req=bool(s & 0x10), lna_path=bool(s & 0x08),
            fmn=bool(s & 0x04), auto=bool(s & 0x02), quiet=bool(s & 0x01),
            trip=bool(f & 0x80), low=bool(f & 0x40), hold=bool(f & 0x20),
            drop=bool(f & 0x10), glt=bool(f & 0x08), rst=bool(f & 0x04),
            lna_en=bool(f & 0x02),
            vcoax_v=p[3] * MV_LSB / 1000.0, i_lna_ma=p[4], vscap_v=p[5] * MV_LSB / 1000.0,
            raw=p)

    @property
    def flags(self) -> List[str]:
        names = ["trip", "low", "hold", "drop", "glt", "rst"]
        return [n.upper() for n in names if getattr(self, n)]

    def summary(self) -> str:
        head = self.kind if self.kind != "NAK" else f"NAK({self.err})"
        path = "LNA" if self.lna_path else "BYP"
        if self.auto:
            path += "/AUTO"
        if self.fmn:
            path += "+FMN"
        return (f"{head} seq={self.seq} F{self.bank} {path}{' QUIET' if self.quiet else ''}  "
                f"Vcoax={self.vcoax_v:.2f} V  I_lna={self.i_lna_ma} mA  "
                f"Vscap={self.vscap_v:.2f} V  flags={','.join(self.flags) or '-'}")


def pack(kind: int, err: int = 0, seq: int = 0, state: int = 0, flags: int = 0,
         vcoax_mv: int = 0, i_ma: int = 0, vscap_mv: int = 0) -> bytes:
    """Build a packet like the firmware does (for tests and simulators)."""
    def q(mv: int) -> int:
        return min(255, (mv + MV_LSB // 2) // MV_LSB)
    p = [(kind << 6) | ((err & 7) << 3) | (seq & 7), state & 0xFF, flags & 0xFE,
         q(vcoax_mv), min(255, i_ma), q(vscap_mv)]
    return bytes(p + [crc8(p)])


# --------------------------------------------------------------------------
# demodulator

@dataclass(frozen=True)
class Heard:
    reply: Optional[Reply]
    offset_hz: float      # carrier relative to the tuned centre
    snr_db: float         # on/off ratio in the beacon bin
    chip_ms: float        # measured chip length (MCU low-frequency clock)
    t_ms: float           # start of the reply in the capture
    note: str = ""


def _fft_size(fs: float) -> int:
    n = 64
    while n * 2 <= fs * 0.0006:   # frames of <= 0.6 ms: >= 8 per chip
        n *= 2
    return n


def _runs(b):
    import numpy as np
    edges = np.flatnonzero(np.diff(b.astype(np.int8))) + 1
    starts = np.concatenate(([0], edges))
    lens = np.diff(np.concatenate((starts, [len(b)])))
    return [(int(b[s]), int(n), int(s)) for s, n in zip(starts, lens)]


def _manchester(seq: List[int]) -> Optional[bytes]:
    pre = [1, 0] * 4
    syn = chips([0] * LEN)[2 * PRE_BITS:2 * PRE_BITS + 16]
    pat = pre + syn
    n = len(pat)
    for i in range(len(seq) - n - 16 * LEN + 1):
        if seq[i:i + n] != pat:
            continue
        data = seq[i + n:i + n + 16 * LEN]
        out = []
        ok = True
        for k in range(LEN):
            v = 0
            for j in range(8):
                a, b = data[16 * k + 2 * j], data[16 * k + 2 * j + 1]
                if a == b:
                    ok = False
                    break
                v = (v << 1) | a
            if not ok:
                break
            out.append(v)
        if ok and crc8(out[:6]) == out[6]:
            return bytes(out)
    return None


def demodulate(iq, fs: float, chip_ms: float = CHIP_MS, min_snr_db: float = 15.0) -> Heard:
    """Find and decode one reply in a complex baseband capture."""
    import numpy as np

    iq = np.asarray(iq)
    n = _fft_size(fs)
    frames = len(iq) // n
    if frames < 64:
        return Heard(None, 0.0, 0.0, 0.0, 0.0, "capture too short")
    x = iq[:frames * n].reshape(frames, n) * np.hanning(n).astype(np.float32)
    p = np.abs(np.fft.fftshift(np.fft.fft(x, axis=1), axes=1)) ** 2
    p = p[:, :-2] + p[:, 1:-1] + p[:, 2:]            # bin k+1 = sum of k..k+2
    # the beacon is on for ~25 % of a 1.4 s capture (Manchester = 50 % duty);
    # a steady carrier or plain noise has a small high/low spread
    hi = np.percentile(p, 98, axis=0)
    lo = np.percentile(p, 20, axis=0) + 1e-20
    score = hi / lo
    c = p.shape[1] // 2                               # DC spike of the dongle
    score[max(0, c - 3):c + 3] = 0
    k = int(np.argmax(score))
    snr = 10 * np.log10(score[k])
    off = (k + 1 - n // 2) * fs / n
    if snr < min_snr_db:
        return Heard(None, off, snr, 0.0, 0.0, "no beacon")

    env = p[:, k]
    b = env > np.sqrt(hi[k] * lo[k])
    frame_ms = n / fs * 1000.0
    w = max(3, int(round(0.3 * chip_ms / frame_ms)) | 1)   # majority vote over ~0.3 chip
    b = np.convolve(b.astype(np.int32), np.ones(w, dtype=np.int32), "same") > w // 2
    runs = _runs(b)

    shorts = [r[1] * frame_ms for r in runs[1:-1]
              if 0.6 * chip_ms <= r[1] * frame_ms <= 1.4 * chip_ms]
    if len(shorts) < 8:
        return Heard(None, off, snr, 0.0, 0.0, "no chip clock")
    t_chip = float(np.median(shorts))

    best = None
    seq: List[int] = []
    seg_start = 0
    for lvl, ln, start in runs + [(0, 10 ** 9, frames)]:
        m = ln * frame_ms / t_chip
        nchip = int(round(m))
        if 1 <= nchip <= 2 and abs(m - nchip) < 0.4:
            if not seq:
                seg_start = start
            seq += [lvl] * nchip
            continue
        if lvl == 0 and seq:                          # silence closes a segment
            seq.append(0)
            pkt = _manchester(seq)
            if pkt is not None:
                best = (pkt, seg_start)
                break
        seq = []
    if best is None:
        return Heard(None, off, snr, t_chip, 0.0, "carrier heard, no valid packet")
    return Heard(Reply.parse(best[0]), off, snr, t_chip, best[1] * frame_ms)


def synth(pkt: bytes, fs: float, offset_hz: float = 0.0, chip_ms: float = CHIP_MS,
          lead_ms: float = 300.0, tail_ms: float = 200.0, snr_db: float = 20.0,
          seed: int = 1, interferer_hz: Optional[float] = None):
    """Complex baseband capture of one reply, for tests."""
    import numpy as np
    rng = np.random.default_rng(seed)
    ch = chips(pkt)
    n_lead, n_chip = int(fs * lead_ms / 1000), fs * chip_ms / 1000
    n_body = int(round(len(ch) * n_chip))
    n = n_lead + n_body + int(fs * tail_ms / 1000)
    gate = np.zeros(n, dtype=np.float32)
    idx = (np.arange(n_body) / n_chip).astype(int)
    gate[n_lead:n_lead + n_body] = np.asarray(ch, dtype=np.float32)[idx]
    t = np.arange(n) / fs
    sig = gate * np.exp(2j * np.pi * offset_hz * t)
    if interferer_hz is not None:
        sig = sig + 3.0 * np.exp(2j * np.pi * interferer_hz * t)   # steady carrier
    # snr_db is per-sample: carrier power vs total noise power in fs
    sigma = np.sqrt(10 ** (-snr_db / 10) / 2)
    noise = rng.normal(0, sigma, n) + 1j * rng.normal(0, sigma, n)
    return (sig + noise).astype(np.complex64)
