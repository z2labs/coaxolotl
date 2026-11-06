#!/usr/bin/env python3
"""Coaxolotl control panel: drive the box from the PC and see what it says.

    python host/coaxolotl_gui.py              # RTL-SDR #0
    python host/coaxolotl_gui.py --sim        # no hardware: firmware simulator

Needs Python 3.8+ with tkinter (part of the Windows/macOS installers),
numpy, and pyrtlsdr with a bias-T capable librtlsdr (docs/host.md).
The dongle stays tuned to the beacon (36.75 MHz) while this runs, so use a
second dongle, or close this, for listening.
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional

import backchannel as bc
from coaxolotl import PANEL_MODES, ConfigFrame, Frame, load_bands
from link import LinkError, RtlLink, SimLink, Transaction

GREEN, RED, AMBER, GREY = "#2e9e3e", "#d03030", "#d08a00", "#9a9a9a"
FLAGS = ("trip", "low", "hold", "drop", "glt", "rst")


class App:
    def __init__(self, root: tk.Tk, args: argparse.Namespace):
        self.root, self.args = root, args
        self.link = None
        self.jobs: "queue.Queue" = queue.Queue()
        self.results: "queue.Queue" = queue.Queue()
        self.busy = False
        self.next_poll = 0.0
        self.bands = load_bands()
        self.csv_path = pathlib.Path(args.log) if args.log else None

        root.title("Coaxolotl")
        root.minsize(560, 520)
        self._build()
        threading.Thread(target=self._worker, daemon=True).start()
        root.after(100, self._tick)
        root.protocol("WM_DELETE_WINDOW", self._quit)

    # ---- layout ---------------------------------------------------------
    def _build(self) -> None:
        pad = dict(padx=6, pady=3)
        top = ttk.LabelFrame(self.root, text="Link")
        top.pack(fill="x", **pad)
        self.v_backend = tk.StringVar(value="simulator" if self.args.sim else "rtl-sdr")
        self.v_dev = tk.IntVar(value=self.args.device)
        self.v_gain = tk.DoubleVar(value=self.args.gain)
        self.v_beacon = tk.DoubleVar(value=self.args.beacon_hz / 1e6)
        ttk.Combobox(top, textvariable=self.v_backend, values=("rtl-sdr", "simulator"),
                     width=10, state="readonly").grid(row=0, column=0, **pad)
        ttk.Label(top, text="device").grid(row=0, column=1)
        ttk.Spinbox(top, from_=0, to=9, width=3, textvariable=self.v_dev).grid(row=0, column=2, **pad)
        ttk.Label(top, text="gain dB").grid(row=0, column=3)
        ttk.Spinbox(top, from_=0, to=50, increment=1, width=5, textvariable=self.v_gain,
                    command=self._apply_rx).grid(row=0, column=4, **pad)
        ttk.Label(top, text="beacon MHz").grid(row=0, column=5)
        e = ttk.Entry(top, width=8, textvariable=self.v_beacon)
        e.grid(row=0, column=6, **pad)
        e.bind("<Return>", lambda _e: self._apply_rx())
        self.b_conn = ttk.Button(top, text="Connect", command=self._connect)
        self.b_conn.grid(row=0, column=7, **pad)
        self.v_bias = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Bias-T (box power)", variable=self.v_bias,
                        command=lambda: self._job("bias", self.v_bias.get())).grid(
            row=1, column=0, columnspan=3, sticky="w", **pad)
        self.l_link = ttk.Label(top, text="not connected", foreground=GREY)
        self.l_link.grid(row=1, column=3, columnspan=5, sticky="w", **pad)

        cmd = ttk.LabelFrame(self.root, text="Command")
        cmd.pack(fill="x", **pad)
        names = [f"F{b['id']}  {b['name']}  ({b['filter']})" for b in self.bands]
        self.v_bank = tk.StringVar(value=names[0])
        ttk.Combobox(cmd, textvariable=self.v_bank, values=names, width=36,
                     state="readonly").grid(row=0, column=0, columnspan=3, sticky="w", **pad)
        self.v_lna, self.v_quiet = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        self.v_listen = tk.BooleanVar(value=True)
        ttk.Checkbutton(cmd, text="LNA", variable=self.v_lna).grid(row=0, column=3, **pad)
        ttk.Checkbutton(cmd, text="Quiet", variable=self.v_quiet).grid(row=0, column=4, **pad)
        ttk.Checkbutton(cmd, text="Read reply", variable=self.v_listen).grid(row=0, column=5, **pad)
        self.cmd_buttons = []
        for i, (txt, fn) in enumerate((("Send", self._send), ("Poll", self._poll),
                                       ("Clear", self._clear), ("Listen", self._listen))):
            b = ttk.Button(cmd, text=txt, command=fn)
            b.grid(row=1, column=i, sticky="ew", **pad)
            self.cmd_buttons.append(b)
        ttk.Label(cmd, text="Panel").grid(row=2, column=0, sticky="e")
        for i, m in enumerate(PANEL_MODES):
            b = ttk.Button(cmd, text=m, command=lambda m=m: self._panel(m))
            b.grid(row=2, column=1 + i, sticky="ew", **pad)
            self.cmd_buttons.append(b)
        self.v_auto = tk.BooleanVar(value=False)
        self.v_period = tk.IntVar(value=30)
        ttk.Checkbutton(cmd, text="Poll every", variable=self.v_auto).grid(row=3, column=0, sticky="w", **pad)
        ttk.Spinbox(cmd, from_=10, to=3600, width=5, textvariable=self.v_period).grid(row=3, column=1, sticky="w")
        ttk.Label(cmd, text="s").grid(row=3, column=2, sticky="w")

        tel = ttk.LabelFrame(self.root, text="Box")
        tel.pack(fill="x", **pad)
        self.l_head = tk.Label(tel, text="no reply yet", font=("TkDefaultFont", 14, "bold"), fg=GREY)
        self.l_head.grid(row=0, column=0, columnspan=6, sticky="w", **pad)
        self.l_state = ttk.Label(tel, text="")
        self.l_state.grid(row=1, column=0, columnspan=6, sticky="w", **pad)
        self.l_meas = {}
        for i, k in enumerate(("Vcoax", "I_lna", "Vscap")):
            self.l_meas[k] = tk.Label(tel, text=f"{k} —", font=("TkFixedFont", 11), fg=GREY)
            self.l_meas[k].grid(row=2, column=2 * i, columnspan=2, sticky="w", **pad)
        self.l_flags = {}
        for i, f in enumerate(FLAGS):
            self.l_flags[f] = tk.Label(tel, text=f.upper(), width=6, relief="groove", fg=GREY)
            self.l_flags[f].grid(row=3, column=i, **pad)
        self.l_rx = ttk.Label(tel, text="", foreground=GREY)
        self.l_rx.grid(row=4, column=0, columnspan=6, sticky="w", **pad)

        logf = ttk.LabelFrame(self.root, text="Log")
        logf.pack(fill="both", expand=True, **pad)
        self.t_log = tk.Text(logf, height=10, font=("TkFixedFont", 9))
        self.t_log.pack(fill="both", expand=True)
        self._set_enabled(False)

    # ---- actions (GUI thread) --------------------------------------------
    def _bank(self) -> int:
        return int(self.v_bank.get().split()[0][1:])

    def _frame(self, clr: bool = False) -> Frame:
        return Frame(bank=self._bank(), lna=self.v_lna.get(), quiet=self.v_quiet.get(), clr=clr)

    def _send(self):
        self._job("frame", self._frame(), self.v_listen.get())

    def _poll(self):
        self._job("frame", self._frame(), True)

    def _clear(self):
        self._job("frame", self._frame(clr=True), True)

    def _listen(self):
        self._job("listen", 3.0)

    def _panel(self, mode: str):
        self._job("frame", ConfigFrame(panel=PANEL_MODES[mode]), self.v_listen.get())

    def _connect(self):
        if self.link is not None:
            self._job("disconnect")
            return
        self._job("connect", self.v_backend.get(), self.v_dev.get(), self.v_gain.get(),
                  self.v_beacon.get() * 1e6)

    def _apply_rx(self):
        try:
            self._job("rx", self.v_gain.get(), self.v_beacon.get() * 1e6)
        except tk.TclError:
            pass

    def _job(self, *job):
        if job[0] in ("frame", "listen") and (self.busy or self.link is None):
            return
        self.busy = job[0] in ("frame", "listen")
        if self.busy:
            self._set_enabled(False)
            self.l_link.config(text="keying ..." if job[0] == "frame" else "listening ...",
                               foreground=AMBER)
        self.jobs.put(job)

    def _set_enabled(self, on: bool):
        for b in self.cmd_buttons:
            b.state(["!disabled"] if on else ["disabled"])

    # ---- worker thread: everything that touches the dongle --------------
    def _worker(self):
        while True:
            job = self.jobs.get()
            kind, a = job[0], job[1:]
            try:
                if kind == "connect":
                    backend, dev, gain, beacon = a
                    link = SimLink() if backend == "simulator" else RtlLink(dev, gain=gain, beacon_hz=beacon)
                    self.link = link.open()
                    self.results.put(("connected", backend))
                elif kind == "disconnect":
                    if self.link:
                        self.link.close()
                    self.link = None
                    self.results.put(("disconnected", None))
                elif kind == "rx" and self.link:
                    self.link.set_gain(a[0])
                    self.link.set_beacon(a[1])
                elif kind == "bias" and self.link:
                    self.link.bias(a[0])
                    self.results.put(("bias", a[0]))
                elif kind == "frame" and self.link:
                    self.results.put(("done", self.link.transact(a[0], listen=a[1])))
                elif kind == "listen" and self.link:
                    self.results.put(("done", self.link.listen(a[0])))
                elif kind == "quit":
                    if self.link:
                        self.link.close()
                    return
            except (LinkError, OSError, ValueError) as e:
                self.results.put(("error", str(e)))
            except Exception as e:  # keep the worker alive whatever the driver throws
                self.results.put(("error", f"{type(e).__name__}: {e}"))

    # ---- results (GUI thread) -------------------------------------------
    def _tick(self):
        while True:
            try:
                kind, val = self.results.get_nowait()
            except queue.Empty:
                break
            if kind == "connected":
                self.b_conn.config(text="Disconnect")
                self.l_link.config(text=f"connected ({val})", foreground=GREEN)
                self._set_enabled(True)
                self._log(f"connected: {val}")
            elif kind == "disconnected":
                self.b_conn.config(text="Connect")
                self.l_link.config(text="not connected", foreground=GREY)
                self._set_enabled(False)
                self.busy = False
                self._log("disconnected")
            elif kind == "bias":
                self._log(f"bias-T {'on' if val else 'off'}")
            elif kind == "error":
                self.busy = False
                self._set_enabled(self.link is not None)
                self.l_link.config(text=val, foreground=RED)
                self._log("ERROR " + val)
            elif kind == "done":
                self.busy = False
                self._set_enabled(True)
                self._show(val)
        if (self.v_auto.get() and self.link is not None and not self.busy
                and time.time() >= self.next_poll):
            self.next_poll = time.time() + max(10, self.v_period.get())
            self._poll()
        self.root.after(100, self._tick)

    def _show(self, t: Transaction):
        what = describe(t.frame)
        jit = f", jitter {t.jitter_ms:.1f} ms" if t.late else ""
        if t.frame is not None:
            self.v_bias.set(True)               # a frame leaves the box powered
        if t.heard is None:
            self.l_link.config(text=f"sent {what}{jit}", foreground=GREEN)
            self._log(f"{what}: sent{jit}")
            return
        h = t.heard
        r = h.reply
        if r is None:
            self.l_head.config(text=f"no reply ({h.note})", fg=AMBER)
            self.l_link.config(text=f"{what}: no reply{jit}", foreground=AMBER)
            self._log(f"{what}: no reply: {h.note} (best bin {h.offset_hz / 1e3:+.0f} kHz, "
                      f"{h.snr_db:.0f} dB){jit}")
            self._csv(t)
            return
        head = r.kind if r.kind != "NAK" else f"NAK  {r.err}"
        self.l_head.config(text=f"{head}   seq {r.seq}", fg=RED if r.kind == "NAK" else GREEN)
        band = self.bands[r.bank]
        path = "LNA" if r.lna_path else "bypass"
        if r.auto:
            path += " (AUTO: branch forbids the LNA)"
        if r.fmn:
            path += " + FM notch"
        mismatch = isinstance(t.frame, Frame) and r.kind == "ACK" and r.bank != t.frame.bank
        self.l_state.config(text=f"F{r.bank} {band['name']} ({band['filter']}), {path}"
                                 f"{', quiet' if r.quiet else ''}",
                            foreground=RED if mismatch else "")
        i_ok = (not r.lna_en) or 60 <= r.i_lna_ma <= 130
        self.l_meas["Vcoax"].config(text=f"Vcoax {r.vcoax_v:.2f} V", fg=RED if r.low else GREEN)
        self.l_meas["I_lna"].config(text=f"I_lna {r.i_lna_ma:3d} mA",
                                    fg=RED if r.trip else (GREEN if i_ok else AMBER))
        self.l_meas["Vscap"].config(text=f"Vscap {r.vscap_v:.2f} V", fg=GREEN if r.hold else AMBER)
        for f in FLAGS:
            on = getattr(r, f)
            good = f == "hold"
            self.l_flags[f].config(fg="white" if on else GREY,
                                   bg=(GREEN if good else RED) if on else self.root.cget("bg"))
        osc = (self.v_beacon.get() * 1e6 + h.offset_hz) / bc.BEACON_HZ - 1
        self.l_rx.config(text=f"beacon {(self.v_beacon.get() * 1e6 + h.offset_hz) / 1e6:.4f} MHz "
                              f"(osc {osc * 100:+.2f} %), on/off {h.snr_db:.0f} dB, "
                              f"chip {h.chip_ms:.2f} ms{jit}")
        self.l_link.config(text=f"{what}: {r.kind}", foreground=RED if r.kind == "NAK" else GREEN)
        self._log(f"{what}: {r.summary()}{jit}")
        self._csv(t)

    def _log(self, s: str):
        self.t_log.insert("end", time.strftime("%H:%M:%S ") + s + "\n")
        self.t_log.see("end")

    def _csv(self, t: Transaction):
        if not self.csv_path:
            return
        new = not self.csv_path.exists()
        with self.csv_path.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(CSV_HEAD)
            w.writerow(csv_row(t))

    def _quit(self):
        self.jobs.put(("quit",))
        self.root.after(200, self.root.destroy)


CSV_HEAD = ["time", "sent", "reply", "err", "seq", "bank", "lna_path", "auto", "fmn",
            "vcoax_v", "i_lna_ma", "vscap_v", "flags", "offset_hz", "snr_db", "jitter_ms"]


def describe(frame) -> str:
    if frame is None:
        return "listen"
    if isinstance(frame, ConfigFrame):
        return "panel " + {v: k for k, v in PANEL_MODES.items()}[frame.panel]
    s = f"F{frame.bank}{' LNA' if frame.lna else ''}{' QUIET' if frame.quiet else ''}"
    return s + (" CLR" if frame.clr else "")


def csv_row(t: Transaction) -> list:
    r, h = t.reply, t.heard
    base = [time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t.t)), describe(t.frame)]
    if r is None:
        return base + ["none"] + [""] * 10 + [f"{h.offset_hz:.0f}" if h else "",
                                              f"{h.snr_db:.1f}" if h else "", f"{t.jitter_ms:.1f}"]
    return base + [r.kind, r.err, r.seq, r.bank, int(r.lna_path), int(r.auto), int(r.fmn),
                   f"{r.vcoax_v:.2f}", r.i_lna_ma, f"{r.vscap_v:.2f}", ",".join(r.flags),
                   f"{h.offset_hz:.0f}", f"{h.snr_db:.1f}", f"{t.jitter_ms:.1f}"]


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Coaxolotl control panel")
    ap.add_argument("--sim", action="store_true", help="start with the firmware simulator")
    ap.add_argument("-d", "--device", type=int, default=0, help="RTL-SDR index")
    ap.add_argument("--gain", type=float, default=20.0, help="tuner gain in dB")
    ap.add_argument("--beacon-hz", type=float, default=bc.BEACON_HZ)
    ap.add_argument("--log", help="append every reply to this CSV file")
    ap.add_argument("--connect", action="store_true", help="connect at start-up")
    args = ap.parse_args(argv)
    root = tk.Tk()
    app = App(root, args)
    if args.connect:
        root.after(200, app._connect)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
