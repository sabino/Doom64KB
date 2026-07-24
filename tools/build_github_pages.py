#!/usr/bin/env python3
"""Build the Doom64KB GitHub Pages player and FBNeo launch package."""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path


PROJECT_NAME = "Doom64KB"
FBNEO_DRIVER = "19yy"
FBNEO_ROM_NAME = f"{FBNEO_DRIVER}.zip"
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class RomEntry:
    target: str
    size: int
    crc: int
    source: str | None = None
    source_parts: tuple[str, ...] = ()
    source_offset: int = 0
    source_size: int | None = None
    pad_byte: int = 0


# 19YY is a plain NeoInit FBNeo driver whose complete 8 MiB C-ROM region exactly
# matches Doom64KB. FBNeo swaps its two 1 MiB P-ROM halves during loading, so
# the browser package stores Doom64KB P2 before P1 to produce the native order.
ROM_ENTRIES = (
    RomEntry(
        "19yy-p1.p1",
        0x200000,
        0x59374C47,
        source_parts=("doom64kb-p2.p2", "doom64kb-p1.p1"),
        pad_byte=0xFF,
    ),
    RomEntry("19yy-s1.s1", 0x020000, 0x219B6F40, "doom64kb-s1.s1"),
    RomEntry("19yy-c1.c1", 0x400000, 0x622719D5, "doom64kb-c1.c1"),
    RomEntry("19yy-c2.c2", 0x400000, 0x41B07BE5, "doom64kb-c2.c2"),
    RomEntry("19yy-m1.m1", 0x020000, 0x8E05762A, "doom64kb-m1.m1"),
    RomEntry(
        "19yy-v1.v1",
        0x800000,
        0x944146C2,
        "doom64kb-v1.v1",
        source_offset=0x000000,
        source_size=0x800000,
    ),
    RomEntry(
        "19yy-v2.v2",
        0x800000,
        0xA4BAFE45,
        "doom64kb-v1.v1",
        source_offset=0x800000,
        source_size=0x800000,
    ),
)

