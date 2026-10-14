"""Pruebas con una tarjeta simulada. Ejecutar desde la raíz: python -m unittest discover tests"""

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from photo_renamer import core


def make_photo(path: Path, when: str | None, subsec: str = "00"):
    """Crea una imagen con EXIF (exifread la lee igual que un NEF por su cabecera)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    if when:
        ifd = exif.get_ifd(0x8769)
        ifd[0x9003] = when      # DateTimeOriginal
        ifd[0x9291] = subsec    # SubSecTimeOriginal
    Image.new("RGB", (8, 8)).save(path, format="JPEG", exif=exif)


class RenombradorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.card = root / "SD" / "DCIM"
        self.dest = root / "Fotos"
        a, b = self.card / "100NIKON", self.card / "101NIKON"
        # Ráfaga: 3 fotos en el mismo segundo, repartidas entre dos carpetas y desordenadas
        make_photo(a / "DSC_0010.NEF", "2026:07:14 18:32:07", "50")
        make_photo(a / "DSC_0011.NEF", "2026:07:14 18:32:07", "10")
        make_photo(b / "DSC_0010.NEF", "2026:07:14 18:32:07", "90")   # mismo nombre que en 100NIKON
        # Foto suelta con JPG y XMP acompañantes
        make_photo(a / "DSC_0012.NEF", "2026:07:14 18:35:51")
        make_photo(a / "DSC_0012.JPG", "2026:07:14 18:35:51")
        (a / "DSC_0012.xmp").write_text("<x/>")
        # Otro día
        make_photo(b / "DSC_0001.NEF", "2026:07:15 09:00:00")
        # Sin EXIF
        make_photo(b / "DSC_0002.NEF", None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_import(self, project="Viaje Portugal"):
        photos = core.scan_source(self.card.parent)
        core.read_metadata(photos)
        plan = core.build_plan(photos, self.dest, "ISA", core.clean_text(project))
        stats = core.execute_plan(plan, self.dest)
        return plan, stats

    def files(self):
        return sorted(str(p.relative_to(self.dest)).replace("\\", "/")
                      for p in self.dest.rglob("*") if p.is_file() and not p.name.startswith((".", "registro")))

    def test_import_completo(self):
        plan, stats = self.run_import()
        self.assertEqual(stats, {"copiadas": 5, "errores": 0})
        self.assertEqual(self.files(), [
            "2026-07-14_viaje-portugal/ISA_2026-07-14_18-32-07_viaje-portugal_01.NEF",  # .10
            "2026-07-14_viaje-portugal/ISA_2026-07-14_18-32-07_viaje-portugal_02.NEF",  # .50
            "2026-07-14_viaje-portugal/ISA_2026-07-14_18-32-07_viaje-portugal_03.NEF",  # .90
            "2026-07-14_viaje-portugal/ISA_2026-07-14_18-35-51_viaje-portugal_01.JPG",
            "2026-07-14_viaje-portugal/ISA_2026-07-14_18-35-51_viaje-portugal_01.NEF",
            "2026-07-14_viaje-portugal/ISA_2026-07-14_18-35-51_viaje-portugal_01.XMP",
            "2026-07-15_viaje-portugal/ISA_2026-07-15_09-00-00_viaje-portugal_01.NEF",
        ])
        # Orden de la ráfaga por centésimas, no por nombre de archivo
        burst = {i.new_name[-6:-4]: i.photo for i in plan if "18-32-07" in i.new_name}
        self.assertEqual(burst["01"].main.name, "DSC_0011.NEF")
        self.assertEqual(burst["03"].main.parent.name, "101NIKON")
        self.assertEqual([i.status for i in plan if i.status == core.NO_DATE], [core.NO_DATE])
        # Los originales siguen en la tarjeta
        self.assertTrue((self.card / "100NIKON" / "DSC_0010.NEF").exists())

    def test_reimportar_no_duplica(self):
        self.run_import()
        before = self.files()
        plan, stats = self.run_import()
        self.assertEqual(stats["copiadas"], 0)
        self.assertEqual(sum(i.status == core.IMPORTED for i in plan), 5)
        self.assertEqual(self.files(), before)

    def test_foto_nueva_en_segundo_ya_existente_continua_contador(self):
        self.run_import()
        make_photo(self.card / "102NIKON" / "DSC_0500.NEF", "2026:07:14 18:32:07", "95")
        _, stats = self.run_import()
        self.assertEqual(stats["copiadas"], 1)
        self.assertIn("2026-07-14_viaje-portugal/ISA_2026-07-14_18-32-07_viaje-portugal_04.NEF", self.files())

    def run_rename(self, folder, project="Viaje Portugal"):
        photos = core.scan_source(folder)
        core.read_metadata(photos)
        plan = core.build_rename_plan(photos, "ISA", core.clean_text(project))
        return plan, core.execute_renames(plan)

    def tree(self, folder):
        return sorted(str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*"))

    def test_solo_renombrar_en_el_sitio(self):
        before_dirs = [p for p in self.tree(self.card) if "." not in p.split("/")[-1]]
        plan, stats = self.run_rename(self.card)
        self.assertEqual(stats, {"renombradas": 5, "errores": 0})
        self.assertEqual(self.tree(self.card), sorted(before_dirs + [
            "100NIKON/ISA_2026-07-14_18-32-07_viaje-portugal_01.NEF",
            "100NIKON/ISA_2026-07-14_18-32-07_viaje-portugal_02.NEF",
            "100NIKON/ISA_2026-07-14_18-35-51_viaje-portugal_01.JPG",
            "100NIKON/ISA_2026-07-14_18-35-51_viaje-portugal_01.NEF",
            "100NIKON/ISA_2026-07-14_18-35-51_viaje-portugal_01.XMP",
            "101NIKON/DSC_0002.NEF",                                    # sin EXIF: se queda igual
            "101NIKON/ISA_2026-07-14_18-32-07_viaje-portugal_01.NEF",   # su carpeta, su contador
            "101NIKON/ISA_2026-07-15_09-00-00_viaje-portugal_01.NEF",
        ]))
        self.assertFalse(self.dest.exists())  # no se crea nada fuera

        # Volver a ejecutarlo no cambia nada
        plan, stats = self.run_rename(self.card)
        self.assertEqual(stats["renombradas"], 0)
        self.assertEqual(sum(i.status == core.UNCHANGED for i in plan), 5)

    def test_corrige_nombres_antiguos_e_intercambia_sin_pisar(self):
        folder = self.card / "100NIKON"
        self.run_rename(folder)
        # Simula nombres antiguos cruzados: cada foto de la ráfaga tiene el nombre de la otra
        a = folder / "ISA_2026-07-14_18-32-07_viaje-portugal_01.NEF"
        b = folder / "ISA_2026-07-14_18-32-07_viaje-portugal_02.NEF"
        a.rename(folder / "x.NEF"); b.rename(a); (folder / "x.NEF").rename(b)
        size_a = a.stat().st_size
        plan, stats = self.run_rename(folder)
        self.assertEqual(stats, {"renombradas": 2, "errores": 0})
        self.assertEqual(b.stat().st_size, size_a)
        self.assertFalse(any(p.name.startswith(".renombrando") for p in folder.iterdir()))

    def test_no_pisa_archivos_ajenos(self):
        folder = self.card / "102NIKON"
        make_photo(folder / "DSC_0001.NEF", "2026:07:15 09:00:00")
        ajeno = folder / "ISA_2026-07-15_09-00-00_viaje-portugal_01.NEF"
        ajeno.write_text("archivo sin EXIF que ya ocupa ese nombre")
        plan, stats = self.run_rename(folder)
        statuses = {i.photo.main.name: i.status for i in plan}
        self.assertEqual(statuses["DSC_0001.NEF"], core.CONFLICT)
        self.assertEqual(stats["renombradas"], 0)
        self.assertEqual(ajeno.read_text(), "archivo sin EXIF que ya ocupa ese nombre")
        self.assertTrue((folder / "DSC_0001.NEF").exists())

    def test_limpieza_de_textos(self):
        self.assertEqual(core.clean_text("  Viaje a Óbidos & Peniche! "), "viaje-a-obidos-peniche")
        self.assertEqual(core.clean_prefix("i.s.a"), "ISA")


if __name__ == "__main__":
    unittest.main()
