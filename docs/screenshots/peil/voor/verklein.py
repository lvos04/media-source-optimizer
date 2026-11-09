"""Peil-migratie MSO — afdrukken verkleinen en de nulmeting aanvullen (alleen-lezen hulpscript, hoort niet bij de app).

1. Vult in metingen.json `layout_breedte_px` en `overloop_apparaat_px` aan waar die ontbreken (afdrukken van vóór de
   meetcorrectie in maak-afdrukken.mjs). Bij een afdruk van de volledige pagina is de PNG-breedte precies de breedte
   van het layout-viewport, dus overloop = PNG-breedte − apparaatbreedte. Viewport-afdrukken (dialogen, menu's) krijgen
   `null`: daar kan het niet uit de PNG worden afgeleid.
2. Zet elke PNG om naar een 256-kleurenpalet zonder dithering (± 3× kleiner; UI-vlakken blijven gelijk, alleen
   anti-aliasing kan minimaal verschuiven). Kleur- en contrastmetingen komen daarom NOOIT uit deze PNG's, maar uit de
   browser (metingen.json, axe) en uit contrast.json van de ontwerptaal.

Gebruik: python3 docs/screenshots/peil/voor/verklein.py   (vereist Pillow)
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent


def main() -> None:
    meta_path = HERE / "metingen.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for key, entry in meta.items():
        png = HERE / f"{key}.png"
        if "fout" in entry or not png.exists():
            continue
        with Image.open(png) as im:
            width = im.width
        if "overloop_apparaat_px" not in entry:
            if entry.get("afdruk") == "volledige pagina":
                entry["layout_breedte_px"] = width
                entry["overloop_apparaat_px"] = width - int(entry["breedte"])
                entry["overloop_bron"] = "PNG-breedte (volledige pagina)"
            else:
                entry["layout_breedte_px"] = None
                entry["overloop_apparaat_px"] = None
                entry["overloop_bron"] = "niet bepaald (viewport-afdruk van vóór de meetcorrectie)"
        else:
            entry.setdefault("overloop_bron", "browser (scrollWidth)")
    meta_path.write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    before = after = 0
    for png in sorted(HERE.glob("*/*.png")):
        before += png.stat().st_size
        with Image.open(png) as im:
            if im.mode == "P":
                after += png.stat().st_size
                continue
            q = im.convert("RGB").quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
        q.save(png, optimize=True)
        after += png.stat().st_size
    print(f"PNG: {before / 1e6:.1f} MB → {after / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
