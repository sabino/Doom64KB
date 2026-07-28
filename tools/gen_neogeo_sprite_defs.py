#!/usr/bin/env python3
"""Generate ROM-resident sprite definitions for the Neo Geo build."""

from __future__ import annotations

import argparse
import hashlib
import re
import struct
from dataclasses import dataclass
from pathlib import Path


MAX_SPRITE_FRAMES = 29


@dataclass(frozen=True)
class WadLump:
    index: int
    raw_name: bytes
    offset: int
    size: int

    @property
    def name(self) -> str:
        return self.raw_name.rstrip(b"\0").decode("ascii", "strict")


@dataclass(frozen=True)
class Wad:
    data: bytes
    lumps: tuple[WadLump, ...]

    def index(self, name: str) -> int:
        wanted = name.encode("ascii").ljust(8, b"\0")
        return next(lump.index for lump in self.lumps if lump.raw_name == wanted)


@dataclass(frozen=True)
class SpriteFrame:
    lumps: tuple[int, ...]
    flipmask: int
    rotate: int


@dataclass(frozen=True)
class SpriteDefinition:
    name: str
    frames: tuple[SpriteFrame, ...]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_directory(
    path: Path,
    data: bytes,
    count: int,
    directory_offset: int,
    entry_size: int,
) -> None:
    if count > 1_000_000:
        raise ValueError(f"{path}: unreasonable lump count {count}")
    directory_end = directory_offset + count * entry_size
    if directory_offset < 12 or directory_end > len(data):
        raise ValueError(f"{path}: WAD directory lies outside the file")


def read_standard_wad(path: Path) -> Wad:
    data = path.read_bytes()
    if len(data) < 12:
        raise ValueError(f"{path}: file is too small to be a WAD")

    ident, count, directory_offset = struct.unpack_from("<4sII", data)
    if ident not in (b"IWAD", b"PWAD"):
        raise ValueError(f"{path}: not a Doom WAD")
    _validate_directory(path, data, count, directory_offset, 16)

    lumps = []
    for index in range(count):
        entry = directory_offset + index * 16
        offset, size, raw_name = struct.unpack_from("<II8s", data, entry)
        if offset > len(data) or size > len(data) - offset:
            raise ValueError(f"{path}: lump {index} lies outside the file")
        raw_name.rstrip(b"\0").decode("ascii", "strict")
        lumps.append(WadLump(index, raw_name, offset, size))
    return Wad(data, tuple(lumps))


def read_embedded_wad_header(path: Path) -> Wad:
    source = path.read_text(encoding="ascii")
    declared_size_match = re.search(
        r"doom_iwad\s*\[\s*(\d+)\s*\]\s*=\s*\{", source
    )
    if not declared_size_match:
        raise ValueError(f"{path}: cannot find the doom_iwad declaration")

    data = bytes(
        int(value, 16)
        for value in re.findall(r"0x([0-9a-fA-F]{2})", source)
    )
    declared_size = int(declared_size_match.group(1))
    if len(data) != declared_size:
        raise ValueError(
            f"{path}: declaration says {declared_size} bytes, found {len(data)}"
        )
    if len(data) < 12:
        raise ValueError(f"{path}: embedded WAD is too small")

    ident, count, filler, directory_offset = struct.unpack_from(">4sHHI", data)
    if ident not in (b"IWAD", b"PWAD") or filler != 0:
        raise ValueError(f"{path}: not a converted big-endian Doom WAD")
    _validate_directory(path, data, count, directory_offset, 16)

    lumps = []
    for index in range(count):
        entry = directory_offset + index * 16
        offset, size, entry_filler, raw_name = struct.unpack_from(
            ">IHH8s", data, entry
        )
        if entry_filler != 0:
            raise ValueError(f"{path}: lump {index} has nonzero filler")
        if offset > len(data) or size > len(data) - offset:
            raise ValueError(f"{path}: lump {index} lies outside the embedded WAD")
        raw_name.rstrip(b"\0").decode("ascii", "strict")
        lumps.append(WadLump(index, raw_name, offset, size))
    return Wad(data, tuple(lumps))


def read_sprite_names(path: Path) -> tuple[str, ...]:
    source = path.read_text(encoding="ascii")
    match = re.search(
        r"const\s+char\s*\*\s*const\s+sprnames\s*"
        r"\[[^\]]+\]\s*=\s*\{(?P<body>.*?)\};",
        source,
        re.DOTALL,
    )
    if not match:
        raise ValueError(f"{path}: cannot find the sprnames initializer")

    names = tuple(re.findall(r'"([^"]+)"', match.group("body")))
    if not names:
        raise ValueError(f"{path}: sprnames is empty")
    if len(names) != len(set(names)):
        raise ValueError(f"{path}: sprnames contains duplicates")
    for name in names:
        if len(name) != 4 or not name.isascii() or name.upper() != name:
            raise ValueError(f"{path}: invalid sprite name {name!r}")
    return names


