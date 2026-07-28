#!/usr/bin/env python3
"""Verify generated Neo Geo sprite tables against the runtime algorithm."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True

from gen_neogeo_sprite_defs import (
    MAX_SPRITE_FRAMES,
    WadLump,
    read_embedded_wad_header,
    read_sprite_names,
    read_standard_wad,
    sha256,
    sprite_lumps_from_exact_wad,
)


@dataclass(frozen=True)
class ReferenceFrame:
    lumps: tuple[int, ...]
    flipmask: int
    rotate: int


@dataclass(frozen=True)
class GeneratedFrame:
    name: str
    frame: str
    lumps: tuple[int, ...]
    flipmask: int
    rotate: int


@dataclass(frozen=True)
class GeneratedDefinition:
    index: int
    name: str
    frame_count: int
    frame_offset: int | None


FRAME_PATTERN = re.compile(
    r"^\s*\{\s*\{\s*(?P<lumps>[^}]*)\s*\},\s*"
    r"(?P<flipmask>0x[0-9a-fA-F]+),\s*(?P<rotate>[01])\s*\},\s*"
    r"/\*\s*(?P<index>\d+):\s*(?P<name>[A-Z0-9]{4})\s+"
    r"(?P<frame>[A-Z])\s*\*/$"
)
DEFINITION_PATTERN = re.compile(
    r"^\s*\{\s*"
    r"(?:(?:\(spriteframe_t __far \*\)\(doom_sprite_frames \+ "
    r"(?P<offset>\d+)\))|(?:\(spriteframe_t __far \*\)0))\s*\},\s*"
    r"/\*\s*(?P<index>\d+):\s*(?P<name>[A-Z0-9]{4}),\s*"
    r"(?P<count>\d+)\s+frames\s*\*/$"
)


def install_reference_lump(
    frames: list[dict[str, object]],
    lump: WadLump,
    frame: int,
    rotation: int,
    flipped: bool,
    maxframe: int,
) -> int:
    if frame >= MAX_SPRITE_FRAMES or rotation > 8:
        raise ValueError(
            f"sprite lump {lump.index} has invalid frame {frame} "
            f"or rotation {rotation}"
        )

    maxframe = max(maxframe, frame)
    target = frames[frame]
    lumps = target["lumps"]
    assert isinstance(lumps, list)

    if rotation == 0:
        target["flipmask"] = 0
        for index, old_lump in enumerate(lumps):
            if old_lump == -1:
                lumps[index] = lump.index
                if flipped:
                    target["flipmask"] = int(target["flipmask"]) | (1 << index)
                target["rotate"] = 0
        return maxframe

    rotation -= 1
    if lumps[rotation] == -1:
        lumps[rotation] = lump.index
        bit = 1 << rotation
        if flipped:
            target["flipmask"] = int(target["flipmask"]) | bit
        else:
            target["flipmask"] = int(target["flipmask"]) & ~bit
        target["flipmask"] = int(target["flipmask"]) & 0xFF
        target["rotate"] = 1
    return maxframe


def build_reference_definition(
    sprite_name: str,
    sprite_lumps: tuple[WadLump, ...],
) -> tuple[ReferenceFrame, ...]:
    frames: list[dict[str, object]] = [
        {"lumps": [-1] * 8, "flipmask": 0xFF, "rotate": -1}
        for _ in range(MAX_SPRITE_FRAMES)
    ]
    prefix = sprite_name.encode("ascii")
    maxframe = -1

    # R_InitSprites prepends each lump to its hash chain, so matching lumps
    # are visited in reverse directory order. Scanning directly in that order
    # is an independent equivalent of the runtime hash-table traversal.
    for lump in reversed(sprite_lumps):
        raw_name = lump.raw_name
        if raw_name[:4] != prefix:
            continue
        maxframe = install_reference_lump(
            frames,
            lump,
            (raw_name[4] - ord("A")) & 0xFF,
            (raw_name[5] - ord("0")) & 0xFF,
            False,
            maxframe,
        )
        if raw_name[6]:
            maxframe = install_reference_lump(
                frames,
                lump,
                (raw_name[6] - ord("A")) & 0xFF,
                (raw_name[7] - ord("0")) & 0xFF,
                True,
                maxframe,
            )

    result = []
    for frame_index in range(maxframe + 1):
        frame = frames[frame_index]
        lumps = tuple(int(value) for value in frame["lumps"])
        rotate = int(frame["rotate"])
        if rotate not in (0, 1):
            raise ValueError(
                f"sprite {sprite_name} frame "
                f"{chr(ord('A') + frame_index)} has no patches"
            )
        if rotate == 1 and -1 in lumps:
            raise ValueError(
                f"sprite {sprite_name} frame "
                f"{chr(ord('A') + frame_index)} is missing rotations"
            )
        result.append(ReferenceFrame(lumps, int(frame["flipmask"]), rotate))
    return tuple(result)


def macro_value(source: str, name: str) -> str:
    match = re.search(rf"^#define {re.escape(name)} (.+)$", source, re.MULTILINE)
    if not match:
        raise ValueError(f"generated header is missing {name}")
    return match.group(1).strip().strip('"')


def parse_generated_header(
    path: Path,
) -> tuple[
    str,
    str,
    int,
    int,
    int,
    tuple[GeneratedFrame, ...],
    tuple[GeneratedDefinition, ...],
]:
    source = path.read_text(encoding="ascii")
    frames = []
    definitions = []

    for line in source.splitlines():
        frame_match = FRAME_PATTERN.match(line)
        if frame_match:
            lumps = tuple(
                int(value.strip())
                for value in frame_match.group("lumps").split(",")
            )
            if len(lumps) != 8:
                raise ValueError(
                    f"{path}: frame {frame_match.group('index')} "
                    f"contains {len(lumps)} rotations"
                )
            index = int(frame_match.group("index"))
            if index != len(frames):
                raise ValueError(f"{path}: non-contiguous frame index {index}")
            frames.append(
                GeneratedFrame(
                    frame_match.group("name"),
                    frame_match.group("frame"),
                    lumps,
                    int(frame_match.group("flipmask"), 16),
                    int(frame_match.group("rotate")),
                )
            )
            continue

        definition_match = DEFINITION_PATTERN.match(line)
        if definition_match:
            index = int(definition_match.group("index"))
            if index != len(definitions):
                raise ValueError(
                    f"{path}: non-contiguous definition index {index}"
                )
            offset_text = definition_match.group("offset")
            definitions.append(
                GeneratedDefinition(
                    index,
                    definition_match.group("name"),
                    int(definition_match.group("count")),
                    int(offset_text) if offset_text is not None else None,
                )
            )

    return (
        macro_value(source, "DOOM_SPRITE_DEFS_IWAD_SHA256"),
        macro_value(source, "DOOM_SPRITE_DEFS_EMBEDDED_WAD_SHA256"),
        int(macro_value(source, "DOOM_SPRITE_DEFINITION_COUNT")),
        int(macro_value(source, "DOOM_SPRITE_FRAME_COUNT")),
        int(macro_value(source, "DOOM_SPRITE_LUMP_COUNT")),
        tuple(frames),
        tuple(definitions),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iwad", required=True, type=Path)
    parser.add_argument("--embedded-wad-header", required=True, type=Path)
    parser.add_argument("--sprite-source", required=True, type=Path)
    parser.add_argument("--generated-header", required=True, type=Path)
    args = parser.parse_args()

    iwad = read_standard_wad(args.iwad)
    embedded_wad = read_embedded_wad_header(args.embedded_wad_header)
    sprite_names = read_sprite_names(args.sprite_source)
    sprite_lumps = sprite_lumps_from_exact_wad(
        iwad, embedded_wad, sprite_names
    )
    (
        generated_iwad_hash,
        generated_embedded_hash,
        generated_definition_count,
        generated_frame_count,
        generated_lump_count,
        generated_frames,
        generated_definitions,
    ) = parse_generated_header(args.generated_header)

    if generated_iwad_hash != sha256(iwad.data):
        raise ValueError("generated header IWAD hash does not match the input")
    if generated_embedded_hash != sha256(embedded_wad.data):
        raise ValueError(
            "generated header embedded-WAD hash does not match the input"
        )
    if generated_definition_count != len(sprite_names):
        raise ValueError("generated definition-count macro is stale")
    if generated_frame_count != len(generated_frames):
        raise ValueError("generated frame-count macro is stale")
    if generated_lump_count != len(sprite_lumps):
        raise ValueError("generated lump-count macro is stale")
    if len(generated_definitions) != len(sprite_names):
        raise ValueError("generated definition table is incomplete")

    checked_rotations = 0
    checked_flip_bits = 0
    expected_offset = 0
    referenced_lumps: set[int] = set()

    for sprite_index, sprite_name in enumerate(sprite_names):
        expected = build_reference_definition(sprite_name, sprite_lumps)
        actual_definition = generated_definitions[sprite_index]
        if actual_definition.name != sprite_name:
            raise ValueError(
                f"sprite {sprite_index}: expected {sprite_name}, "
                f"found {actual_definition.name}"
            )
        if actual_definition.frame_count != len(expected):
            raise ValueError(
                f"sprite {sprite_name}: expected {len(expected)} frames, "
                f"found {actual_definition.frame_count}"
            )
        expected_pointer = expected_offset if expected else None
        if actual_definition.frame_offset != expected_pointer:
            raise ValueError(
                f"sprite {sprite_name}: expected frame offset "
                f"{expected_pointer}, found {actual_definition.frame_offset}"
            )

        for frame_index, expected_frame in enumerate(expected):
            actual_frame = generated_frames[expected_offset + frame_index]
            frame_name = chr(ord("A") + frame_index)
            if (actual_frame.name, actual_frame.frame) != (
                sprite_name,
                frame_name,
            ):
                raise ValueError(
                    f"frame {expected_offset + frame_index}: expected "
                    f"{sprite_name} {frame_name}, found "
                    f"{actual_frame.name} {actual_frame.frame}"
                )
            if actual_frame.rotate != expected_frame.rotate:
                raise ValueError(
                    f"sprite {sprite_name} frame {frame_name}: rotate mismatch"
                )
            if actual_frame.flipmask != expected_frame.flipmask:
                raise ValueError(
                    f"sprite {sprite_name} frame {frame_name}: "
                    "flipmask mismatch"
                )

            for rotation, (actual_lump, expected_lump) in enumerate(
                zip(actual_frame.lumps, expected_frame.lumps)
            ):
                if actual_lump != expected_lump:
                    raise ValueError(
                        f"sprite {sprite_name} frame {frame_name} "
                        f"rotation {rotation}: expected lump {expected_lump}, "
                        f"found {actual_lump}"
                    )
                actual_flip = (actual_frame.flipmask >> rotation) & 1
                expected_flip = (expected_frame.flipmask >> rotation) & 1
                if actual_flip != expected_flip:
                    raise ValueError(
                        f"sprite {sprite_name} frame {frame_name} "
                        f"rotation {rotation}: flip mismatch"
                    )
                referenced_lumps.add(actual_lump)
                checked_rotations += 1
                checked_flip_bits += 1

        expected_offset += len(expected)

    source_lump_indices = {lump.index for lump in sprite_lumps}
    missing_lumps = source_lump_indices - referenced_lumps
    foreign_lumps = referenced_lumps - source_lump_indices
    if missing_lumps or foreign_lumps:
        raise ValueError(
            "generated lookup coverage differs from the runtime namespace: "
            f"missing={sorted(missing_lumps)}, foreign={sorted(foreign_lumps)}"
        )

    print(
        f"verified {len(sprite_names)}/{len(sprite_names)} sprites, "
        f"{len(source_lump_indices)}/{len(source_lump_indices)} source lumps, "
        f"{len(generated_frames)}/{len(generated_frames)} frames, "
        f"{checked_rotations}/{checked_rotations} lump rotations, and "
        f"{checked_flip_bits}/{checked_flip_bits} flip rotations; "
        f"IWAD sha256 {sha256(iwad.data)}"
    )


if __name__ == "__main__":
    main()
