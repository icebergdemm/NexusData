"""
One-off, developer-machine-only tool: extracts item icons + Russian display
names from a Barotrauma installation's Content directory into this repo's
static/ folder, so the running app never needs the actual game files —
only the pre-cropped PNGs + a JSON name catalog this script produces.

Not part of the running app. Re-run manually whenever the mod/game content
updates (see WORLD_STATE_API_GUIDE.md / routes/world_state.py for where the
resulting identifiers show up — WorldState.items[].item).

Usage:
    python tools/extract_barotrauma_items.py --content-dir "C:\\...\\MorePlugins\\Content"

Output:
    static/item_icons/<identifier>.png   — one cropped icon per item that has one
    static/item_catalog.json             — {identifier: {"name": str, "has_icon": bool}}

Item prefab schema (confirmed by reading actual Barotrauma Content files):
  - <Items> root, one or more <Item identifier="..." name=""> children per
    XML file under Items/**/*.xml. The `name` attribute is always empty in
    practice — real display names live only in the Texts/ localization
    files, keyed by `entityname.<identifier>`.
  - Icon source, in priority order: a direct child <InventoryIcon
    texture="..." sourcerect="x,y,w,h"/> (clean square icon), else the
    first direct child <Sprite texture="..." sourcerect="..."/> (world
    render sprite, often non-square, but a serviceable fallback). Sprites
    nested inside <Wearable> are per-limb render sprites, not icons —
    Element.find() with a bare tag name only matches direct children, so
    those are naturally skipped.
  - `texture` is usually a path rooted at the mod folder (e.g.
    "Content/Items/Weapons/weapons_new.png"), but sometimes relative to the
    XML file's own directory instead (e.g. "Misc.png" inside
    Items/Misc/misc.xml resolving to Items/Misc/Misc.png) — both are tried.
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

try:
    from PIL import Image
except ImportError:
    print("Pillow is required — pip install Pillow (already in requirements.txt)", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS_OUT_DIR = os.path.join(REPO_ROOT, "static", "item_icons")
CATALOG_OUT_PATH = os.path.join(REPO_ROOT, "static", "item_catalog.json")


def _iter_item_xml_files(content_dir: str):
    items_dir = os.path.join(content_dir, "Items")
    for dirpath, _dirnames, filenames in os.walk(items_dir):
        for fn in filenames:
            if fn.lower().endswith(".xml"):
                yield os.path.join(dirpath, fn)


def _resolve_texture_path(content_dir: str, xml_path: str, texture: str) -> str | None:
    texture = texture.replace("\\", "/")
    mod_root = os.path.dirname(content_dir)  # parent of ".../Content"
    candidates = [
        os.path.join(mod_root, *texture.split("/")),
        os.path.join(os.path.dirname(xml_path), *texture.split("/")),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _parse_ints(raw: str, count: int):
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != count:
        return None
    try:
        return tuple(int(float(p)) for p in parts)
    except ValueError:
        return None


def _icon_rect(icon_el) -> tuple | None:
    """Two addressing schemes seen in the wild: a plain `sourcerect="x,y,w,h"`
    pixel rect, or (common for clothing packed into a uniform grid atlas,
    e.g. InventoryIconAtlas2.png) `sheetindex="col,row"` +
    `sheetelementsize="w,h"` — a grid-cell reference instead of raw pixels.
    Missing this second form was the reason ~80 wearable/clothing items
    (minerclothes, banditclothes2, etc.) showed up with no icon at all."""
    sourcerect = icon_el.get("sourcerect")
    if sourcerect:
        return _parse_ints(sourcerect, 4)
    sheetindex = icon_el.get("sheetindex")
    sheetelementsize = icon_el.get("sheetelementsize")
    if sheetindex and sheetelementsize:
        idx = _parse_ints(sheetindex, 2)
        size = _parse_ints(sheetelementsize, 2)
        if idx and size:
            col, row = idx
            w, h = size
            return (col * w, row * h, w, h)
    return None


def _load_text_catalog(texts_dir: str, filename: str) -> dict:
    """Flat XML: <entityname.spear>Гарпун</entityname.spear> — tag IS the key."""
    path = os.path.join(texts_dir, filename)
    if not os.path.isfile(path):
        return {}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        print(f"  ! failed to parse {path}: {e}", file=sys.stderr)
        return {}
    return {el.tag: (el.text or "").strip() for el in root}


def _safe_filename(identifier: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in identifier)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--content-dir", required=True, help='Path to the Barotrauma mod\'s "Content" folder')
    args = parser.parse_args()

    content_dir = os.path.abspath(args.content_dir)
    if not os.path.isdir(os.path.join(content_dir, "Items")):
        print(f"'{content_dir}' does not look like a Barotrauma Content folder (no Items/ subfolder)", file=sys.stderr)
        sys.exit(1)

    texts_dir_ru = os.path.join(content_dir, "Texts", "Russian")
    texts_dir_en = os.path.join(content_dir, "Texts", "English")
    names_ru = _load_text_catalog(texts_dir_ru, "RussianVanilla.xml")
    names_en = _load_text_catalog(texts_dir_en, "EnglishVanilla.xml")
    print(f"Loaded {len(names_ru)} RU names, {len(names_en)} EN names")

    os.makedirs(ICONS_OUT_DIR, exist_ok=True)

    # Pass 1: collect each identifier's OWN icon source (if any) plus its
    # `variantof` base identifier (if any) — a "variantof" item (e.g.
    # screwdriverhardened variantof="screwdriver") inherits everything
    # including its icon from the base item unless it declares its own, so
    # this has to be resolved as a second pass once every identifier in the
    # whole content set is known (a variant can be defined in a different
    # file than its base, processed in either order).
    raw: dict = {}  # identifier -> {"texture_path": str|None, "rect": tuple|None, "variantof": str|None}

    for xml_path in _iter_item_xml_files(content_dir):
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError as e:
            print(f"  ! failed to parse {xml_path}: {e}", file=sys.stderr)
            continue

        # Only TOP-LEVEL <Item> prefabs — root.findall("Item") is
        # non-recursive (direct children only). Some container items
        # (e.g. a medkit) nest <Item identifier="..."> elements INSIDE
        # themselves to declare default starting contents — those are
        # instance placements referencing an identifier defined elsewhere,
        # not prefab definitions, and almost never carry their own
        # Sprite/InventoryIcon. Using root.iter("Item") (recursive) picked
        # those up too and could overwrite a real prefab's icon with a
        # spriteless duplicate processed later.
        items = [root] if root.tag == "Item" else root.findall("Item")
        for item_el in items:
            identifier = item_el.get("identifier")
            if not identifier:
                continue

            icon_el = item_el.find("InventoryIcon")
            if icon_el is None:
                icon_el = item_el.find("Sprite")

            texture_path = None
            rect = None
            if icon_el is not None:
                texture = icon_el.get("texture")
                rect = _icon_rect(icon_el)
                if texture and rect:
                    texture_path = _resolve_texture_path(content_dir, xml_path, texture)

            raw[identifier] = {
                "texture_path": texture_path,
                "rect": rect,
                "variantof": item_el.get("variantof"),
            }

    # Pass 2: resolve each identifier's effective icon, following variantof
    # chains (bounded — some modded content could in theory cycle).
    def _resolve(identifier: str, depth: int = 0):
        info = raw.get(identifier)
        if not info or depth > 8:
            return None, None
        if info["texture_path"] and info["rect"]:
            return info["texture_path"], info["rect"]
        if info["variantof"]:
            return _resolve(info["variantof"], depth + 1)
        return None, None

    catalog: dict = {}
    icons_written = 0
    missing_texture = 0
    no_sprite = 0

    for identifier, info in raw.items():
        texture_path, rect = _resolve(identifier)
        has_icon = False
        if texture_path and rect:
            try:
                x, y, w, h = rect
                with Image.open(texture_path) as img:
                    crop = img.convert("RGBA").crop((x, y, x + w, y + h))
                    out_path = os.path.join(ICONS_OUT_DIR, f"{_safe_filename(identifier)}.png")
                    crop.save(out_path)
                has_icon = True
                icons_written += 1
            except Exception as e:
                print(f"  ! crop failed for {identifier} ({texture_path}): {e}", file=sys.stderr)
        elif info["variantof"]:
            missing_texture += 1  # had a base to inherit from, but resolution still failed
        else:
            no_sprite += 1

        name = names_ru.get(f"entityname.{identifier}") or names_en.get(f"entityname.{identifier}") or identifier
        catalog[identifier] = {"name": name, "has_icon": has_icon}

    with open(CATALOG_OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2, sort_keys=True)

    print(f"\nDone: {len(catalog)} items catalogued, {icons_written} icons written")
    print(f"  {no_sprite} items had no InventoryIcon/Sprite at all (and no variantof base)")
    print(f"  {missing_texture} items had a variantof base but icon resolution still failed")
    print(f"Wrote {CATALOG_OUT_PATH}")
    print(f"Wrote icons to {ICONS_OUT_DIR}")


if __name__ == "__main__":
    main()
