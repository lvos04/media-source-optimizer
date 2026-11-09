"""Peil-review MSO (stap 5) — schermafdrukken verkleinen (alleen-lezen hulpscript, hoort niet bij de app).

Zet elke PNG in deze map (en submappen) om naar een 256-kleurenpalet zonder dithering (± 3× kleiner; vlakken blijven
gelijk, alleen anti-aliasing verschuift minimaal). Contrast wordt daarom NOOIT uit deze PNG's gemeten, maar in de
browser (metingen.json: `tekst_*`, `ui_*`, axe) en in contrast.json van de ontwerptaal.

Gebruik: python3 docs/screenshots/peil/review/verklein.py   (vereist Pillow)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent


def main() -> None:
    before = after = 0
    for png in sorted(HERE.rglob("*.png")):
        size = png.stat().st_size
        with Image.open(png) as im:
            if im.mode == "P":
                continue
            reduced = im.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        reduced.save(png, optimize=True)
        before += size
        after += png.stat().st_size
    print(f"PNG {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
