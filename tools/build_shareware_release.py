#!/usr/bin/env python3
"""Build a Neo Geo release from canonical Doom v1.9 shareware data."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from datetime import date
from pathlib import Path


SHAREWARE_WAD_BYTES = 4_196_020
SHAREWARE_WAD_SHA256 = (
    "1d7d43be501e67d927e415e0b8f3e29c3bf33075e859721816f652a526cac771"
)
SHAREWARE_ARCHIVE_URL = (
    "https://www.gamers.org/pub/games/idgames/idstuff/doom/doom19s.zip"
)
SOURCE_URL = "https://github.com/sabino/Doom64KB"
FIXED_ZIP_TIME = (2026, 7, 24, 0, 0, 0)

ROM_SIZES = {
    "doom64kb-p1.p1": 1 * 1024 * 1024,
    "doom64kb-p2.p2": 1 * 1024 * 1024,
    "doom64kb-c1.c1": 4 * 1024 * 1024,
    "doom64kb-c2.c2": 4 * 1024 * 1024,
    "doom64kb-v1.v1": 16 * 1024 * 1024,
    "doom64kb-s1.s1": 128 * 1024,
    "doom64kb-m1.m1": 128 * 1024,
}

GENERATED_MENU_ASSETS = (
    "neogeo/assets/doom-menu.fix",
    "neogeo/assets/doom-title.c1",
    "neogeo/assets/doom-title.c2",
    "neogeo/doom_fix_menu_assets.h",
    "neogeo/doom_fix_intermission_assets.h",
    "neogeo/doom_fix_menu_palette.h",
    "neogeo/doom_fix_wipe_assets.h",
    "neogeo/doom_title_assets.h",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    print("+ " + " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def require_tools(names: tuple[str, ...]) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise ValueError("missing required tools: " + ", ".join(missing))


def validate_shareware_wad(path: Path) -> None:
    actual_size = path.stat().st_size
    actual_hash = sha256_file(path)
    if (
        actual_size != SHAREWARE_WAD_BYTES
        or actual_hash != SHAREWARE_WAD_SHA256
    ):
        raise ValueError(
            f"{path} is not canonical Doom v1.9 shareware DOOM1.WAD "
            f"({actual_size} bytes, SHA-256 {actual_hash})"
        )


def copy_working_tree(repo: Path, stage: Path) -> str:
    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {
            ".git",
            "__pycache__",
            "dist",
            "DOOM64TB.WAD",
        }
        current = Path(directory)
        if current == repo / "neogeo":
            ignored.add("rom")
        if current == repo / "neogeo" / "assets":
            ignored.add("generated")
        return ignored & set(names)

    shutil.copytree(repo, stage, ignore=ignore)
    commit = run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture=True,
    ).stdout.decode("ascii").strip()
    dirty = run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture=True,
    ).stdout
    return commit + ("-working-tree" if dirty else "")


def force_source_asset_regeneration(stage: Path) -> None:
    for relative in GENERATED_MENU_ASSETS:
        path = stage / relative
        if path.exists():
            path.unlink()


def normalize_zip(path: Path) -> None:
    with zipfile.ZipFile(path) as source:
        entries = [
            (entry.filename, source.read(entry))
            for entry in sorted(source.infolist(), key=lambda item: item.filename)
            if not entry.is_dir()
        ]
    temporary = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as output:
        for name, data in entries:
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, data)
    temporary.replace(path)


def validate_roms(stage: Path) -> Path:
    rom_dir = stage / "neogeo/rom"
    for name, expected in ROM_SIZES.items():
        actual = (rom_dir / name).stat().st_size
        if actual != expected:
            raise ValueError(f"{name}: expected {expected} bytes, found {actual}")

    game_zip = rom_dir / "doom64kb.zip"
    normalize_zip(game_zip)
    with zipfile.ZipFile(game_zip) as archive:
        names = set(archive.namelist())
    if set(ROM_SIZES) - names:
        raise ValueError("game archive is missing ROM components")
    if {"aes.zip", "neogeo.zip"} & names:
        raise ValueError("game archive unexpectedly contains a Neo Geo BIOS")
    return rom_dir


def write_release_docs(
    release_dir: Path,
    source_commit: str,
    shareware_readme: Path | None,
) -> None:
    (release_dir / "README.txt").write_text(
        textwrap.dedent(
            f"""\
            Doom64KB: Neo Geo Shareware Edition

            This unofficial, noncommercial ROM set uses canonical Doom v1.9
            shareware data. It is limited to Episode 1 (E1M1-E1M9), includes
            the original shareware information page under READ THIS!, and
            contains no registered or Ultimate Doom data. The PC keyboard
            controls page is intentionally omitted for the Neo Geo build.

            Neo Geo BIOS files are not included. Use a compatible user-supplied
            BIOS or open-source replacement.

            Controls:
              Joystick       Move
              A              Use / run
              B              Fire
              C and D        Strafe
              Player 1 Start Menu
              Player 2 Start Automap

            Source: {SOURCE_URL}
            Source commit: {source_commit}
            Shareware IWAD SHA-256: {SHAREWARE_WAD_SHA256}
            Original shareware archive: {SHAREWARE_ARCHIVE_URL}

            Doom64KB is free software under GPL-2.0-or-later. Doom game data
            remains copyright id Software and subject to its shareware terms.
            This project is not affiliated with id Software or SNK.
            """
        ),
        encoding="ascii",
    )
    (release_dir / "SOURCE.txt").write_text(
        textwrap.dedent(
            f"""\
            Source repository: {SOURCE_URL}
            Source commit: {source_commit}
            Build command: bash bneogeo.sh
            Input profile: canonical Doom v1.9 shareware DOOM1.WAD
            Input SHA-256: {SHAREWARE_WAD_SHA256}
            """
        ),
        encoding="ascii",
    )
    if shareware_readme is not None:
        shutil.copy2(shareware_readme, release_dir / "DOOM19S-README.TXT")


def package_release(
    stage: Path,
    rom_dir: Path,
    output_dir: Path,
    source_commit: str,
    shareware_readme: Path | None,
    force: bool,
) -> Path:
    stem = f"Doom64KB-NeoGeo-Shareware-{date.today():%Y%m%d}"
    release_dir = output_dir / stem
    release_zip = output_dir / f"{stem}.zip"
    if (release_dir.exists() or release_zip.exists()) and not force:
        raise ValueError(f"{release_dir} already exists; pass --force")
    if release_dir.exists():
        shutil.rmtree(release_dir)
    if release_zip.exists():
        release_zip.unlink()
    release_dir.mkdir(parents=True)

    for name in ("doom64kb.zip", "neogeo.xml"):
        shutil.copy2(rom_dir / name, release_dir / name)
    shutil.copy2(stage / "LICENSE", release_dir / "LICENSE-GPL.txt")
    write_release_docs(
        release_dir,
        source_commit,
        shareware_readme,
    )

    manifest: dict[str, object] = {
        "profile": "doom-v1.9-shareware",
        "source_repository": SOURCE_URL,
        "source_commit": source_commit,
        "shareware_iwad_sha256": SHAREWARE_WAD_SHA256,
        "files": {},
    }
    files = manifest["files"]
    assert isinstance(files, dict)
    for path in sorted(release_dir.iterdir()):
        if path.is_file():
            files[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
    (release_dir / "BUILD-MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )

    checksums = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(release_dir.iterdir())
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    (release_dir / "SHA256SUMS").write_text(
        "\n".join(checksums) + "\n",
        encoding="ascii",
    )

    with zipfile.ZipFile(
        release_zip,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(release_dir.iterdir()):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(f"{stem}/{path.name}", FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return release_zip


def build(args: argparse.Namespace, work: Path) -> tuple[Path, Path]:
    repo = Path(__file__).resolve().parent.parent
    source_iwad = args.iwad.resolve()
    validate_shareware_wad(source_iwad)

    stage = work / "source"
    source_commit = copy_working_tree(repo, stage)
    shutil.copy2(source_iwad, stage / "DOOM64TB.WAD")
    force_source_asset_regeneration(stage)
    run(["bash", "bneogeo.sh"], cwd=stage)
    rom_dir = validate_roms(stage)
    release = package_release(
        stage,
        rom_dir,
        args.output_dir.resolve(),
        source_commit,
        args.shareware_readme.resolve() if args.shareware_readme else None,
        args.force,
    )
    return release, stage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iwad", type=Path, help="canonical Doom v1.9 DOOM1.WAD")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist/shareware"),
    )
    parser.add_argument("--shareware-readme", type=Path)
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    require_tools(
        (
            "sox",
            "adpcmtool.py",
            "m68k-neogeo-elf-gcc",
            "m68k-neogeo-elf-objcopy",
            "z80-neogeo-ihx-sdasz80",
            "romtool.py",
        )
    )
    if not args.iwad.is_file():
        parser.error(f"IWAD not found: {args.iwad}")
    if args.shareware_readme and not args.shareware_readme.is_file():
        parser.error(f"shareware README not found: {args.shareware_readme}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if args.keep_work:
            work = Path(tempfile.mkdtemp(prefix="doom64kb-shareware."))
            release, stage = build(args, work)
            print(f"build directory retained at {work}")
            print(f"test ROM directory: {stage / 'neogeo/rom'}")
        else:
            with tempfile.TemporaryDirectory(
                prefix="doom64kb-shareware."
            ) as temporary:
                release, _stage = build(args, Path(temporary))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(f"release: {release}")
    print(f"sha256: {sha256_file(release)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
