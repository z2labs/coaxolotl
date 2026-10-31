"""Host-side tests. Run: python3 -m unittest discover -s host"""
import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import backchannel as bc  # noqa: E402
import coaxolotl as cx  # noqa: E402

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
SIM = ROOT / "firmware" / "build" / "sim"
CPP = ROOT / "sdrpp" / "build" / "test_coaxlink"


class FrameTest(unittest.TestCase):
    def test_payload_layout(self):
        f = cx.Frame(bank=6, lna=True)
        self.assertEqual(f.payload, 0b110100)
        self.assertEqual(f.bits(), [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0, 1, 0, 1, 1])

    def test_check_is_inverse(self):
        for p in range(64):
            f = cx.Frame(bank=p >> 3, lna=bool(p & 4), quiet=bool(p & 2), clr=bool(p & 1))
            b = f.bits()
            self.assertEqual(len(b), 15)
            self.assertEqual(b[:3], [1, 0, 1])
            self.assertEqual([x ^ 1 for x in b[3:9]], b[9:])

    def test_edges_timing(self):
        e = cx.Frame(bank=0).edges()
        self.assertEqual(e[0], (0.0, 1))
        self.assertEqual(e[1], (400.0, 0))
        self.assertEqual(e[2], (470.0, 1))                 # sync bit 1 = long gap
        self.assertEqual(len(e), 1 + 2 * 15)
        self.assertEqual(e[-1][1], 1)                       # ends powered

    def test_timing_guard(self):
        with self.assertRaises(ValueError):
            cx.Frame(bank=0).edges(cx.Timing(gap0=30, gap1=40))

    def test_config_frame(self):
        f = cx.ConfigFrame(panel=2)
        self.assertEqual(f.payload, 0b100000)
        self.assertEqual(f.bits()[:3], [0, 1, 0])
        self.assertEqual([x ^ 1 for x in f.bits()[3:9]], f.bits()[9:])
        with self.assertRaises(ValueError):
            cx.ConfigFrame(panel=3).bits()

    def test_bandplan(self):
        self.assertEqual(cx.find_band("2m")["id"], 6)
        self.assertEqual(cx.find_band("thru")["id"], 0)
        self.assertEqual(len(cx.load_bands()), 8)


class ReplyTest(unittest.TestCase):
    def test_crc_check_value(self):
        self.assertEqual(bc.crc8(b"123456789"), 0xFB)       # same as firmware test

    def test_pack_parse(self):
        p = bc.pack(2, 0, 5, 0xDC, 0x22, 4212, 97, 3611)
        r = bc.Reply.parse(p)
        self.assertEqual((r.kind, r.seq, r.bank, r.lna_path, r.fmn, r.hold, r.lna_en),
                         ("ACK", 5, 6, True, True, True, True))
        self.assertAlmostEqual(r.vcoax_v, 4.22)
        self.assertEqual(r.i_lna_ma, 97)
        bad = bytearray(p)
        bad[4] ^= 1
        with self.assertRaises(ValueError):
            bc.Reply.parse(bad)

    def test_nak(self):
        r = bc.Reply.parse(bc.pack(3, 1, 0))
        self.assertEqual((r.kind, r.err), ("NAK", "SYNC"))
        self.assertTrue(r.summary().startswith("NAK(SYNC)"))

    def test_chips(self):
        c = bc.chips(bc.pack(1))
        self.assertEqual(len(c), bc.NCHIPS)
        self.assertEqual(len(c), 145)
        self.assertEqual(c[:4], [1, 0, 1, 0])
        self.assertEqual(c[-1], 1)
        self.assertTrue(all(c[i] != c[i + 1] for i in range(0, 144, 2)))


