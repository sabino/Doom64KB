#!/usr/bin/env python3
"""Generate Neo Geo FIX menu art and the sprite-backed TITLEPIC source.

The retail WAD is needed only when regenerating these checked-in assets. Normal
ROM builds consume the generated FIX fragment and C headers directly.
"""

from __future__ import annotations

import argparse
from collections import Counter
import re
import struct
from dataclasses import dataclass
from pathlib import Path


FIX_TILE_BYTES = 32
# HELP2 is sprite-backed along with TITLEPIC and WIMAP0.  Reuse its former
# S-ROM range for FIX overlays that must sit above those sprite backgrounds.
FIX_UI_TILE_BASE = 2393
FIX_UI_TILE_END = 3460
TITLE_CROM_TILE_BASE = 272
BACKGROUND_COLUMNS = 38
BACKGROUND_ROWS = 28
BACKGROUND_TILE_COUNT = BACKGROUND_COLUMNS * BACKGROUND_ROWS
WIMAP_CROM_TILE_BASE = TITLE_CROM_TILE_BASE + BACKGROUND_TILE_COUNT
HELP_CROM_TILE_BASE = WIMAP_CROM_TILE_BASE + BACKGROUND_TILE_COUNT
SPRITE_TILE_HALF_BYTES = 64
WIPE_TILE_IDS = frozenset(slot << 8 for slot in range(16))

PATCH_NAMES = (
    "M_NGAME",
    "M_OPTION",
    "M_RDTHIS",
    "M_NEWG",
    "M_SKILL",
    "M_JKILL",
    "M_ROUGH",
    "M_HURT",
    "M_ULTRA",
    "M_NMARE",
    "M_SKULL1",
    "M_SKULL2",
    "M_OPTTTL",
    "M_SVOL",
    "M_SFXVOL",
    "M_MUSVOL",
    "M_THERML",
    "M_THERMM",
    "M_THERMR",
    "M_THERMO",
)

COMPACT_PATCHES = frozenset((
    "M_NEWG",
    "M_SKILL",
    "M_JKILL",
    "M_ROUGH",
    "M_HURT",
    "M_ULTRA",
    "M_NMARE",
    "M_SVOL",
    "M_SFXVOL",
    "M_MUSVOL",
))

TEXT_PATCHES = (
    ("doom_menu_text_end_game", "END GAME"),
    ("doom_menu_text_messages", "MESSAGES"),
    ("doom_menu_text_always_run", "ALWAYS RUN"),
    ("doom_menu_text_graphic_detail", "GRAPHIC DETAIL"),
    ("doom_menu_text_gamma_boost", "GAMMA BOOST"),
    ("doom_menu_text_sound_volume", "SOUND VOLUME"),
    ("doom_menu_text_off", "OFF"),
    ("doom_menu_text_on", "ON"),
    ("doom_menu_text_low", "LOW"),
    ("doom_menu_text_medium", "MEDIUM"),
    ("doom_menu_text_high", "HIGH"),
    *((f"doom_menu_text_{value}", str(value)) for value in range(6)),
)

INTERMISSION_PATCH_NAMES = (
    "WIF",
    "WIENTER",
    "WICOLON",
    "WISUCKS",
    "WIPCNT",
    "WIURH0",
    "WISPLAT",
    "WIOSTK",
    "WIOSTI",
    "WISCRT2",
    "WITIME",
    "WIMSTT",
    "WIPAR",
    *(f"WINUM{value}" for value in range(10)),
    *(f"WILV0{value}" for value in range(9)),
)

COMPACT_INTERMISSION_PATCHES = frozenset((
    "WIPCNT",
    *(f"WINUM{value}" for value in range(10)),
))


def neo_color(rgb: tuple[int, int, int]) -> int:
    """Match the compact Neo Geo color packing, including the dark bit."""
    r8, g8, b8 = rgb
    r, g, b = r8 // 8, g8 // 8, b8 // 8
    dark = 1 ^ (int(54.213 * r8 + 182.376 * g8 + 18.411 * b8) & 1)
    return (
        (dark << 15)
        | ((r & 1) << 14)
        | ((g & 1) << 13)
        | ((b & 1) << 12)
        | ((r & 0x1E) << 7)
        | ((g & 0x1E) << 3)
        | (b >> 1)
    )