BIOS_ENTRIES = {
    "sp-s3.sp1": (("sp-s3.sp1", "sp-s2.sp1", "neo-epo.bin", "aes-bios.bin"), 0x91B64BE3),
    "sm1.sm1": (("sm1.sm1",), 0x94416D67),
    "sfix.sfix": (("sfix.sfix",), 0xC2EA0CFD),
    "000-lo.lo": (("000-lo.lo",), 0x5A86CFF2),
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def crc32(data: bytes) -> int:
    return binascii.crc32(data) & 0xFFFFFFFF


def force_crc32(data: bytes, desired: int, patch_offset: int) -> bytes:
    """Adjust four padding bytes so the complete blob has the requested CRC32."""
    if patch_offset < 0 or patch_offset + 4 > len(data):
        raise BuildError(f"invalid CRC patch offset {patch_offset} for {len(data)} bytes")

    base = bytearray(data)
    base[patch_offset : patch_offset + 4] = b"\0\0\0\0"
    base_crc = crc32(base)
    delta = desired ^ base_crc

    columns: list[int] = []
    probe = bytearray(base)
    for bit in range(32):
        probe[patch_offset : patch_offset + 4] = b"\0\0\0\0"
        probe[patch_offset + bit // 8] = 1 << (bit % 8)
        columns.append(crc32(probe) ^ base_crc)

    rows: list[tuple[int, int]] = []
    for bit in range(32):
        mask = 0
        for column, value in enumerate(columns):
            if (value >> bit) & 1:
                mask |= 1 << column
        rows.append((mask, (delta >> bit) & 1))

    rank = 0
    for column in range(32):
        pivot = next(
            (row for row in range(rank, 32) if (rows[row][0] >> column) & 1),
            None,
        )
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        pivot_mask, pivot_rhs = rows[rank]
        for row in range(32):
            if row != rank and ((rows[row][0] >> column) & 1):
                rows[row] = (rows[row][0] ^ pivot_mask, rows[row][1] ^ pivot_rhs)
        rank += 1

    if rank != 32:
        raise BuildError("CRC patch matrix is singular")

    patch_value = 0
    for mask, rhs in rows:
        if mask and rhs:
            patch_value |= mask & -mask

    patched = bytearray(base)
    patched[patch_offset : patch_offset + 4] = patch_value.to_bytes(4, "little")
    actual = crc32(patched)
    if actual != desired:
        raise BuildError(f"CRC patch failed: wanted {desired:08x}, got {actual:08x}")
    return bytes(patched)


def find_padding_patch_offset(data: bytes, label: str) -> tuple[int, int, int]:
    """Find a long zero/FF run so CRC correction does not replace live code/art."""
    candidates: list[tuple[int, int, int]] = []
    for value in (0x00, 0xFF):
        pattern = re.compile(bytes((value,)) + b"{8,}")
        for match in pattern.finditer(data):
            candidates.append((match.end() - match.start(), match.start(), value))
    if not candidates:
        raise BuildError(f"{label} has no safe zero/FF run for its CRC correction")
    length, start, value = max(candidates, key=lambda item: (item[0], item[1]))
    return start + length - 4, value, length


def patch_for_fbneo(data: bytes, desired_crc: int, label: str) -> tuple[bytes, dict[str, int | str | None]]:
    original_crc = crc32(data)
    if original_crc == desired_crc:
        return data, {
            "original_crc32": f"{original_crc:08x}",
            "target_crc32": f"{desired_crc:08x}",
            "patch_offset": None,
            "padding_byte": None,
            "padding_run": 0,
        }

    patch_offset, padding_byte, padding_run = find_padding_patch_offset(data, label)
    patched = force_crc32(data, desired_crc, patch_offset)
    return patched, {
        "original_crc32": f"{original_crc:08x}",
        "target_crc32": f"{desired_crc:08x}",
        "patch_offset": patch_offset,
        "padding_byte": f"{padding_byte:02x}",
        "padding_run": padding_run,
    }


def read_source_entry(archive: zipfile.ZipFile, entry: RomEntry) -> bytes:
    if entry.source_parts:
        if entry.source is not None or entry.source_offset or entry.source_size is not None:
            raise BuildError(f"{entry.target} mixes whole-entry parts with a source slice")
        try:
            data = b"".join(archive.read(name) for name in entry.source_parts)
        except KeyError as exc:
            raise BuildError(f"ROM entry is missing: {exc.args[0]}") from exc
        if len(data) > entry.size:
            raise BuildError(f"{entry.target} source is larger than its {entry.size:#x}-byte window")
        return data + bytes((entry.pad_byte,)) * (entry.size - len(data))

    if entry.source is None:
        return bytes((entry.pad_byte,)) * entry.size
    try:
        source = archive.read(entry.source)
    except KeyError as exc:
        raise BuildError(f"ROM entry is missing: {entry.source}") from exc

    source_size = entry.source_size
    if source_size is None:
        source_size = len(source) - entry.source_offset
    end = entry.source_offset + source_size
    if end > len(source):
        raise BuildError(
            f"{entry.source} slice {entry.source_offset:#x}..{end:#x} exceeds "
            f"its {len(source):#x}-byte size"
        )
    data = source[entry.source_offset:end]
    if len(data) > entry.size:
        raise BuildError(f"{entry.target} source is larger than its {entry.size:#x}-byte window")
    return data + bytes((entry.pad_byte,)) * (entry.size - len(data))


def build_rom_entries(source_zip: Path) -> tuple[dict[str, bytes], list[dict[str, object]]]:
    output: dict[str, bytes] = {}
    records: list[dict[str, object]] = []
    with zipfile.ZipFile(source_zip) as archive:
        for entry in ROM_ENTRIES:
            data = read_source_entry(archive, entry)
            patched, correction = patch_for_fbneo(data, entry.crc, entry.target)
            output[entry.target] = patched
            records.append(
                {
                    "name": entry.target,
                    "size": len(patched),
                    "sha256": sha256_bytes(patched),
                    "source": list(entry.source_parts) if entry.source_parts else entry.source,
                    "source_offset": entry.source_offset,
                    **correction,
                }
            )
    return output, records


def build_bios_entries(source_zip: Path) -> tuple[dict[str, bytes], list[dict[str, object]]]:
    output: dict[str, bytes] = {}
    records: list[dict[str, object]] = []
    with zipfile.ZipFile(source_zip) as archive:
        available = set(archive.namelist())
        for target, (aliases, target_crc) in BIOS_ENTRIES.items():
            source_name = next((name for name in aliases if name in available), None)
            if source_name is None:
                raise BuildError(f"NullBIOS entry is missing: {' or '.join(aliases)}")
            data = archive.read(source_name)
            if len(data) > 0x20000:
                raise BuildError(f"{source_name} is unexpectedly large: {len(data)} bytes")
            data += b"\0" * (0x20000 - len(data))
            patched, correction = patch_for_fbneo(data, target_crc, target)
            output[target] = patched
            records.append(
                {
                    "name": target,
                    "size": len(patched),
                    "sha256": sha256_bytes(patched),
                    "source": source_name,
                    **correction,
                }
            )
    return output, records


def write_zip(path: Path, entries: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(
                info,
                entries[name],
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )


def verify_zip(path: Path, entries: tuple[RomEntry, ...] | None = None) -> None:
    expected = {entry.target: (entry.size, entry.crc) for entry in entries or ()}
    if entries is None:
        expected = {name: (0x20000, crc) for name, (_, crc) in BIOS_ENTRIES.items()}
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        missing = set(expected) - names
        if missing:
            raise BuildError(f"{path} is missing: {', '.join(sorted(missing))}")
        for name, (size, expected_crc) in expected.items():
            info = archive.getinfo(name)
            if info.file_size != size:
                raise BuildError(f"{path}:{name} is {info.file_size}, expected {size}")
            if info.CRC != expected_crc:
                raise BuildError(
                    f"{path}:{name} CRC {info.CRC:08x}, expected {expected_crc:08x}"
                )


def copy_site(
    templates: Path,
    output: Path,
    title_image: Path,
    game_url: str,
    bios_url: str,
    build_id: str,
) -> None:
    if not templates.is_dir():
        raise BuildError(f"web templates not found: {templates}")
    if not title_image.is_file():
        raise BuildError(f"title image not found: {title_image}")

    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(templates, output)
    shutil.copy2(title_image, output / "title.png")

    player_path = output / "player.js"
    player = player_path.read_text(encoding="utf-8")
    player = player.replace("__GAME_URL__", json.dumps(game_url))
    player = player.replace("__BIOS_URL__", json.dumps(bios_url))
    player = player.replace("__BUILD_ID__", build_id)
    if "__" in player:
        raise BuildError("unresolved player placeholder")
    player_path.write_text(player, encoding="utf-8")

    index_path = output / "index.html"
    index = index_path.read_text(encoding="utf-8").replace("__BUILD_ID__", build_id)
    index_path.write_text(index, encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="ascii")


def build(args: argparse.Namespace) -> None:
    source_rom = args.rom.resolve()
    source_bios = args.bios.resolve()
    templates = args.templates.resolve()
    title_image = args.title_image.resolve()
    output = args.output.resolve()
    for path, label in ((source_rom, "ROM"), (source_bios, "NullBIOS")):
        if not path.is_file():
            raise BuildError(f"{label} archive not found: {path}")

    version_digest = hashlib.sha256()
    for path in (source_rom, source_bios, title_image):
        version_digest.update(path.name.encode("utf-8"))
        version_digest.update(path.read_bytes())
    for path in sorted(templates.glob("*")):
        if path.is_file():
            version_digest.update(path.name.encode("utf-8"))
            version_digest.update(path.read_bytes())
    build_id = version_digest.hexdigest()[:12]
    asset_dir = Path("rom") / f"web-{build_id}"
    game_url = (asset_dir / FBNEO_ROM_NAME).as_posix()
    bios_url = (asset_dir / "neogeo.zip").as_posix()

    copy_site(templates, output, title_image, game_url, bios_url, build_id)
    rom_entries, rom_records = build_rom_entries(source_rom)
    bios_entries, bios_records = build_bios_entries(source_bios)
    web_dir = output / asset_dir
    launch_zip = web_dir / FBNEO_ROM_NAME
    bios_zip = web_dir / "neogeo.zip"
    write_zip(launch_zip, {**rom_entries, **bios_entries})
    write_zip(bios_zip, bios_entries)
    verify_zip(launch_zip, ROM_ENTRIES)
    verify_zip(launch_zip)
    verify_zip(bios_zip)

    manifest = {
        "project": PROJECT_NAME,
        "profile": "Doom v1.9 shareware",
        "renderer_profile": "fbneo-web-safe-strips",
        "fbneo_driver": FBNEO_DRIVER,
        "emulatorjs_version": "4.2.3",
        "build_id": build_id,
        "source_commit": os.environ.get("GITHUB_SHA"),
        "source_rom": {
            "name": source_rom.name,
            "bytes": source_rom.stat().st_size,
            "sha256": sha256_file(source_rom),
        },
        "source_bios": {
            "name": source_bios.name,
            "provider": "ngdevkit NullBIOS",
            "bytes": source_bios.stat().st_size,
            "sha256": sha256_file(source_bios),
        },
        "launch_zip": {
            "path": launch_zip.relative_to(output).as_posix(),
            "bytes": launch_zip.stat().st_size,
            "sha256": sha256_file(launch_zip),
        },
        "bios_zip": {
            "path": bios_zip.relative_to(output).as_posix(),
            "bytes": bios_zip.stat().st_size,
            "sha256": sha256_file(bios_zip),
        },
        "rom_entries": rom_records,
        "bios_entries": bios_records,
    }
    (output / "build-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Built {PROJECT_NAME} Pages bundle: {output}")
    print(f"FBNeo launch package: {launch_zip} ({launch_zip.stat().st_size} bytes)")
    print(f"Build ID: {build_id}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True, help="native doom64kb.zip")
    parser.add_argument("--bios", type=Path, required=True, help="ngdevkit NullBIOS zip")
    parser.add_argument("--output", type=Path, default=Path("dist/pages"))
    parser.add_argument("--templates", type=Path, default=Path("web"))
    parser.add_argument(
        "--title-image",
        type=Path,
        default=Path("readme_imgs/neogeo-title.png"),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        build(parse_args(sys.argv[1:] if argv is None else argv))
    except (BuildError, OSError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
