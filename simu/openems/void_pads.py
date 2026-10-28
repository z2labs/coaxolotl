"""What-if: clear the inner GND planes under the signal pads of one filter-bank lane, so the pads see B.Cu instead of
In1 0.2 mm below (each lane node carries ~0.85 pF of pad capacitance to In1, which detunes the UHF high-pass F7).

Adds keep-out rule areas (no copper fill) on In1.Cu and In2.Cu under every non-GND pad of the given parts, grown by
GROW, refills the zones and saves a copy. The GND pads of the shunt parts keep their plane and vias.

    kicad-python void_pads.py board.kicad_pcb out.kicad_pcb C701 C702 C703 L701 L702 [--grow 0.3]
"""
import sys
import pcbnew


def main(src, dst, refs, grow):
    b = pcbnew.LoadBoard(src)
    mm = pcbnew.FromMM
    n = 0
    for fp in b.GetFootprints():
        if fp.GetReference() not in refs:
            continue
        for p in fp.Pads():
            if p.GetNetname() == "GND":
                continue
            bb = p.GetBoundingBox()
            x0, y0, x1, y1 = bb.GetX() - mm(grow), bb.GetY() - mm(grow), bb.GetRight() + mm(grow), bb.GetBottom() + mm(grow)
            for layer in (pcbnew.In1_Cu, pcbnew.In2_Cu):
                z = pcbnew.ZONE(b)
                z.SetIsRuleArea(True)
                z.SetDoNotAllowZoneFills(True)
                z.SetDoNotAllowTracks(False); z.SetDoNotAllowVias(False); z.SetDoNotAllowPads(False)
                z.SetDoNotAllowFootprints(False)
                z.SetLayer(layer)
                z.SetZoneName(f"VOID_{fp.GetReference()}_{p.GetNumber()}")
                ol = z.Outline(); ol.NewOutline()
                for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
                    ol.Append(int(x), int(y))
                b.Add(z); n += 1
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(dst, b)
    print(f"{n} void areas on In1/In2 -> {dst}")


if __name__ == "__main__":
    a = sys.argv[1:]
    grow = 0.3
    if "--grow" in a:
        i = a.index("--grow"); grow = float(a[i + 1]); del a[i:i + 2]
    main(a[0], a[1], set(a[2:]), grow)
