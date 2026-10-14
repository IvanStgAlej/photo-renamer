"""Interfaz gráfica del Renombrador de Fotos."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from photo_renamer import core

# En la raíz del proyecto (junto a Iniciar.bat), o junto al .exe si está empaquetado con PyInstaller.
BASE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_SOURCE = ""                          # el usuario elige la tarjeta
DEFAULT_DEST = str(Path.home() / "Pictures")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Renombrador de fotos")
        self.geometry("1100x650")
        self.minsize(850, 450)

        self.prefix = tk.StringVar(value="ISA")
        self.project = tk.StringVar()
        self.source = tk.StringVar(value=DEFAULT_SOURCE)
        self.dest = tk.StringVar(value=DEFAULT_DEST)
        self.only_rename = tk.BooleanVar(value=False)
        self.example = tk.StringVar()
        self.status = tk.StringVar(value="Elige origen, destino y nombre del proyecto, y pulsa «Analizar».")

        self.plan: list[core.PlanItem] = []
        self.events: queue.Queue = queue.Queue()
        self.busy = False

        self._load_config()
        self._build()
        for var in (self.prefix, self.project, self.source, self.dest, self.only_rename):
            var.trace_add("write", lambda *_: self._inputs_changed())
        self._inputs_changed()
        self.after(100, self._poll)

    # ---------- interfaz ----------
    def _build(self):
        pad = {"padx": 6, "pady": 4}
        form = ttk.Frame(self, padding=10)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Iniciales:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(form, textvariable=self.prefix, width=8).grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(form, text="Proyecto / día:").grid(row=1, column=0, sticky="w", **pad)
        entry = ttk.Entry(form, textvariable=self.project)
        entry.grid(row=1, column=1, columnspan=2, sticky="ew", **pad)
        entry.focus_set()

        self.lbl_source = ttk.Label(form, text="Origen (tarjeta SD):")
        self.lbl_source.grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(form, textvariable=self.source).grid(row=2, column=1, sticky="ew", **pad)
        ttk.Button(form, text="Elegir…", command=lambda: self._pick(self.source)).grid(row=2, column=2, **pad)

        self.lbl_dest = ttk.Label(form, text="Destino:")
        self.lbl_dest.grid(row=3, column=0, sticky="w", **pad)
        self.ent_dest = ttk.Entry(form, textvariable=self.dest)
        self.ent_dest.grid(row=3, column=1, sticky="ew", **pad)
        self.btn_dest = ttk.Button(form, text="Elegir…", command=lambda: self._pick(self.dest))
        self.btn_dest.grid(row=3, column=2, **pad)

        ttk.Checkbutton(
            form, variable=self.only_rename,
            text="Solo renombrar: cambia el nombre de las fotos dentro de la carpeta de origen "
                 "(sin copiar, mover ni crear carpetas)",
        ).grid(row=4, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(form, textvariable=self.example, foreground="#555").grid(
            row=5, column=0, columnspan=3, sticky="w", **pad)

        buttons = ttk.Frame(self, padding=(10, 0))
        buttons.pack(fill="x")
        self.btn_analyze = ttk.Button(buttons, text="1. Analizar", command=self.analyze)
        self.btn_analyze.pack(side="left", padx=4)
        self.btn_copy = ttk.Button(buttons, text="2. Copiar y renombrar", command=self.copy)
        self.btn_copy.pack(side="left", padx=4)
        self.btn_open = ttk.Button(buttons, text="Abrir destino", command=self.open_dest)
        self.btn_open.pack(side="left", padx=4)

        table = ttk.Frame(self, padding=10)
        table.pack(fill="both", expand=True)
        cols = ("original", "fecha", "carpeta", "nuevo", "estado")
        self.tree = ttk.Treeview(table, columns=cols, show="headings")
        for col, title, width in (
            ("original", "Archivo original", 230),
            ("fecha", "Fecha de captura", 160),
            ("carpeta", "Subcarpeta destino", 220),
            ("nuevo", "Nombre nuevo", 380),
            ("estado", "Estado", 120),
        ):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w")
        self.tree.tag_configure("warn", foreground="#b35900")
        self.tree.tag_configure("muted", foreground="#888")
        self.tree.tag_configure("error", foreground="#c00")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        bottom = ttk.Frame(self, padding=(10, 0, 10, 10))
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status).pack(anchor="w", pady=(4, 0))

    def _pick(self, var: tk.StringVar):
        folder = filedialog.askdirectory(initialdir=var.get() or None)
        if folder:
            var.set(os.path.normpath(folder))

    def _inputs_changed(self):
        # Cualquier cambio invalida el análisis anterior.
        self.plan = []
        if not self.busy:
            self.tree.delete(*self.tree.get_children())
        prefix = core.clean_prefix(self.prefix.get()) or "ISA"
        project = core.clean_text(self.project.get()) or "proyecto"
        name = f"{prefix}_2026-07-14_18-32-07_{project}_01.NEF"
        rename = self.only_rename.get()
        self.example.set(f"Ejemplo →  {name}" if rename else f"Ejemplo →  2026-07-14_{project}\\{name}")
        self.lbl_source.configure(text="Carpeta con las fotos:" if rename else "Origen (tarjeta SD):")
        state = ["disabled"] if rename else ["!disabled"]
        for widget in (self.lbl_dest, self.ent_dest, self.btn_dest):
            widget.state(state)
        self.btn_copy.configure(text="2. Renombrar" if rename else "2. Copiar y renombrar")
        self.btn_open.configure(text="Abrir carpeta" if rename else "Abrir destino")
        self._update_buttons()

    def _update_buttons(self):
        pending = any(i.status == core.OK for i in self.plan)
        self.btn_analyze.state(["disabled"] if self.busy else ["!disabled"])
        self.btn_copy.state(["!disabled"] if pending and not self.busy else ["disabled"])

    # ---------- acciones ----------
    def _validate(self):
        prefix = core.clean_prefix(self.prefix.get())
        project = core.clean_text(self.project.get())
        source = Path(self.source.get().strip())
        dest = Path(self.dest.get().strip())
        if not prefix:
            return messagebox.showwarning("Falta dato", "Escribe tus iniciales.")
        if not project:
            return messagebox.showwarning("Falta dato", "Escribe el nombre del proyecto o del día.")
        if not self.source.get().strip() or not source.is_dir():
            return messagebox.showwarning("Falta dato", "Elige una carpeta de origen que exista.")
        if self.only_rename.get():
            return prefix, project, source, None
        if not self.dest.get().strip():
            return messagebox.showwarning("Falta dato", "Elige la carpeta de destino.")
        if dest.resolve() == source.resolve() or source.resolve() in dest.resolve().parents:
            return messagebox.showwarning("Destino no válido", "El destino no puede estar dentro del origen.")
        return prefix, project, source, dest

    def analyze(self):
        values = self._validate()
        if not values:
            return
        self._save_config()
        prefix, project, source, dest = values
        self._start("Buscando fotos…")

        def work():
            photos = core.scan_source(source)
            self.events.put(("status", f"Leyendo fecha de {len(photos)} fotos…"))
            core.read_metadata(photos, lambda i, n: self.events.put(("progress", i, n)))
            if dest is None:
                plan = core.build_rename_plan(photos, prefix, project)
            else:
                plan = core.build_plan(photos, dest, prefix, project)
            self.events.put(("analyzed", plan))

        threading.Thread(target=work, daemon=True).start()

    def copy(self):
        pending = [i for i in self.plan if i.status == core.OK]
        if not pending:
            return
        if self.only_rename.get():
            return self._rename(pending)
        folders = sorted({i.folder.name for i in pending})
        if not messagebox.askyesno(
            "Confirmar",
            f"Se copiarán {len(pending)} fotos a {len(folders)} carpeta(s):\n\n"
            + "\n".join(folders[:15]) + ("\n…" if len(folders) > 15 else "")
            + "\n\nLos archivos de la tarjeta no se modifican. ¿Continuar?",
        ):
            return
        dest = Path(self.dest.get().strip())
        plan = self.plan
        self._start("Copiando…")

        def work():
            stats = core.execute_plan(plan, dest, lambda i, n: self.events.put(("progress", i, n)))
            self.events.put(("copied", plan, stats))

        threading.Thread(target=work, daemon=True).start()

    def _rename(self, pending):
        folders = {i.folder for i in pending}
        if not messagebox.askyesno(
            "Confirmar",
            f"Se cambiará el nombre de {len(pending)} fotos en {len(folders)} carpeta(s), "
            f"dentro de:\n\n{self.source.get()}\n\nNo se copia ni se mueve nada. ¿Continuar?",
        ):
            return
        plan = self.plan
        self._start("Renombrando…")

        def work():
            stats = core.execute_renames(plan, lambda i, n: self.events.put(("progress", i, n)))
            self.events.put(("renamed", plan, stats))

        threading.Thread(target=work, daemon=True).start()

    def open_dest(self):
        dest = (self.source if self.only_rename.get() else self.dest).get().strip()
        if dest and os.path.isdir(dest):
            os.startfile(dest)

    # ---------- hilo de trabajo ----------
    def _start(self, message):
        self.busy = True
        self.progress["value"] = 0
        self.status.set(message)
        self._update_buttons()

    def _poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "status":
                    self.status.set(event[1])
                elif kind == "progress":
                    _, i, n = event
                    self.progress["maximum"] = max(n, 1)
                    self.progress["value"] = i
                elif kind == "analyzed":
                    self.busy = False
                    self.plan = event[1]
                    self._show_plan()
                    self.status.set(self._summary())
                elif kind in ("copied", "renamed"):
                    self.busy = False
                    _, plan, stats = event
                    self._show_plan()
                    msg = (f"Listo: {stats['renombradas']} fotos renombradas" if kind == "renamed"
                           else f"Listo: {stats['copiadas']} fotos copiadas")
                    if stats["errores"]:
                        msg += f", {stats['errores']} con error (revisa la tabla)"
                    self.status.set(msg + ".")
                    self.plan = []
                    (messagebox.showwarning if stats["errores"] else messagebox.showinfo)("Terminado", msg + ".")
                self._update_buttons()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _show_plan(self):
        self.tree.delete(*self.tree.get_children())
        for item in self.plan:
            p = item.photo
            extra = f" (+{', '.join(c.suffix.upper()[1:] for c in p.companions)})" if p.companions else ""
            when = f"{p.taken:%Y-%m-%d %H:%M:%S}" if p.taken else "—"
            tag = ()
            if item.status in (core.NO_DATE, core.CONFLICT) or item.status.startswith("Error"):
                tag = ("error",) if item.status.startswith("Error") else ("warn",)
            elif item.status in (core.IMPORTED, core.UNCHANGED):
                tag = ("muted",)
            self.tree.insert("", "end", tags=tag, values=(
                p.main.name + extra, when,
                item.folder.name if item.folder else "",
                item.new_name, item.status,
            ))

    def _summary(self):
        if not self.plan:
            return "No se han encontrado fotos (NEF/JPG) en el origen."
        count = lambda s: sum(1 for i in self.plan if i.status == s)
        folders = {i.folder for i in self.plan if i.status == core.OK}
        action = "renombrar" if self.only_rename.get() else "copiar"
        parts = [f"{count(core.OK)} fotos para {action} en {len(folders)} carpeta(s)"]
        if count(core.UNCHANGED):
            parts.append(f"{count(core.UNCHANGED)} ya tienen el nombre correcto")
        if count(core.IMPORTED):
            parts.append(f"{count(core.IMPORTED)} ya importadas antes (se omiten)")
        if count(core.NO_DATE):
            parts.append(f"{count(core.NO_DATE)} sin fecha EXIF (se omiten)")
        if count(core.CONFLICT):
            parts.append(f"{count(core.CONFLICT)} en conflicto (se omiten)")
        return " · ".join(parts)

    # ---------- configuración ----------
    def _load_config(self):
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.prefix.set(data.get("prefix", "ISA"))

    def _save_config(self):
        data = {"prefix": self.prefix.get()}
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
