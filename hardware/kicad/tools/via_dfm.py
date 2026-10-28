"""Slide routed vias that sit too close to an SMD pad (JLC DFM "via to pad": FAIL < 0.10 mm, WARN < 0.15 mm).

Bassberry's via_off_pad.py only moves vias that touch a pad. srouter also leaves vias whose ring ends 0.05-0.10 mm
off a pad edge, which JLC flags as Danger. This pass moves every via whose ring is closer than GAP to any SMD pad
straight away from the nearest pad, by the smallest step (up to MAXSHIFT) that keeps the netclass clearance for the
via and the tracks that end on it; 0.15 mm if there is room, else 0.10 mm. Vias placed on purpose inside an exposed
pad (thermal vias, centre inside a pad larger than 1 mm2) are left alone.

usage: kicad-python via_dfm.py board.kicad_pcb        (BASSBERRY_TOOLS: folder with fatten.py)
"""
import sys, os, math
sys.path.insert(0, os.environ.get("BASSBERRY_TOOLS", r"C:\Users\RF\Documents\GitHub\bassberry\hardware\tools"))
import pcbnew
import fatten

GAPS = (0.15, 0.10)   # mm via ring to pad edge: wanted, then the JLC Danger limit
MAXSHIFT = 0.8        # mm
EP_AREA = 1.0         # mm2: a via centred in a pad this large is a thermal via


def main(path):
    b = pcbnew.LoadBoard(path)
    mm, FM = pcbnew.ToMM, pcbnew.FromMM
    fatten.CLR = 0.15; fatten.CLR_PAD = 0.15
    cu = fatten.Copper(b)
    tracks = [t for t in b.GetTracks() if t.GetClass() != "PCB_VIA"]
    ALL = (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu)
    smd = []
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
                bb = p.GetBoundingBox()
                smd.append((fp.GetReference(), p.GetNumber(), (mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetRight()), mm(bb.GetBottom()))))
    moved, stuck, kept = 0, [], 0
    for v in [t for t in b.GetTracks() if t.GetClass() == "PCB_VIA"]:
        c = (mm(v.GetPosition().x), mm(v.GetPosition().y)); r = mm(v.GetWidth(pcbnew.F_Cu)) / 2
        if cu.off_pads(c, r, GAPS[0]):
            continue
        near, ep = [], False
        for ref, num, box in smd:
            q = (min(max(c[0], box[0]), box[2]), min(max(c[1], box[1]), box[3]))
            d = math.dist(c, q)
            if d == 0 and (box[2] - box[0]) * (box[3] - box[1]) >= EP_AREA:
                ep = True; break
            if d < r + GAPS[0]:
                near.append((d, q, ref, num))
        if ep:
            kept += 1; continue
        if not near:
            continue
        d, q, ref, num = min(near)
        if d == 0:             # centre on a small pad: via_off_pad's job
            stuck.append(f"{v.GetNetname()} at ({c[0]:.2f},{c[1]:.2f}) on {ref}.{num} (centre on pad)"); continue
        u = ((c[0] - q[0]) / d, (c[1] - q[1]) / d)
        ends = [t for t in tracks if t.GetNetname() == v.GetNetname() and (t.GetStart() == v.GetPosition() or t.GetEnd() == v.GetPosition())]
        done = False
        for gap in GAPS:
            s = r + gap + 0.01 - d
            while s <= r + gap + 0.01 - d + MAXSHIFT and not done:
                np_ = (c[0] + u[0] * s, c[1] + u[1] * s)
                ok = cu.off_pads(np_, r, gap) and all(cu.clear(v.GetNetname(), l, np_, np_, r, skip=(v,)) for l in ALL)
                for t in ends if ok else []:
                    other = t.GetEnd() if t.GetStart() == v.GetPosition() else t.GetStart()
                    o = (mm(other.x), mm(other.y))
                    if not cu.clear(t.GetNetname(), t.GetLayer(), o, np_, mm(t.GetWidth()) / 2, skip=(t,)):
                        ok = False; break
                if ok:
                    old = v.GetPosition(); new = pcbnew.VECTOR2I(FM(np_[0]), FM(np_[1]))
                    for t in ends:
                        if t.GetStart() == old:
                            t.SetStart(new)
                        else:
                            t.SetEnd(new)
                    v.SetPosition(new)
                    cu = fatten.Copper(b)
                    moved += 1; done = True
                s += 0.025
            if done:
                break
        if not done:
            stuck.append(f"{v.GetNetname()} at ({c[0]:.2f},{c[1]:.2f}) near {ref}.{num} {d - r:.2f} mm")
    pcbnew.SaveBoard(path, b)
    print(f"via_dfm: {moved} vias moved to >= 0.10 mm off SMD pads, {kept} exposed-pad vias kept, {len(stuck)} could not move")
    for s_ in stuck:
        print("  stuck:", s_)


if __name__ == "__main__":
    main(sys.argv[1])