@dataclass(frozen=True)
class Lump:
    offset: int
    size: int


class Wad:
    def __init__(self, data: bytes, endian: str = "<") -> None:
        if endian == ">":
            ident, count, _filler, directory = struct.unpack_from(">4sHHI", data, 0)
        else:
            ident, count, directory = struct.unpack_from("<4sII", data, 0)
        if ident not in (b"IWAD", b"PWAD"):
            raise ValueError(f"not a Doom WAD: {ident!r}")

        self.data = data
        self.lumps: dict[str, Lump] = {}
        for index in range(count):
            entry = directory + index * 16
            if endian == ">":
                offset, size, _filler, raw_name = struct.unpack_from(">IHH8s", data, entry)
            else:
                offset, size, raw_name = struct.unpack_from("<II8s", data, entry)
            name = raw_name.rstrip(b"\0").decode("ascii", "replace").upper()
            self.lumps[name] = Lump(offset, size)

    def get(self, name: str) -> bytes:
        lump = self.lumps[name.upper()]
        return self.data[lump.offset : lump.offset + lump.size]


def decode_patch(data: bytes) -> list[list[int]]:
    width, height, _left, _top = struct.unpack_from("<hhhh", data, 0)
    columns = [struct.unpack_from("<I", data, 8 + x * 4)[0] for x in range(width)]
    pixels = [[-1] * width for _ in range(height)]

    for x, offset in enumerate(columns):
        pos = offset
        while data[pos] != 0xFF:
            top = data[pos]
            length = data[pos + 1]
            pos += 3
            for y, color in enumerate(data[pos : pos + length]):
                if top + y < height:
                    pixels[top + y][x] = color
            pos += length + 1
    return pixels


