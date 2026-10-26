"""Host-side tests. Run: python3 -m unittest discover -s host"""
import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import coaxolotl as cx  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SIM = ROOT / "firmware" / "build" / "sim"


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

    def test_bandplan(self):
        self.assertEqual(cx.find_band("2m")["id"], 6)
        self.assertEqual(cx.find_band("thru")["id"], 0)
        self.assertEqual(len(cx.load_bands()), 8)


@unittest.skipUnless(SIM.exists(), "build the simulator first: make -C firmware sim")
class EndToEndTest(unittest.TestCase):
    """host encoder -> real firmware decoder/app, through the simulator"""

    def run_sim(self, send_args, sim_args=()):
        dry = subprocess.run([sys.executable, str(ROOT / "host" / "coaxolotl.py"), "send",
                              *send_args, "--backend", "dry-run"],
                             capture_output=True, text=True, check=True).stdout
        return subprocess.run([str(SIM), *sim_args], input=dry,
                              capture_output=True, text=True, check=True).stdout

    def test_every_branch(self):
        for bank in range(8):
            out = self.run_sim(["--bank", str(bank), "--lna"])
            self.assertIn(f"FRAME_OK    payload=0x{(bank << 3) | 4:02X} bank={bank} ", out)
            self.assertIn(f"bank F{bank},", out)

    def test_distorted_link(self):
        out = self.run_sim(["--band", "2m", "--lna"], ["--fall-delay", "9", "--jitter", "3"])
        self.assertIn("FRAME_OK", out)
        self.assertIn("bank F6, LNA + FM notch", out)


if __name__ == "__main__":
    unittest.main()