def sprite_lumps_from_exact_wad(
    iwad: Wad,
    embedded_wad: Wad,
    sprite_names: tuple[str, ...],
) -> tuple[WadLump, ...]:
    source_start = iwad.index("S_START") + 1
    source_end = iwad.index("S_END")
    embedded_start = embedded_wad.index("S_START") + 1
    embedded_end = embedded_wad.index("S_END")

    prefixes = {name.encode("ascii") for name in sprite_names}
    source_names = [
        lump.raw_name
        for lump in iwad.lumps[source_start:source_end]
        if lump.raw_name[:4] in prefixes
    ]
    embedded_lumps = embedded_wad.lumps[embedded_start:embedded_end]
    embedded_names = [lump.raw_name for lump in embedded_lumps]

    if source_names != embedded_names:
        mismatch = next(
            (
                index
                for index, (source, embedded) in enumerate(
                    zip(source_names, embedded_names)
                )
                if source != embedded
            ),
            min(len(source_names), len(embedded_names)),
        )
        source_name = (
            source_names[mismatch].rstrip(b"\0").decode("ascii")
            if mismatch < len(source_names)
            else "<end>"
        )
        embedded_name = (
            embedded_names[mismatch].rstrip(b"\0").decode("ascii")
            if mismatch < len(embedded_names)
            else "<end>"
        )
        raise ValueError(
            "embedded sprite namespace does not match the exact IWAD filter "
            f"at entry {mismatch}: IWAD={source_name}, embedded={embedded_name}"
        )

    return embedded_lumps


def sprite_name_hash(raw_name: bytes) -> int:
    value = raw_name[0] - (
        (raw_name[1] * 3 - raw_name[3] * 2 - raw_name[2]) * 2
    )
    return value & 0xFFFF


def _install_sprite_lump(
    frames: list[dict[str, object]],
    lump: int,
    frame: int,
    rotation: int,
    flipped: bool,
    maxframe: int,
) -> int:
    if frame >= MAX_SPRITE_FRAMES or rotation > 8:
        raise ValueError(
            f"sprite lump {lump} has invalid frame {frame} or rotation {rotation}"
        )

    maxframe = max(maxframe, frame)
    target = frames[frame]
    lumps = target["lumps"]
    assert isinstance(lumps, list)

    if rotation == 0:
        target["flipmask"] = 0
        for index in range(8):
            if lumps[index] == -1:
                lumps[index] = lump
                if flipped:
                    target["flipmask"] = int(target["flipmask"]) | (1 << index)
                target["rotate"] = 0
        return maxframe

    rotation -= 1
    if lumps[rotation] == -1:
        lumps[rotation] = lump
        if flipped:
            target["flipmask"] = int(target["flipmask"]) | (1 << rotation)
        else:
            target["flipmask"] = int(target["flipmask"]) & ~(1 << rotation)
        target["flipmask"] = int(target["flipmask"]) & 0xFF
        target["rotate"] = 1
    return maxframe


def build_sprite_definitions(
    sprite_names: tuple[str, ...],
    sprite_lumps: tuple[WadLump, ...],
) -> tuple[SpriteDefinition, ...]:
    entry_count = len(sprite_lumps)
    if not entry_count:
        raise ValueError("embedded WAD has no sprite lumps")

    hash_heads = [-1] * entry_count
    hash_next = [-1] * entry_count
    for index, lump in enumerate(sprite_lumps):
        bucket = sprite_name_hash(lump.raw_name) % entry_count
        hash_next[index] = hash_heads[bucket]
        hash_heads[bucket] = index

    definitions = []
    for sprite_name in sprite_names:
        frames: list[dict[str, object]] = [
            {"lumps": [-1] * 8, "flipmask": 0xFF, "rotate": -1}
            for _ in range(MAX_SPRITE_FRAMES)
        ]
        sprite_bytes = sprite_name.encode("ascii")
        index = hash_heads[sprite_name_hash(sprite_bytes) % entry_count]
        maxframe = -1

        while index >= 0:
            lump = sprite_lumps[index]
            raw_name = lump.raw_name
            if raw_name[:4] == sprite_bytes:
                maxframe = _install_sprite_lump(
                    frames,
                    lump.index,
                    (raw_name[4] - ord("A")) & 0xFF,
                    (raw_name[5] - ord("0")) & 0xFF,
                    False,
                    maxframe,
                )
                if raw_name[6]:
                    maxframe = _install_sprite_lump(
                        frames,
                        lump.index,
                        (raw_name[6] - ord("A")) & 0xFF,
                        (raw_name[7] - ord("0")) & 0xFF,
                        True,
                        maxframe,
                    )
            index = hash_next[index]

        output_frames = []
        for frame_index in range(maxframe + 1):
            frame = frames[frame_index]
            rotate = int(frame["rotate"])
            lumps = tuple(int(value) for value in frame["lumps"])
            if rotate not in (0, 1):
                raise ValueError(
                    f"sprite {sprite_name} frame {chr(ord('A') + frame_index)} "
                    "has no patches"
                )
            if rotate == 1 and -1 in lumps:
                raise ValueError(
                    f"sprite {sprite_name} frame {chr(ord('A') + frame_index)} "
                    "is missing rotations"
                )
            output_frames.append(
                SpriteFrame(lumps, int(frame["flipmask"]), rotate)
            )
        definitions.append(SpriteDefinition(sprite_name, tuple(output_frames)))

    return tuple(definitions)


