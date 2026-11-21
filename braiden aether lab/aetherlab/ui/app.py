"""Tkinter bench UI. No third-party widgets: it has to run on a clean box."""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import BANNER, __version__, config, log as alog
from ..errors import AetherError
from ..transport.usb import enumerate_devices

LOG = alog.get(debug=True, path=None)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Braiden's Aether Lab {__version__}")
        self.geometry("1020x680")
        self.q = queue.Queue()
        self.session = None
        self._build()
        self.after(120, self._drain)
        self.after(1500, self.refresh_devices)

    def _build(self):
        bar = ttk.Frame(self, padding=6)
        bar.pack(fill="x")
        ttk.Button(bar, text="Rescan", command=self.refresh_devices).pack(side="left")
        self.dev_label = ttk.Label(bar, text="no device", foreground="#a00")
        self.dev_label.pack(side="left", padx=10)
        ttk.Button(bar, text="Attach EDL", command=self.attach_edl).pack(side="right")
        ttk.Button(bar, text="Attach MTK", command=self.attach_mtk).pack(side="right", padx=6)

        split = ttk.PanedWindow(self, orient="horizontal")
        split.pack(fill="both", expand=True, padx=6, pady=6)

        left = ttk.Frame(split)
        cols = ("lun", "name", "sectors", "start")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=24)
        for c, w in zip(cols, (50, 260, 100, 110)):
            self.tree.heading(c, text=c.upper())
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True)
        split.add(left, weight=3)

        right = ttk.Frame(split)
        for label, fn in (
            ("Dump selected", self.do_dump),
            ("Write image to selected", self.do_write),
            ("Erase selected", self.do_erase),
            ("Erase FRP", self.do_frp),
            ("Bootloader unlock", self.do_unlock),
            ("Full backup", self.do_backup),
        ):
            ttk.Button(right, text=label, command=fn).pack(fill="x", pady=3)
        split.add(right, weight=1)

        self.text = tk.Text(self, height=16, bg="#101014", fg="#cfd6e4",
                            insertbackground="#cfd6e4", font=("TkFixedFont", 9))
        self.text.pack(fill="both", padx=6, pady=(0, 6))

    # ---- plumbing --------------------------------------------------------
    def _drain(self):
        while True:
            try:
                line = self.q.get_nowait()
            except queue.Empty:
                break
            self.text.insert("end", line + "\n")
            self.text.see("end")
        self.after(120, self._drain)

    def run_bg(self, fn, done=None):
        def wrap():
            try:
                r = fn()
                if done:
                    self.q.put(f"[done] {r}")
            except AetherError as e:
                self.q.put(f"[error] {e}")
            except Exception as e:  # noqa: BLE001
                self.q.put(f"[crash] {type(e).__name__}: {e}")
        threading.Thread(target=wrap, daemon=True).start()

    def refresh_devices(self):
        devs = enumerate_devices()
        if devs:
            d = devs[0]
            self.dev_label.config(text=f"{d['label']} {d['vid']:04x}:{d['pid']:04x}",
                                  foreground="#070")
        else:
            self.dev_label.config(text="no device", foreground="#a00")
            self.tree.delete(*self.tree.get_children())

    def _need_session(self):
        if not self.session:
            messagebox.showwarning("Aether Lab", "Attach a device first")
            return False
        return True

    # ---- attach ----------------------------------------------------------
    def attach_edl(self):
        chip = tk.simpledialog.askstring("Chipset", "Chipset dir (blank = auto)")
        self.q.put(f"[..] attaching EDL {chip or ''}")
        from ..ops.edl import EdlSession

        def work():
            s = EdlSession(LOG, chipset=chip or None).__enter__()
            self.session = s
            return s
        self.run_bg(work, done=self._fill_tree)

    def attach_mtk(self):
        soc = tk.simpledialog.askstring("SoC", "SoC dir e.g. mt6789")
        if not soc:
            return
        from ..ops.mtk import MtkSession

        def work():
            s = MtkSession(LOG, soc).__enter__()
            self.session = s
            return s
        self.run_bg(work, done=self._fill_tree)

    def _fill_tree(self, *_):
        self.tree.delete(*self.tree.get_children())
        if not self.session:
            return
        for lun, e in self.session.list_partitions():
            self.tree.insert("", "end", values=(lun, e.name, e.sectors, e.first_lba))

    def _sel(self):
        sel = self.tree.selection()
        if not sel:
            return None
        v = self.tree.item(sel[0], "values")
        return int(v[0]), v[1]

    # ---- actions ---------------------------------------------------------
    def do_dump(self):
        if not (self._need_session() and (s := self._sel())):
            return
        lun, name = s
        dest = filedialog.asksaveasfilename(initialfile=f"{name}.img")
        if dest:
            self.run_bg(lambda: self.session.dump(name, dest, lun=lun))

    def do_write(self):
        if not (self._need_session() and (s := self._sel())):
            return
        lun, name = s
        src = filedialog.askopenfilename()
        if src:
            self.run_bg(lambda: self.session.write(name, src, lun=lun))

    def do_erase(self):
        if not (self._need_session() and (s := self._sel())):
            return
        lun, name = s
        if messagebox.askyesno("Erase", f"Erase {name} on LUN {lun}?"):
            self.run_bg(lambda: self.session.erase(name, lun=lun))

    def do_frp(self):
        if self._need_session():
            self.run_bg(self.session.frp_erase)

    def do_unlock(self):
        if self._need_session() and hasattr(self.session, "unlock_bootloader"):
            self.run_bg(self.session.unlock_bootloader)

    def do_backup(self):
        if not self._need_session():
            return
        dest = filedialog.askdirectory()
        if dest:
            from ..ops import backup
            self.run_bg(lambda: backup.backup(self.session, dest, log=LOG))


def main():
    config.ensure_dirs()
    app = App()
    app.q.put(BANNER)
    app.mainloop()
    return 0
