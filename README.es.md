# Renombrador de fotos

🇬🇧 [English version](README.md)

Copia las fotos de la tarjeta SD a una carpeta de destino, ordenadas por día y renombradas con la fecha de captura del EXIF.

```
Destino\
  2026-07-14_viaje-portugal\
    ISA_2026-07-14_18-32-07_viaje-portugal_01.NEF
    ISA_2026-07-14_18-32-07_viaje-portugal_02.NEF   (ráfaga: mismo segundo)
    ISA_2026-07-14_18-35-51_viaje-portugal_01.NEF
  2026-07-15_viaje-portugal\
    ...
```

## Uso

1. Doble clic en `Iniciar.bat`.
2. Escribe las iniciales (ISA) y el nombre del proyecto o del día.
3. Elige el origen (la tarjeta, por ejemplo `E:\DCIM`; se recorren todas las subcarpetas) y el destino.
4. **Analizar**: muestra la vista previa sin tocar nada.
5. **Copiar y renombrar**.

Por defecto, el destino es la carpeta `Imágenes` del usuario. Las iniciales se recuerdan en `config.json`.

### Solo renombrar

Con la casilla **Solo renombrar** marcada, se cambia el nombre de las fotos de la carpeta de origen (y de sus subcarpetas) en el mismo sitio. No se copia, no se mueve y no se crea nada. El contador de ráfagas va por carpeta. Si lo ejecutas dos veces, la segunda no cambia nada.

## Reglas

- La fecha y la hora salen de `DateTimeOriginal` (EXIF), no de la fecha del ordenador.
- El contador `_NN` solo sube cuando hay varias fotos en el mismo segundo; se ordenan por centésimas de segundo (`SubSecTimeOriginal`).
- Los `.JPG` y `.XMP` con el mismo nombre que un NEF se copian con su mismo nombre nuevo.
- Los originales de la tarjeta no se modifican. Nunca se sobrescribe un archivo.
- Si vuelves a importar la misma tarjeta, las fotos ya copiadas se omiten (`.renombrador_registro.json` en el destino).
- Cada copia queda anotada en `registro_importaciones.csv` en el destino.
- Las fotos sin fecha EXIF se omiten y se marcan en naranja.

## Pruebas

Las pruebas crean fotos con EXIF usando Pillow:

```
pip install -e ".[dev]"
python -m unittest discover tests -v
```
