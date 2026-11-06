"""Link layer through the firmware simulator. Run: python3 -m unittest discover -s host"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

try:
    import numpy  # noqa: F401
    import link as lk
    from coaxolotl import ConfigFrame, Frame
    READY = lk.SIM.exists()
except ImportError:  # pragma: no cover
    READY = False

try:
    import tkinter  # noqa: F401
    HAVE_TK = True
except ImportError:
    HAVE_TK = False


@unittest.skipUnless(READY, "needs numpy and the simulator: make -C firmware sim")
class SimLinkTest(unittest.TestCase):
    def test_session(self):
        with lk.SimLink() as box:
            box.bias(True)
            t = box.listen(3.0)
            self.assertEqual(t.reply.kind, "HELLO")

            t = box.transact(Frame(bank=6, lna=True))
            r = t.reply
            self.assertEqual((r.kind, r.seq, r.bank, r.lna_path, r.fmn), ("ACK", 1, 6, True, True))
            self.assertAlmostEqual(t.heard.offset_hz, box.offset_hz, delta=3e3)

            r = box.transact(Frame(bank=3, lna=True)).reply      # HF: LNA forbidden
            self.assertEqual((r.seq, r.bank, r.lna_path, r.auto), (2, 3, False, True))

            r = box.transact(ConfigFrame(panel=2)).reply        # config: switches stay
            self.assertEqual((r.kind, r.seq, r.bank), ("ACK", 3, 3))

            t = box.transact(Frame(bank=7), listen=False)
            self.assertIsNone(t.heard)
            r = box.transact(Frame(bank=7, quiet=True)).reply   # state survived
            self.assertEqual((r.seq, r.bank, r.quiet), (5, 7, True))

    def test_trip_and_clear(self):
        with lk.SimLink(i_ma=170) as box:
            r = box.transact(Frame(bank=5, lna=True)).reply
            self.assertTrue(r.trip)
            self.assertFalse(r.lna_en or r.lna_path)
            self.assertEqual(r.i_lna_ma, 170)
            box.i_ma = 95
            r = box.transact(Frame(bank=5, lna=True, clr=True)).reply
            self.assertFalse(r.trip)
            self.assertTrue(r.lna_path)

    def test_unpowered(self):
        with lk.SimLink() as box:
            self.assertIsNone(box.listen(1.0).reply)


@unittest.skipUnless(READY and HAVE_TK, "needs tkinter")
class GuiHelpersTest(unittest.TestCase):
    def test_csv_row(self):
        import coaxolotl_gui as g
        with lk.SimLink() as box:
            t = box.transact(Frame(bank=6, lna=True))
        row = g.csv_row(t)
        self.assertEqual(len(row), len(g.CSV_HEAD))
        self.assertEqual(row[1:6], ["F6 LNA", "ACK", "-", 1, 6])
        self.assertEqual(g.describe(ConfigFrame(panel=1)), "panel bands")


if __name__ == "__main__":
    unittest.main()
