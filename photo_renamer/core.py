"""Lógica del renombrador: lee la fecha de captura (EXIF) de las fotos de una
tarjeta, decide el nombre nuevo y la subcarpeta de destino, y las copia.

Esquema del nombre:
    {INICIALES}_{AAAA-MM-DD}_{HH-MM-SS}_{proyecto}_{NN}.EXT

NN es un contador de 2 dígitos que solo distingue fotos tomadas en el mismo
segundo (ráfagas): la única de ese segundo es _01; en una ráfaga, _01, _02...

Subcarpeta de destino:
    {AAAA-MM-DD}_{proyecto}
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import exifread

# Archivo principal que define la foto (por orden de preferencia).
MAIN_EXTS = (".nef", ".jpg", ".jpeg")
# Archivos que acompañan a la foto y se renombran con su mismo nombre.
COMPANION_EXTS = (".nef", ".jpg", ".jpeg", ".xmp")

REGISTRY_NAME = ".renombrador_registro.json"
LOG_NAME = "registro_importaciones.csv"

# Estados de cada foto en el plan
OK = "Pendiente"
IMPORTED = "Ya importada"
NO_DATE = "Sin fecha EXIF"
CONFLICT = "Conflicto"
UNCHANGED = "Sin cambios"


@dataclass
class Photo:
    main: Path
    companions: list[Path] = field(default_factory=list)
    taken: datetime | None = None
    subsec: float = 0.0
    number: int = 0
    size: int = 0
    error: str = ""

    @property
    def signature(self) -> str:
        """Identifica la foto aunque se vuelva a leer de la tarjeta otro día."""
        when = self.taken.isoformat() if self.taken else "?"
        return f"{self.main.name}|{when}|{self.subsec}|{self.size}"


@dataclass
class PlanItem:
    photo: Photo
    status: str
    folder: Path | None = None
    new_name: str = ""          # nombre del archivo principal
    targets: list[tuple[Path, Path]] = field(default_factory=list)  # (origen, destino)


def clean_text(text: str) -> str:
    """'Viaje Portugal Ñ' -> 'viaje-portugal-n' (sin tildes ni espacios)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text.lower())
    return text.strip("-")