def resize_patch(patch: list[list[int]], width: int, height: int) -> list[list[int]]:
    source_height = len(patch)
    source_width = len(patch[0]) if source_height else 0
    if source_width == 0 or width <= 0 or height <= 0:
        raise ValueError("cannot resize an empty Doom patch")

    return [
        [
            patch[min(source_height - 1, ((2 * y + 1) * source_height) // (2 * height))]
                 [min(source_width - 1, ((2 * x + 1) * source_width) // (2 * width))]
            for x in range(width)
        ]
        for y in range(height)
    ]


def compact_patch(patch: list[list[int]]) -> list[list[int]]:
    height = len(patch)
    width = len(patch[0]) if height else 0
    return resize_patch(
        patch,
        max(1, (width * 4 + 2) // 5),
        max(1, (height * 4 + 2) // 5),
    )


def text_patch(iwad: Wad, text: str) -> list[list[int]]:
    glyphs: list[list[list[int]] | None] = []
    width = 0
    height = 0
    for char in text.upper():
        if char == " ":
            glyphs.append(None)
            width += 4
            continue
        glyph = decode_patch(iwad.get(f"STCFN{ord(char):03d}"))
        glyphs.append(glyph)
        width += len(glyph[0]) + 1
        height = max(height, len(glyph))

    width = max(1, width - (1 if glyphs and glyphs[-1] is not None else 0))
    canvas = [[-1] * width for _ in range(height)]
    x_offset = 0
    for glyph in glyphs:
        if glyph is None:
            x_offset += 4
            continue
        for y, row in enumerate(glyph):
            canvas[y][x_offset : x_offset + len(row)] = row
        x_offset += len(glyph[0]) + 1
    return canvas


def menu_patch(iwad: Wad, name: str) -> list[list[int]]:
    patch = decode_patch(iwad.get(name))
    if name in COMPACT_PATCHES:
        patch = compact_patch(patch)

    if name == "M_THERML":
        middle = decode_patch(iwad.get("M_THERMM"))
        patch = [
            row + middle[y][:2]
            for y, row in enumerate(patch)
        ]
    elif name == "M_THERMM":
        # Vanilla draws the 9-pixel middle patch every 8 pixels. Cropping the
        # overlapping column preserves that pitch when each FIX cell is 8px.
        patch = [row[:8] for row in patch]
    elif name == "M_THERMO":
        middle = [row[:8] for row in decode_patch(iwad.get("M_THERMM"))]
        knob = patch
        patch = [row[:] for row in middle]
        for y, row in enumerate(knob):
            for x, color in enumerate(row):
                if color >= 0:
                    patch[y][x] = color

    return patch


def intermission_patch(iwad: Wad, name: str) -> list[list[int]]:
    patch = decode_patch(iwad.get(name))
    if name in COMPACT_INTERMISSION_PATCHES:
        patch = resize_patch(patch, 8, len(patch))
    return patch


def encode_fix_tile(pixels: list[int]) -> bytes:
    result = bytearray()
    for xa, xb in ((4, 5), (6, 7), (0, 1), (2, 3)):
        for y in range(8):
            result.append((pixels[y * 8 + xb] << 4) | pixels[y * 8 + xa])
    return bytes(result)


def decode_fix_tile(data: bytes) -> list[int]:
    if len(data) != FIX_TILE_BYTES:
        raise ValueError("Neo Geo FIX tiles must contain 32 bytes")

    pixels = [0] * 64
    pos = 0
    for xa, xb in ((4, 5), (6, 7), (0, 1), (2, 3)):
        for y in range(8):
            value = data[pos]
            pos += 1
            pixels[y * 8 + xa] = value & 0x0F
            pixels[y * 8 + xb] = value >> 4
    return pixels


def encode_sprite_tile(pixels: list[int]) -> tuple[bytes, bytes]:
    if len(pixels) != 256:
        raise ValueError("Neo Geo sprite tiles must be 16x16")

    c1 = bytearray()
    c2 = bytearray()
    for block_x, block_y in ((8, 0), (8, 8), (0, 0), (0, 8)):
        for y in range(8):
            planes = [0, 0, 0, 0]
            for x in range(8):
                color = pixels[(block_y + y) * 16 + block_x + x]
                bit = x
                for plane in range(4):
                    if color & (1 << plane):
                        planes[plane] |= 1 << bit
            c1.extend((planes[0], planes[1]))
            c2.extend((planes[2], planes[3]))
    return bytes(c1), bytes(c2)


def color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return sum((a[channel] - b[channel]) ** 2 for channel in range(3))


def patch_tile_sources(
    patch: list[list[int]],
    tile_size: int = 8,
) -> tuple[int, int, list[list[int]]]:
    height = len(patch)
    width = len(patch[0]) if height else 0
    cols = max(1, (width + tile_size - 1) // tile_size)
    rows = max(1, (height + tile_size - 1) // tile_size)
    tiles: list[list[int]] = []

    for tile_y in range(rows):
        for tile_x in range(cols):
            source = []
            for y in range(tile_size):
                src_y = tile_y * tile_size + y
                for x in range(tile_size):
                    src_x = tile_x * tile_size + x
                    source.append(patch[src_y][src_x] if src_y < height and src_x < width else -1)
            tiles.append(source)
    return cols, rows, tiles


def optimize_palette(
    histogram: Counter[int],
    playpal: list[tuple[int, int, int]],
    distances: list[list[int]],
) -> list[int]:
    colors = list(histogram)
    if not colors:
        return []
    if len(colors) <= 15:
        return sorted(colors, key=lambda color: (-histogram[color], color))

    palette = sorted(colors, key=lambda color: (-histogram[color], color))[:15]
    for _ in range(4):
        clusters: list[list[int]] = [[] for _ in palette]
        for color in colors:
            slot = min(
                range(len(palette)),
                key=lambda index: (distances[color][palette[index]], index),
            )
            clusters[slot].append(color)

        updated: list[int] = []
        for old, cluster in zip(palette, clusters):
            if not cluster:
                updated.append(old)
                continue
            updated.append(min(
                cluster,
                key=lambda candidate: (
                    sum(
                        histogram[color] * distances[color][candidate]
                        for color in cluster
                    ),
                    candidate,
                ),
            ))
        if updated == palette:
            break
        palette = updated
    return palette


def palette_error(
    histogram: Counter[int],
    palette: list[int],
    distances: list[list[int]],
) -> int:
    if not histogram:
        return 0
    if not palette:
        return sum(histogram.values()) * 255 * 255 * 3
    return sum(
        count * min(distances[color][candidate] for candidate in palette)
        for color, count in histogram.items()
    )


def build_palette_set(
    sources: list[list[int]],
    playpal: list[tuple[int, int, int]],
    limit: int,
) -> tuple[list[list[int]], list[list[int]]]:
    distances = [
        [color_distance(source, target) for target in playpal]
        for source in playpal
    ]
    histograms = [Counter(color for color in source if color >= 0) for source in sources]
    combined: Counter[int] = Counter()
    for histogram in histograms:
        combined.update(histogram)

    palettes = [optimize_palette(combined, playpal, distances)]
    candidates = [optimize_palette(histogram, playpal, distances) for histogram in histograms]
    selected = {tuple(palettes[0])}
    best_errors = [palette_error(histogram, palettes[0], distances) for histogram in histograms]

    while len(palettes) < limit:
        candidate_index = None
        for index in sorted(range(len(histograms)), key=best_errors.__getitem__, reverse=True):
            key = tuple(candidates[index])
            if key and key not in selected:
                candidate_index = index
                break
        if candidate_index is None:
            break

        palette = candidates[candidate_index]
        palettes.append(palette)
        selected.add(tuple(palette))
        for index, histogram in enumerate(histograms):
            best_errors[index] = min(
                best_errors[index],
                palette_error(histogram, palette, distances),
            )

    for _ in range(3):
        assignments = [
            min(
                range(len(palettes)),
                key=lambda index: palette_error(histogram, palettes[index], distances),
            )
            for histogram in histograms
        ]
        groups = [Counter() for _ in palettes]
        for histogram, assignment in zip(histograms, assignments):
            groups[assignment].update(histogram)
        updated = [
            optimize_palette(group, playpal, distances) if group else palette
            for group, palette in zip(groups, palettes)
        ]
        if updated == palettes:
            break
        palettes = updated

    return palettes, distances


def quantize_tile(
    source: list[int],
    palettes: list[list[int]],
    distances: list[list[int]],
) -> tuple[int, bytes]:
    histogram = Counter(color for color in source if color >= 0)
    palette_index = min(
        range(len(palettes)),
        key=lambda index: palette_error(histogram, palettes[index], distances),
    )
    palette = palettes[palette_index]
    output = [
        0 if color < 0 else 1 + min(
            range(len(palette)),
            key=lambda index: distances[color][palette[index]],
        )
        for color in source
    ]
    return palette_index, encode_fix_tile(output)


def neo_color_components(color: int) -> tuple[int, int, int]:
    return (
        ((color >> 14) & 1) | ((color >> 7) & 0x1E),
        ((color >> 13) & 1) | ((color >> 3) & 0x1E),
        ((color >> 12) & 1) | ((color & 0x0F) << 1),
    )


def embedded_fix_palettes(
    embedded_playpal: list[int],
    playpal: list[tuple[int, int, int]],
) -> list[list[int]]:
    """Map the compact WAD's FIX palettes back to nearest PLAYPAL indices."""
    playpal_components = [
        tuple(component // 8 for component in color)
        for color in playpal
    ]

    def nearest_playpal(color: int) -> int:
        components = neo_color_components(color)
        return min(
            range(256),
            key=lambda index: sum(
                (components[channel] - playpal_components[index][channel]) ** 2
                for channel in range(3)
            ),
        )

    return [
        [
            nearest_playpal(embedded_playpal[palette * 16 + pen])
            for pen in range(1, 16)
        ]
        for palette in range(16)
    ]


def wipe_zero_pen_map(playpal: list[int]) -> list[int]:
    components = [neo_color_components(color) for color in playpal]
    opaque_colors = [color for color in range(256) if color & 0x0F]
    return [
        min(
            opaque_colors,
            key=lambda candidate: sum(
                (components[color][channel] - components[candidate][channel]) ** 2
                for channel in range(3)
            ),
        )
        for color in range(0, 256, 16)
    ]


def compact_fix_map(embedded_wad: Wad, lump_name: str) -> list[int]:
    tilemap = embedded_wad.get(lump_name)
    if len(tilemap) != BACKGROUND_TILE_COUNT * 2:
        raise ValueError(
            f"unexpected compact {lump_name} size: {len(tilemap)}"
        )

    entries = struct.unpack(f">{BACKGROUND_TILE_COUNT}H", tilemap)
    padded: list[int] = []
    for row in range(BACKGROUND_ROWS):
        start = row * BACKGROUND_COLUMNS
        padded.extend((0x0020, *entries[start : start + BACKGROUND_COLUMNS], 0x0020))
    return padded


def compact_solid_wipe_map(
    embedded_wad: Wad,
    lump_name: str,
    srom: bytes,
    zero_pen_map: list[int],
) -> list[int]:
    tilemap = embedded_wad.get(lump_name)
    if len(tilemap) != BACKGROUND_TILE_COUNT * 2:
        raise ValueError(
            f"unexpected compact {lump_name} size: {len(tilemap)}"
        )

    entries = struct.unpack(f">{BACKGROUND_TILE_COUNT}H", tilemap)
    wipe_entries: list[int] = []
    for entry in entries:
        palette = entry >> 12
        tile = entry & 0x0FFF
        pixels = decode_fix_tile(
            srom[tile * FIX_TILE_BYTES : (tile + 1) * FIX_TILE_BYTES]
        )
        histogram = Counter(pixels)
        pen = max(range(16), key=lambda value: (histogram[value], -value))
        color = (palette << 4) | pen
        if pen == 0:
            color = zero_pen_map[palette]
        wipe_entries.append(color << 8)

    padded: list[int] = []
    for row in range(BACKGROUND_ROWS):
        start = row * BACKGROUND_COLUMNS
        padded.extend((0x0020, *wipe_entries[start : start + BACKGROUND_COLUMNS], 0x0020))
    return padded


def c_name(name: str) -> str:
    return name.lower().replace("m_", "doom_menu_")


def intermission_c_name(name: str) -> str:
    return f"doom_intermission_{name.lower()}"


def write_patch_header(
    output: Path,
    guard: str,
    definitions: list[str],
    patches: list[tuple[str, int, int, list[int]]],
) -> None:
    lines = [
        "/* Generated by tools/gen_neogeo_fix_menu.py. */",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <stdint.h>",
        "",
        *definitions,
        "",
    ]

    for name, cols, rows, entries in patches:
        lines.extend(
            [
                f"#define {name.upper()}_COLS {cols}u",
                f"#define {name.upper()}_ROWS {rows}u",
                f"static const uint16_t {name}[{len(entries)}] = {{",
            ]
        )
        for offset in range(0, len(entries), 12):
            values = ", ".join(
                f"0x{entry:04x}" for entry in entries[offset : offset + 12]
            )
            lines.append(f"    {values},")
        lines.extend(["};", ""])

    lines.extend(["#endif", ""])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="ascii")


def write_menu_assets(
    iwad: Wad,
    embedded_wad: Wad,
    base_srom_path: Path,
    output_fix: Path,
    output_header: Path,
    output_intermission_header: Path,
    output_palette_header: Path,
    output_wipe_header: Path,
) -> None:
    playpal_data = iwad.get("PLAYPAL")
    playpal = [tuple(playpal_data[index : index + 3]) for index in range(0, 768, 3)]
    embedded_playpal_data = embedded_wad.get("PLAYPAL")
    if len(embedded_playpal_data) < 512:
        raise ValueError("compact PLAYPAL does not contain a complete base palette")
    embedded_playpal = list(struct.unpack_from(">256H", embedded_playpal_data))
    zero_pen_map = wipe_zero_pen_map(embedded_playpal)
    blank = bytes(FIX_TILE_BYTES)
    unique_tiles: list[tuple[int, bytes]] = []
    tile_ids: dict[bytes, int] = {}
    menu_patches: list[tuple[str, int, int, list[int]]] = []
    intermission_patches: list[tuple[str, int, int, list[int]]] = []

    menu_sources: list[tuple[str, int, int, list[list[int]]]] = []
    palette_sources: list[list[int]] = []
    for name in PATCH_NAMES:
        cols, rows, sources = patch_tile_sources(menu_patch(iwad, name))
        menu_sources.append((c_name(name), cols, rows, sources))
        palette_sources.extend(sources)

    for name, text in TEXT_PATCHES:
        cols, rows, sources = patch_tile_sources(text_patch(iwad, text))
        menu_sources.append((name, cols, rows, sources))
        palette_sources.extend(sources)

    wi_sources: list[tuple[str, int, int, list[list[int]]]] = []
    for name in INTERMISSION_PATCH_NAMES:
        cols, rows, sources = patch_tile_sources(intermission_patch(iwad, name))
        wi_sources.append((intermission_c_name(name), cols, rows, sources))

    palettes, distances = build_palette_set(palette_sources, playpal, 16)
    wi_palettes = embedded_fix_palettes(embedded_playpal, playpal)
    base = base_srom_path.read_bytes()
    title_wipe_map = compact_fix_map(embedded_wad, "TITLEPIC")
    wimap_wipe_map = compact_fix_map(embedded_wad, "WIMAP0")
    help_wipe_map = compact_solid_wipe_map(
        embedded_wad,
        "HELP2",
        base,
        zero_pen_map,
    )

    def next_tile_id() -> int:
        candidate = FIX_UI_TILE_BASE + len(unique_tiles)
        while candidate in WIPE_TILE_IDS:
            candidate += 1
        if unique_tiles:
            candidate = unique_tiles[-1][0] + 1
            while candidate in WIPE_TILE_IDS:
                candidate += 1
        return candidate

    def tile_id(tile: bytes) -> int:
        if tile == blank:
            return 0
        if tile not in tile_ids:
            tile_ids[tile] = next_tile_id()
            unique_tiles.append((tile_ids[tile], tile))
        return tile_ids[tile]

    for name, cols, rows, sources in menu_sources:
        tiles = [quantize_tile(source, palettes, distances) for source in sources]
        entries = [(palette << 12) | tile_id(tile) for palette, tile in tiles]
        menu_patches.append((name, cols, rows, entries))
    menu_tile_count = len(unique_tiles)

    for name, cols, rows, sources in wi_sources:
        tiles = [quantize_tile(source, wi_palettes, distances) for source in sources]
        entries = [(palette << 12) | tile_id(tile) for palette, tile in tiles]
        intermission_patches.append((name, cols, rows, entries))

    if unique_tiles and unique_tiles[-1][0] > FIX_UI_TILE_END:
        raise ValueError(
            f"FIX UI needs {len(unique_tiles)} unique tiles at {FIX_UI_TILE_BASE}, "
            f"but exceeds the reclaimed HELP2 range ending at {FIX_UI_TILE_END}"
        )

    output_fix.parent.mkdir(parents=True, exist_ok=True)
    last_tile = unique_tiles[-1][0]
    overlay = bytearray(
        base[FIX_UI_TILE_BASE * FIX_TILE_BYTES : (last_tile + 1) * FIX_TILE_BYTES]
    )
    for tile, data in unique_tiles:
        offset = (tile - FIX_UI_TILE_BASE) * FIX_TILE_BYTES
        overlay[offset : offset + FIX_TILE_BYTES] = data
    output_fix.write_bytes(overlay)

    write_patch_header(
        output_header,
        "DOOM_NEO_GEO_FIX_MENU_ASSETS_H",
        [
            f"#define DOOM_MENU_FIX_TILE_BASE {FIX_UI_TILE_BASE}u",
            f"#define DOOM_MENU_FIX_TILE_COUNT {menu_tile_count}u",
        ],
        menu_patches,
    )
    write_patch_header(
        output_intermission_header,
        "DOOM_NEO_GEO_FIX_INTERMISSION_ASSETS_H",
        [
            f"#define DOOM_INTERMISSION_FIX_TILE_BASE {FIX_UI_TILE_BASE}u",
            "#define DOOM_INTERMISSION_FIX_TILE_COUNT "
            f"{len(unique_tiles) - menu_tile_count}u",
        ],
        intermission_patches,
    )

    palette_lines = [
        "/* Generated by tools/gen_neogeo_fix_menu.py. */",
        "#ifndef DOOM_NEO_GEO_FIX_MENU_PALETTE_H",
        "#define DOOM_NEO_GEO_FIX_MENU_PALETTE_H",
        "",
        "#include <stdint.h>",
        "",
        "static const uint16_t doom_menu_palettes[16][16] = {",
    ]
    for palette in palettes:
        values = [0x8000]
        values.extend(neo_color(playpal[color]) for color in palette)
        values.extend([0x8000] * (16 - len(values)))
        palette_lines.append(
            "    {" + ", ".join(f"0x{value:04x}" for value in values) + "},"
        )
    for _ in range(16 - len(palettes)):
        palette_lines.append("    {" + ", ".join(["0x8000"] * 16) + "},")
    palette_lines.extend(["};", "", "#endif", ""])
    output_palette_header.parent.mkdir(parents=True, exist_ok=True)
    output_palette_header.write_text("\n".join(palette_lines), encoding="ascii")

    wipe_lines = [
        "/* Generated by tools/gen_neogeo_fix_menu.py. */",
        "#ifndef DOOM_NEO_GEO_FIX_WIPE_ASSETS_H",
        "#define DOOM_NEO_GEO_FIX_WIPE_ASSETS_H",
        "",
        "#include <stdint.h>",
        "",
        "static const uint8_t doom_fix_wipe_zero_pen_map[16] = {",
        "    " + ", ".join(f"{color}u" for color in zero_pen_map) + ",",
        "};",
        "",
    ]
    for name, values in (
        ("doom_title_wipe_map", title_wipe_map),
        ("doom_wimap_wipe_map", wimap_wipe_map),
        ("doom_help_wipe_map", help_wipe_map),
    ):
        wipe_lines.append(f"static const uint16_t {name}[{len(values)}] = {{")
        for offset in range(0, len(values), 12):
            encoded = ", ".join(
                f"0x{entry:04x}" for entry in values[offset : offset + 12]
            )
            wipe_lines.append(f"    {encoded},")
        wipe_lines.extend(["};", ""])
    wipe_lines.extend(["#endif", ""])
    output_wipe_header.parent.mkdir(parents=True, exist_ok=True)
    output_wipe_header.write_text("\n".join(wipe_lines), encoding="ascii")

    print(
        f"FIX UI: {len(unique_tiles)} unique tiles, "
        f"IDs {FIX_UI_TILE_BASE}..{last_tile} (wipe tiles preserved)"
    )

def build_background_crom(
    embedded_wad: Wad,
    lump_name: str,
    srom: bytes,
) -> tuple[bytes, bytes, list[int]]:
    tilemap = embedded_wad.get(lump_name)
    if len(tilemap) != BACKGROUND_TILE_COUNT * 2:
        raise ValueError(
            f"unexpected compact {lump_name} size: {len(tilemap)}"
        )

    c1 = bytearray()
    c2 = bytearray()
    palette_map: list[int] = []
    for offset in range(0, len(tilemap), 2):
        entry = struct.unpack_from(">H", tilemap, offset)[0]
        tile = entry & 0x0FFF
        tile_offset = tile * FIX_TILE_BYTES
        pixels = decode_fix_tile(srom[tile_offset : tile_offset + FIX_TILE_BYTES])
        scaled = [
            pixels[(y // 2) * 8 + (x // 2)]
            for y in range(16)
            for x in range(16)
        ]
        tile_c1, tile_c2 = encode_sprite_tile(scaled)
        c1.extend(tile_c1)
        c2.extend(tile_c2)
        palette_map.append(entry >> 12)

    return bytes(c1), bytes(c2), palette_map


def write_title_crom(
    embedded_wad: Wad,
    base_srom_path: Path,
    output_c1: Path,
    output_c2: Path,
    output_header: Path,
) -> None:
    srom = base_srom_path.read_bytes()
    title_c1, title_c2, title_palette_map = build_background_crom(
        embedded_wad,
        "TITLEPIC",
        srom,
    )
    wimap_c1, wimap_c2, wimap_palette_map = build_background_crom(
        embedded_wad,
        "WIMAP0",
        srom,
    )
    help_c1, help_c2, help_palette_map = build_background_crom(
        embedded_wad,
        "HELP2",
        srom,
    )
    playpal_data = embedded_wad.get("PLAYPAL")
    if len(playpal_data) < 512:
        raise ValueError("compact PLAYPAL does not contain a complete base palette")
    palettes = [
        list(struct.unpack_from(">16H", playpal_data, palette * 32))
        for palette in range(16)
    ]

    output_c1.parent.mkdir(parents=True, exist_ok=True)
    output_c2.parent.mkdir(parents=True, exist_ok=True)
    output_c1.write_bytes(title_c1 + wimap_c1 + help_c1)
    output_c2.write_bytes(title_c2 + wimap_c2 + help_c2)

    lines = [
        "/* Generated by tools/gen_neogeo_fix_menu.py. */",
        "#ifndef DOOM_NEO_GEO_TITLE_ASSETS_H",
        "#define DOOM_NEO_GEO_TITLE_ASSETS_H",
        "",
        "#include <stdint.h>",
        "",
        f"#define DOOM_TITLE_TILE_BASE {TITLE_CROM_TILE_BASE}u",
        f"#define DOOM_WIMAP_TILE_BASE {WIMAP_CROM_TILE_BASE}u",
        f"#define DOOM_HELP_TILE_BASE {HELP_CROM_TILE_BASE}u",
        f"#define DOOM_BACKGROUND_COLUMNS {BACKGROUND_COLUMNS}u",
        f"#define DOOM_BACKGROUND_ROWS {BACKGROUND_ROWS}u",
        "#define DOOM_TITLE_PALETTE_COUNT 16u",
        "#define DOOM_WIMAP_PALETTE_COUNT 16u",
        "#define DOOM_HELP_PALETTE_COUNT 16u",
        "",
        "static const uint16_t doom_title_palettes[16][16] = {",
    ]
    for palette in palettes:
        lines.append(
            "    {" + ", ".join(f"0x{value:04x}" for value in palette) + "},"
        )
    lines.extend([
        "};",
        "#define doom_wimap_palettes doom_title_palettes",
        "#define doom_help_palettes doom_title_palettes",
        "",
        f"static const uint8_t doom_title_palette_map[{len(title_palette_map)}] = {{",
    ])
    for offset in range(0, len(title_palette_map), 20):
        values = ", ".join(
            f"{value}u" for value in title_palette_map[offset : offset + 20]
        )
        lines.append("    " + values + ",")
    lines.extend([
        "};",
        "",
        f"static const uint8_t doom_wimap_palette_map[{len(wimap_palette_map)}] = {{",
    ])
    for offset in range(0, len(wimap_palette_map), 20):
        values = ", ".join(
            f"{value}u" for value in wimap_palette_map[offset : offset + 20]
        )
        lines.append("    " + values + ",")
    lines.extend([
        "};",
        "",
        f"static const uint8_t doom_help_palette_map[{len(help_palette_map)}] = {{",
    ])
    for offset in range(0, len(help_palette_map), 20):
        values = ", ".join(
            f"{value}u" for value in help_palette_map[offset : offset + 20]
        )
        lines.append("    " + values + ",")
    lines.extend(["};", "", "#endif", ""])
    output_header.parent.mkdir(parents=True, exist_ok=True)
    output_header.write_text("\n".join(lines), encoding="ascii")

    print(
        f"background C-ROM: TITLEPIC at tile {TITLE_CROM_TILE_BASE} "
        f"(16 palettes), WIMAP0 at tile {WIMAP_CROM_TILE_BASE} "
        f"(16 palettes), HELP2 at tile {HELP_CROM_TILE_BASE} (16 palettes), "
        f"{BACKGROUND_COLUMNS}x{BACKGROUND_ROWS} tiles each"
    )


def parse_embedded_wad(header: Path) -> Wad:
    source = header.read_text(encoding="ascii")
    values = bytes(
        int(value, 16)
        for value in re.findall(r"0x([0-9a-fA-F]{2})", source)
    )
    return Wad(values, endian=">")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iwad", type=Path, required=True)
    parser.add_argument("--embedded-wad-header", type=Path, required=True)
    parser.add_argument("--base-srom", type=Path, required=True)
    parser.add_argument("--out-fix", type=Path, required=True)
    parser.add_argument("--out-menu-header", type=Path, required=True)
    parser.add_argument("--out-intermission-header", type=Path, required=True)
    parser.add_argument("--out-menu-palette-header", type=Path, required=True)
    parser.add_argument("--out-wipe-header", type=Path, required=True)
    parser.add_argument("--out-title-c1", type=Path, required=True)
    parser.add_argument("--out-title-c2", type=Path, required=True)
    parser.add_argument("--out-title-header", type=Path, required=True)
    args = parser.parse_args()

    embedded_wad = parse_embedded_wad(args.embedded_wad_header)
    write_menu_assets(
        Wad(args.iwad.read_bytes()),
        embedded_wad,
        args.base_srom,
        args.out_fix,
        args.out_menu_header,
        args.out_intermission_header,
        args.out_menu_palette_header,
        args.out_wipe_header,
    )
    write_title_crom(
        embedded_wad,
        args.base_srom,
        args.out_title_c1,
        args.out_title_c2,
        args.out_title_header,
    )


if __name__ == "__main__":
    main()