@unittest.skipUnless(np is not None, "numpy needed")
class DemodTest(unittest.TestCase):
    PKT = bc.pack(2, 0, 1, 0xDC, 0x22, 4300, 100, 3900)

    def test_offset_and_skew(self):
        # MCU HFOSC +/-2 % moves the carrier, LFOSC +/-10 % stretches the chips
        for fs, off, chip in [(2.4e6, -700e3, 5.5), (2.4e6, 650e3, 4.5), (1.024e6, 300e3, 5.2)]:
            iq = bc.synth(self.PKT, fs, off, chip, snr_db=-5, interferer_hz=-off / 2)
            h = bc.demodulate(iq, fs)
            self.assertIsNotNone(h.reply, f"fs={fs} off={off}: {h.note}")
            self.assertEqual(h.reply.raw, self.PKT)
            self.assertLess(abs(h.offset_hz - off), 2 * fs / 1024)
            self.assertAlmostEqual(h.chip_ms, chip, delta=0.3)
            self.assertAlmostEqual(h.t_ms, 300, delta=2)

    def test_silence(self):
        iq = bc.synth(self.PKT, 2.4e6, snr_db=-80)
        self.assertIsNone(bc.demodulate(iq, 2.4e6).reply)

    def test_decode_cli_cu8(self):
        import tempfile
        iq = bc.synth(self.PKT, 2.4e6, 123e3, snr_db=0) * 0.3
        u8 = np.empty(2 * len(iq), dtype=np.uint8)
        u8[0::2] = np.clip(iq.real * 128 + 127.4, 0, 255).astype(np.uint8)
        u8[1::2] = np.clip(iq.imag * 128 + 127.4, 0, 255).astype(np.uint8)
        with tempfile.NamedTemporaryFile(suffix=".cu8", delete=False) as f:
            f.write(u8.tobytes())
        out = subprocess.run([sys.executable, str(ROOT / "host" / "coaxolotl.py"), "decode", f.name],
                             capture_output=True, text=True)
        pathlib.Path(f.name).unlink()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("ACK seq=1 F6 LNA+FMN", out.stdout)


@unittest.skipUnless(np is not None and CPP.exists(), "build the C++ tests first: make -C sdrpp test")
class CppDemodTest(unittest.TestCase):
    """Python-synthesised capture -> the SDR++ module's C++ demodulator"""

    def test_cross(self):
        import tempfile
        pkt = bc.pack(3, 4, 6, 0x6B, 0x9C, 3880, 0, 2950)
        iq = bc.synth(pkt, 2.048e6, -612e3, 4.7, snr_db=-6, interferer_hz=250e3)
        with tempfile.NamedTemporaryFile(suffix=".cf32", delete=False) as f:
            f.write(iq.tobytes())
        out = subprocess.run([str(CPP), f.name, "2.048e6"], capture_output=True, text=True)
        pathlib.Path(f.name).unlink()
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(out.stdout.splitlines()[0], bc.Reply.parse(pkt).summary())


@unittest.skipUnless(SIM.exists(), "build the simulator first: make -C firmware sim")
class EndToEndTest(unittest.TestCase):
    """host encoder -> real firmware decoder/app, through the simulator"""

    def run_sim(self, send_args, sim_args=(), cmd="send"):
        dry = subprocess.run([sys.executable, str(ROOT / "host" / "coaxolotl.py"), cmd,
                              *send_args, "--backend", "dry-run"],
                             capture_output=True, text=True, check=True).stdout
        return subprocess.run([str(SIM), *sim_args], input=dry,
                              capture_output=True, text=True, check=True).stdout

    def test_every_branch(self):
        for bank in range(8):
            out = self.run_sim(["--bank", str(bank), "--lna"])
            self.assertIn(f"FRAME_OK    payload=0x{(bank << 3) | 4:02X} bank={bank} ", out)
            self.assertIn(f"bank F{bank},", out)

    def test_reply_from_firmware(self):
        """firmware packet + chips (sim) == host mirror, and the demod reads it back"""
        out = self.run_sim(["--band", "70", "--lna"], ["--chips", "--ilna", "88", "--vc", "4150"])
        line = [ln for ln in out.splitlines() if "REPLY" in ln][-1]
        hexes, chipstr = line.split("REPLY", 1)[1].split("chips=")
        pkt = bytes(int(h, 16) for h in hexes.split())
        self.assertEqual([int(c) for c in chipstr.strip()], bc.chips(pkt))
        r = bc.Reply.parse(pkt)
        self.assertEqual((r.kind, r.bank, r.lna_path, r.i_lna_ma), ("ACK", 7, True, 88))
        self.assertAlmostEqual(r.vcoax_v, 4.16)
        if np is not None:
            h = bc.demodulate(bc.synth(pkt, 2.4e6, -321e3, 5.3, snr_db=0), 2.4e6)
            self.assertEqual(h.reply, r)

    def test_distorted_link(self):
        out = self.run_sim(["--band", "2m", "--lna"], ["--fall-delay", "9", "--jitter", "3"])
        self.assertIn("FRAME_OK", out)
        self.assertIn("bank F6, LNA + FM notch", out)


    def test_panel_modes(self):
        for mode in ("bands", "off", "full"):
            out = self.run_sim([mode], cmd="panel")
            self.assertIn(f"CONFIG payload=0x{cx.PANEL_MODES[mode] << 4:02X} panel={mode}", out)
            self.assertIn(f"panel {mode}", out)
        out = self.run_sim(["off"], ["--fall-delay", "9", "--jitter", "3"], cmd="panel")
        self.assertIn("( 0 lamps lit)", out)


if __name__ == "__main__":
    unittest.main()