def clean_prefix(text: str) -> str:
    """Iniciales: solo letras y números, en mayúsculas."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "", text).upper()


def read_capture_time(path: Path) -> tuple[datetime | None, float]:
    """Devuelve (fecha de captura, fracción de segundo) leídas del EXIF."""
    with open(path, "rb") as f:
        tags = exifread.process_file(f, details=False)

    raw = None
    for key in ("EXIF DateTimeOriginal", "EXIF DateTimeDigitized", "Image DateTime"):
        if key in tags:
            raw = str(tags[key]).strip()
            break
    if not raw:
        return None, 0.0

    try:
        taken = datetime.strptime(raw[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None, 0.0

    subsec = 0.0
    sub = tags.get("EXIF SubSecTimeOriginal") or tags.get("EXIF SubSecTime")
    if sub:
        digits = re.sub(r"\D", "", str(sub))
        if digits:
            subsec = float("0." + digits)
    return taken, subsec


def file_number(path: Path) -> int:
    """DSC_0123.NEF -> 123 (para desempatar si coinciden hasta las centésimas)."""
    match = re.search(r"(\d+)$", path.stem)
    return int(match.group(1)) if match else 0


def scan_source(source: Path) -> list[Photo]:
    """Recorre la tarjeta (con subcarpetas) y agrupa cada foto con sus acompañantes."""
    photos: list[Photo] = []
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        groups: dict[str, list[Path]] = {}
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() in COMPANION_EXTS:
                groups.setdefault(path.stem.lower(), []).append(path)

        for _, files in sorted(groups.items()):
            main = None
            for ext in MAIN_EXTS:
                main = next((p for p in files if p.suffix.lower() == ext), None)
                if main:
                    break
            if main is None:  # solo un .xmp suelto: no es una foto
                continue
            companions = [p for p in files if p != main]
            photos.append(Photo(main=main, companions=companions, number=file_number(main)))
    return photos


def read_metadata(photos: list[Photo], progress=None) -> None:
    for i, photo in enumerate(photos, 1):
        try:
            photo.size = photo.main.stat().st_size
            photo.taken, photo.subsec = read_capture_time(photo.main)
        except Exception as exc:  # archivo dañado o ilegible
            photo.error = str(exc)
        if progress:
            progress(i, len(photos))


def load_registry(dest: Path) -> dict[str, str]:
    try:
        return json.loads((dest / REGISTRY_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_registry(dest: Path, registry: dict[str, str]) -> None:
    path = dest / REGISTRY_NAME
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def build_plan(photos: list[Photo], dest: Path, prefix: str, project: str) -> list[PlanItem]:
    """Calcula carpeta y nombre nuevo de cada foto sin tocar nada en disco."""
    registry = load_registry(dest)
    used: dict[str, int] = {}          # base del nombre -> último contador usado
    existing_cache: dict[Path, set[str]] = {}
    seen: set[str] = set()

    def existing_names(folder: Path) -> set[str]:
        if folder not in existing_cache:
            existing_cache[folder] = (
                {p.name.lower() for p in folder.iterdir()} if folder.is_dir() else set()
            )
        return existing_cache[folder]

    def last_counter(folder: Path, base: str) -> int:
        """Mayor contador que ya existe en destino para esa base (de importaciones anteriores)."""
        pattern = re.compile(re.escape(base.lower()) + r"_(\d+)\.")
        nums = [int(m.group(1)) for n in existing_names(folder) if (m := pattern.match(n))]
        return max(nums, default=0)

    dated = [p for p in photos if p.taken]
    dated.sort(key=lambda p: (p.taken, p.subsec, str(p.main.parent).lower(), p.number))

    plan: list[PlanItem] = []
    for photo in dated:
        sig = photo.signature
        if sig in registry or sig in seen:
            item = PlanItem(photo, IMPORTED, new_name=registry.get(sig, "(repetida en la tarjeta)"))
            plan.append(item)
            continue
        seen.add(sig)

        folder = dest / f"{photo.taken:%Y-%m-%d}_{project}"
        base = f"{prefix}_{photo.taken:%Y-%m-%d_%H-%M-%S}_{project}"
        if base not in used:
            used[base] = last_counter(folder, base)
        used[base] += 1
        stem = f"{base}_{used[base]:02d}"

        targets = [(src, folder / f"{stem}{src.suffix.upper()}")
                   for src in [photo.main, *photo.companions]]
        clash = any(t.name.lower() in existing_names(folder) for _, t in targets)
        plan.append(PlanItem(
            photo, CONFLICT if clash else OK, folder, targets[0][1].name, targets,
        ))

    for photo in photos:
        if not photo.taken:
            plan.append(PlanItem(photo, NO_DATE, new_name=photo.error))
    return plan


def build_rename_plan(photos: list[Photo], prefix: str, project: str) -> list[PlanItem]:
    """Modo «solo renombrar»: cada foto se queda en su carpeta y solo cambia de nombre."""
    dated = [p for p in photos if p.taken]
    dated.sort(key=lambda p: (p.taken, p.subsec, str(p.main.parent).lower(), p.number))
    renamed = {f.resolve() for p in dated for f in [p.main, *p.companions]}

    used: dict[tuple[Path, str], int] = {}
    others_cache: dict[Path, set[str]] = {}

    def other_names(folder: Path) -> set[str]:
        """Archivos de la carpeta que NO se van a renombrar (no se pueden pisar)."""
        if folder not in others_cache:
            others_cache[folder] = {p.name.lower() for p in folder.iterdir()
                                    if p.resolve() not in renamed}
        return others_cache[folder]

    plan: list[PlanItem] = []
    for photo in dated:
        folder = photo.main.parent
        base = f"{prefix}_{photo.taken:%Y-%m-%d_%H-%M-%S}_{project}"
        key = (folder, base)
        used[key] = used.get(key, 0) + 1
        stem = f"{base}_{used[key]:02d}"

        targets = [(src, folder / f"{stem}{src.suffix.upper()}")
                   for src in [photo.main, *photo.companions]]
        if all(src.name == t.name for src, t in targets):
            status = UNCHANGED
        elif any(t.name.lower() in other_names(folder) for _, t in targets):
            status = CONFLICT
        else:
            status = OK
        plan.append(PlanItem(photo, status, folder, targets[0][1].name, targets))

    for photo in photos:
        if not photo.taken:
            plan.append(PlanItem(photo, NO_DATE, new_name=photo.error))
    return plan


def execute_renames(plan: list[PlanItem], progress=None) -> dict[str, int]:
    """Renombra en el sitio. Va en dos pasos (nombre temporal y nombre final) para
    que dos fotos puedan intercambiarse el nombre sin pisarse."""
    todo = [item for item in plan if item.status == OK]
    stats = {"renombradas": 0, "errores": 0}
    total = len(todo) * 2
    step = 0

    # Paso 1: nombre temporal
    staged: list[tuple[PlanItem, list[tuple[Path, Path, Path]]]] = []
    for n, item in enumerate(todo):
        moves = []
        try:
            for i, (src, target) in enumerate(item.targets):
                tmp = src.with_name(f".renombrando_{n}_{i}{src.suffix}")
                src.rename(tmp)
                moves.append((src, tmp, target))
            staged.append((item, moves))
        except Exception as exc:
            for src, tmp, _ in moves:  # deshacer esta foto
                tmp.rename(src)
            item.status = f"Error: {exc}"
            stats["errores"] += 1
        step += 1
        if progress:
            progress(step, total)

    # Paso 2: nombre final
    for item, moves in staged:
        done = []
        try:
            for src, tmp, target in moves:
                if target.exists():
                    raise FileExistsError(f"Ya existe {target.name}")
            for src, tmp, target in moves:
                tmp.rename(target)
                done.append(target)
            item.status = "Renombrada"
            stats["renombradas"] += 1
        except Exception as exc:
            for src, tmp, target in moves:  # vuelve a su nombre original
                (target if target in done else tmp).rename(src)
            item.status = f"Error: {exc}"
            stats["errores"] += 1
        step += 1
        if progress:
            progress(step, total)
    return stats


def execute_plan(plan: list[PlanItem], dest: Path, progress=None) -> dict[str, int]:
    """Copia y renombra. Nunca sobrescribe; los originales no se tocan."""
    registry = load_registry(dest)
    todo = [item for item in plan if item.status == OK]
    stats = {"copiadas": 0, "errores": 0}
    log_rows = []

    dest.mkdir(parents=True, exist_ok=True)
    for i, item in enumerate(todo, 1):
        try:
            item.folder.mkdir(parents=True, exist_ok=True)
            for src, target in item.targets:
                if target.exists():
                    raise FileExistsError(f"Ya existe {target.name}")
            done = []
            try:
                for src, target in item.targets:
                    shutil.copy2(src, target)
                    done.append(target)
                    if target.stat().st_size != src.stat().st_size:
                        raise OSError(f"Copia incompleta de {src.name}")
            except Exception:
                for target in done:  # no dejar copias a medias
                    target.unlink(missing_ok=True)
                raise
            rel = str(item.targets[0][1].relative_to(dest))
            registry[item.photo.signature] = rel
            item.status = "Copiada"
            stats["copiadas"] += 1
            for src, target in item.targets:
                log_rows.append([datetime.now().isoformat(timespec="seconds"),
                                 str(src), str(target)])
        except Exception as exc:
            item.status = f"Error: {exc}"
            stats["errores"] += 1

        if i % 25 == 0:
            save_registry(dest, registry)
        if progress:
            progress(i, len(todo))

    save_registry(dest, registry)
    if log_rows:
        log_path = dest / LOG_NAME
        new_file = not log_path.exists()
        with open(log_path, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            if new_file:
                writer.writerow(["fecha_importacion", "origen", "destino"])
            writer.writerows(log_rows)
    return stats