def generate_header(
    iwad: Wad,
    embedded_wad: Wad,
    sprite_lumps: tuple[WadLump, ...],
    definitions: tuple[SpriteDefinition, ...],
) -> str:
    frame_count = sum(len(definition.frames) for definition in definitions)
    lines = [
        "/* Generated by tools/gen_neogeo_sprite_defs.py. */",
        f"/* IWAD SHA-256: {sha256(iwad.data)} */",
        f"/* Embedded WAD SHA-256: {sha256(embedded_wad.data)} */",
        "#ifndef DOOM_NEOGEO_SPRITE_DEFS_H",
        "#define DOOM_NEOGEO_SPRITE_DEFS_H",
        "",
        f'#define DOOM_SPRITE_DEFS_IWAD_SHA256 "{sha256(iwad.data)}"',
        (
            '#define DOOM_SPRITE_DEFS_EMBEDDED_WAD_SHA256 '
            f'"{sha256(embedded_wad.data)}"'
        ),
        f"#define DOOM_SPRITE_DEFINITION_COUNT {len(definitions)}",
        f"#define DOOM_SPRITE_FRAME_COUNT {frame_count}",
        f"#define DOOM_SPRITE_LUMP_COUNT {len(sprite_lumps)}",
        "",
        "_Static_assert(NUMSPRITES == DOOM_SPRITE_DEFINITION_COUNT,",
        '               "generated sprite definitions do not match info.c");',
        "",
        "#if defined __NGDEVKIT__",
        '#define DOOM_SPRITE_ROM __attribute__((section(".text2")))',
        "#else",
        "#define DOOM_SPRITE_ROM",
        "#endif",
        "",
        "DOOM_SPRITE_ROM",
        (
            "static const spriteframe_t "
            "doom_sprite_frames[DOOM_SPRITE_FRAME_COUNT] ="
        ),
        "{",
    ]

    global_frame = 0
    offsets = []
    for definition in definitions:
        offsets.append(global_frame)
        for frame_index, frame in enumerate(definition.frames):
            lump_values = ", ".join(str(value) for value in frame.lumps)
            frame_name = chr(ord("A") + frame_index)
            lines.append(
                f"    {{ {{ {lump_values} }}, 0x{frame.flipmask:02x}, "
                f"{frame.rotate} }}, "
                f"/* {global_frame}: {definition.name} {frame_name} */"
            )
            global_frame += 1
    lines.extend(
        [
            "};",
            "",
            "DOOM_SPRITE_ROM",
            (
                "static const spritedef_t "
                "doom_sprite_definitions[DOOM_SPRITE_DEFINITION_COUNT] ="
            ),
            "{",
        ]
    )

    for index, (definition, offset) in enumerate(zip(definitions, offsets)):
        if definition.frames:
            frame_pointer = (
                "(spriteframe_t __far *)(doom_sprite_frames "
                f"+ {offset})"
            )
        else:
            frame_pointer = "(spriteframe_t __far *)0"
        lines.append(
            f"    {{ {frame_pointer} }}, "
            f"/* {index}: {definition.name}, {len(definition.frames)} frames */"
        )

    lines.extend(
        [
            "};",
            "",
            "#undef DOOM_SPRITE_ROM",
            "#endif",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iwad", required=True, type=Path)
    parser.add_argument("--embedded-wad-header", required=True, type=Path)
    parser.add_argument("--sprite-source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    iwad = read_standard_wad(args.iwad)
    embedded_wad = read_embedded_wad_header(args.embedded_wad_header)
    sprite_names = read_sprite_names(args.sprite_source)
    sprite_lumps = sprite_lumps_from_exact_wad(
        iwad, embedded_wad, sprite_names
    )
    definitions = build_sprite_definitions(sprite_names, sprite_lumps)
    output = generate_header(iwad, embedded_wad, sprite_lumps, definitions)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="ascii")

    print(
        f"generated {len(definitions)} sprite definitions, "
        f"{sum(len(definition.frames) for definition in definitions)} frames, "
        f"and {len(sprite_lumps)} ROM lump mappings "
        f"(IWAD sha256 {sha256(iwad.data)})"
    )


if __name__ == "__main__":
    main()
