#!/usr/bin/env python3
"""Generate Doom YM2610 audio assets directly from a Doom IWAD."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path


SFX_RATE = 18500
SFX_CMD_BASE = 0x04
MUSIC_CMD_BASE = 0x80
SFX_VOLUME_CMD_BASE = 0xD0
MUSIC_VOLUME_CMD_BASE = 0xE0
CMD_STOP = 0xF0
CMD_ALL_OFF = 0xF1
ADPCM_BLOCK_SIZE = 256
ADPCMA_PAGE_SIZE = 1 << 20
YM2610_ADDRESS_SPACE = 1 << 24

OUTPUT_NAMES = (
    "doom_audio.vrom",
    "doom_audio_generated.inc",
    "doom_audio_generated.h",
    "doom_audio_report.txt",
)

KNOWN_IWADS = {
    "1d7d43be501e67d927e415e0b8f3e29c3bf33075e859721816f652a526cac771":
        "Doom v1.9 shareware",
    "ff2c301b8719465a6e386a512bfa319931b7f64ea517d337c5a47afe03951902":
        "Doom v1.9 registered",
}

DEFAULT_SOUNDFONTS = (
    Path("/usr/share/sounds/sf2/default-GM.sf2"),
    Path("/usr/share/sounds/sf2/TimGM6mb.sf2"),
    Path("/usr/share/sounds/sf3/default-GM.sf3"),
)

MUS_CONTROLLER_TO_MIDI = {
    1: 0,
    2: 1,
    3: 7,
    4: 10,
    5: 11,
    6: 91,
    7: 93,
    8: 64,
    9: 67,
    10: 120,
    11: 123,
    12: 126,
    13: 127,
    14: 121,
}


@dataclass(frozen=True)
class WadLump:
    name: str
    offset: int
    size: int


class Wad:
    """Small, strict IWAD reader sufficient for Doom audio lumps."""

    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        if len(self.data) < 12:
            raise ValueError(f"{path}: file is too small to be a WAD")
        self.kind = self.data[:4]
        if self.kind != b"IWAD":
            kind = self.kind.decode("ascii", "replace")
            raise ValueError(f"{path}: expected a Doom IWAD, found {kind!r}")

        lump_count, directory_offset = struct.unpack_from("<II", self.data, 4)
        if lump_count > 1_000_000:
            raise ValueError(f"{path}: unreasonable lump count {lump_count}")
        directory_end = directory_offset + lump_count * 16
        if directory_offset < 12 or directory_end > len(self.data):
            raise ValueError(f"{path}: WAD directory lies outside the file")

        self.lumps: list[WadLump] = []
        self.by_name: dict[str, list[WadLump]] = {}
        for index in range(lump_count):
            entry = directory_offset + index * 16
            offset, size, raw_name = struct.unpack_from("<II8s", self.data, entry)
            if offset > len(self.data) or size > len(self.data) - offset:
                raise ValueError(f"{path}: lump {index} lies outside the file")
            name = raw_name.rstrip(b"\0").decode("ascii", "strict").upper()
            lump = WadLump(name, offset, size)
            self.lumps.append(lump)
            self.by_name.setdefault(name, []).append(lump)

    def lump_data(self, name: str) -> bytes:
        matches = self.by_name.get(name.upper())
        if not matches:
            raise ValueError(f"missing required IWAD lump {name}")
        lump = matches[-1]
        return self.data[lump.offset:lump.offset + lump.size]


@dataclass(frozen=True)
class SfxSpec:
    name: str
    doom_priority: int
    pan_volume: int = 0xDF

    @property
    def lump_name(self) -> str:
        return f"DS{self.name}"

    @property
    def driver_priority(self) -> int:
        # Doom gives lower numbers higher priority; the Z80 allocator uses
        # larger numbers for stronger sounds.
        return 0xFF - self.doom_priority


@dataclass(frozen=True)
class MusicSpec:
    name: str
    lump_name: str


@dataclass(frozen=True)
class PackedSfx:
    spec: SfxSpec
    start_unit: int
    stop_unit: int
    byte_size: int
    page: int
    padding_before: int


@dataclass(frozen=True)
class PackedMusic:
    spec: MusicSpec
    start_unit: int
    stop_unit: int
    byte_size: int
    delta_n: int
    pan: int = 0xC0
    volume: int = 0xFF
    repeat: int = 1


@dataclass(frozen=True)
class MusicRender:
    frames: int
    rate: int
    truncated: bool

    @property
    def seconds(self) -> float:
        return self.frames / float(self.rate)


# Order is exactly sounds.h:sfxenum_t with sfx_None removed.
SFX_SPECS = (
    SfxSpec("PISTOL", 64),
    SfxSpec("SHOTGN", 64),
    SfxSpec("SGCOCK", 64),
    SfxSpec("SAWUP", 64),
    SfxSpec("SAWIDL", 118),
    SfxSpec("SAWFUL", 64),
    SfxSpec("SAWHIT", 64),
    SfxSpec("RLAUNC", 64),
    SfxSpec("RXPLOD", 70),
    SfxSpec("FIRSHT", 70),
    SfxSpec("FIRXPL", 70),
    SfxSpec("PSTART", 100),
    SfxSpec("PSTOP", 100),
    SfxSpec("DOROPN", 100),
    SfxSpec("DORCLS", 100),
    SfxSpec("STNMOV", 119),
    SfxSpec("SWTCHN", 78),
    SfxSpec("SWTCHX", 78),
    SfxSpec("PLPAIN", 96),
    SfxSpec("DMPAIN", 96),
    SfxSpec("POPAIN", 96),
    SfxSpec("SLOP", 78),
    SfxSpec("ITEMUP", 78),
    SfxSpec("WPNUP", 78),
    SfxSpec("OOF", 96),
    SfxSpec("TELEPT", 32),
    SfxSpec("POSIT1", 98),
    SfxSpec("POSIT2", 98),
    SfxSpec("POSIT3", 98),
    SfxSpec("BGSIT1", 98),
    SfxSpec("BGSIT2", 98),
    SfxSpec("SGTSIT", 98),
    SfxSpec("BRSSIT", 94),
    SfxSpec("SGTATK", 70),
    SfxSpec("CLAW", 70),
    SfxSpec("PLDETH", 32),
    SfxSpec("PDIEHI", 32),
    SfxSpec("PODTH1", 70),
    SfxSpec("PODTH2", 70),
    SfxSpec("PODTH3", 70),
    SfxSpec("BGDTH1", 70),
    SfxSpec("BGDTH2", 70),
    SfxSpec("SGTDTH", 70),
    SfxSpec("BRSDTH", 32),
    SfxSpec("POSACT", 120),
    SfxSpec("BGACT", 120),
    SfxSpec("DMACT", 120),
    SfxSpec("NOWAY", 78),
    SfxSpec("BAREXP", 60),
    SfxSpec("PUNCH", 64),
    SfxSpec("TINK", 60),
    SfxSpec("GETPOW", 60),
)

# Order is exactly sounds.h:musicenum_t with mus_None removed.
MUSIC_SPECS = (
    MusicSpec("E1M1", "D_E1M1"),
    MusicSpec("E1M2", "D_E1M2"),
    MusicSpec("E1M3", "D_E1M3"),
    MusicSpec("E1M4", "D_E1M4"),
    MusicSpec("E1M5", "D_E1M5"),
    MusicSpec("E1M6", "D_E1M6"),
    MusicSpec("E1M7", "D_E1M7"),
    MusicSpec("E1M8", "D_E1M8"),
    MusicSpec("E1M9", "D_E1M9"),
    MusicSpec("INTER", "D_INTER"),
    MusicSpec("INTRO", "D_INTRO"),
    MusicSpec("VICTOR", "D_VICTOR"),
    MusicSpec("INTROA", "D_INTROA"),
)


def parse_int(text: str) -> int:
    try:
        return int(text, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {text}") from exc


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def align(data: bytearray, boundary: int) -> int:
    padding = (-len(data)) % boundary
    if padding:
        data.extend(b"\0" * padding)
    return padding


def resolve_executable(value: str) -> str:
    found = shutil.which(value)
    if found:
        return found
    path = Path(value).expanduser()
    if path.is_file():
        return str(path.resolve())
    raise ValueError(f"executable not found: {value}")


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def command_version(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError:
        return "unknown"
    output = result.stdout.strip().splitlines()
    return output[0] if output else "unknown"


def extract_dmx_sound(wad: Wad, lump_name: str) -> tuple[int, bytes]:
    data = wad.lump_data(lump_name)
    if len(data) < 40:
        raise ValueError(f"{lump_name}: lump is too small for DMX sound data")
    sample_format, sample_rate = struct.unpack_from("<HH", data, 0)
    sample_count = struct.unpack_from("<I", data, 4)[0]
    if sample_format != 3:
        raise ValueError(f"{lump_name}: unsupported DMX format {sample_format}")
    if sample_rate <= 0:
        raise ValueError(f"{lump_name}: invalid sample rate {sample_rate}")
    if sample_count > len(data) - 8:
        raise ValueError(
            f"{lump_name}: declared {sample_count} samples exceed lump size {len(data)}"
        )
    if sample_count <= 32:
        raise ValueError(f"{lump_name}: no PCM remains after DMX guard samples")
    pcm = data[8 + 16:8 + sample_count - 16]
    if not pcm:
        raise ValueError(f"{lump_name}: empty PCM payload")
    return sample_rate, pcm


def write_u8_wav(path: Path, rate: int, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(1)
        output.setframerate(rate)
        output.writeframes(pcm)


def convert_wav(
    sox: str,
    source: Path,
    output: Path,
    rate: int,
    gain_db: float,
) -> None:
    run([
        sox,
        "-D",
        "-G",
        str(source),
        "-r",
        str(rate),
        "-c",
        "1",
        "-b",
        "16",
        str(output),
        "gain",
        f"{gain_db:g}",
    ])


def encode_adpcm(adpcmtool: str, mode: str, source: Path, output: Path) -> None:
    run([adpcmtool, mode, "-e", str(source), "-o", str(output)])
    size = output.stat().st_size
    if size == 0 or size % ADPCM_BLOCK_SIZE:
        raise ValueError(f"{output.name}: invalid encoded size {size}")


def read_var_len(data: bytes, position: int, end: int) -> tuple[int, int]:
    value = 0
    while position < end:
        byte = data[position]
        position += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, position
    raise ValueError("truncated MUS variable-length value")


def mus_to_midi_channel(channel: int) -> int:
    if channel == 15:
        return 9
    if channel >= 9:
        return channel + 1
    return channel


def choose_soundfont(explicit: str | None) -> Path:
    candidate = explicit or os.environ.get("DOOM_SOUNDFONT")
    if candidate:
        path = Path(candidate).expanduser()
        if not path.is_file():
            raise ValueError(f"soundfont not found: {path}")
        return path.resolve()
    for path in DEFAULT_SOUNDFONTS:
        if path.is_file():
            return path.resolve()
    raise ValueError(
        "no GM soundfont found; pass --soundfont or install TimGM6mb.sf2"
    )


def load_fluidsynth() -> tuple[ctypes.CDLL, str]:
    library_name = ctypes.util.find_library("fluidsynth") or "libfluidsynth.so.3"
    try:
        library = ctypes.CDLL(library_name)
    except OSError as exc:
        raise ValueError("libfluidsynth is unavailable") from exc

    library.new_fluid_settings.restype = ctypes.c_void_p
    library.delete_fluid_settings.argtypes = [ctypes.c_void_p]
    library.fluid_settings_setnum.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_double,
    ]
    library.fluid_settings_setnum.restype = ctypes.c_int
    if hasattr(library, "fluid_settings_setint"):
        library.fluid_settings_setint.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        library.fluid_settings_setint.restype = ctypes.c_int
    library.new_fluid_synth.argtypes = [ctypes.c_void_p]
    library.new_fluid_synth.restype = ctypes.c_void_p
    library.delete_fluid_synth.argtypes = [ctypes.c_void_p]
    library.fluid_synth_sfload.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    library.fluid_synth_sfload.restype = ctypes.c_int
    library.fluid_synth_program_change.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
    ]
    library.fluid_synth_cc.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]
    library.fluid_synth_noteon.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]
    library.fluid_synth_noteoff.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
    ]
    if hasattr(library, "fluid_synth_pitch_bend"):
        library.fluid_synth_pitch_bend.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
        ]
    library.fluid_synth_write_s16.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
    ]
    library.fluid_synth_write_s16.restype = ctypes.c_int
    return library, library_name


def synth_mus_to_stereo_wav(
    library: ctypes.CDLL,
    wad: Wad,
    spec: MusicSpec,
    output: Path,
    rate: int,
    max_seconds: float,
    tail_seconds: float,
    soundfont: Path,
) -> MusicRender:
    data = wad.lump_data(spec.lump_name)
    if len(data) < 16 or data[:4] != b"MUS\x1a":
        raise ValueError(f"{spec.lump_name}: not a MUS score")
    score_length, score_start = struct.unpack_from("<HH", data, 4)
    score_end = score_start + score_length
    if score_start < 16 or score_end > len(data):
        raise ValueError(f"{spec.lump_name}: invalid MUS score range")

    settings = library.new_fluid_settings()
    if not settings:
        raise ValueError("FluidSynth failed to allocate settings")
    synth = None
    pcm = bytearray()
    max_frames = max(1, int(max_seconds * rate))
    rendered_frames = 0
    elapsed_ticks = 0
    truncated = False
    last_volume = [96] * 16

    try:
        library.fluid_settings_setnum(settings, b"synth.sample-rate", float(rate))
        library.fluid_settings_setnum(settings, b"synth.gain", 0.45)
        if hasattr(library, "fluid_settings_setint"):
            library.fluid_settings_setint(settings, b"synth.chorus.active", 0)
            library.fluid_settings_setint(settings, b"synth.reverb.active", 0)
            library.fluid_settings_setint(settings, b"synth.polyphony", 64)
        synth = library.new_fluid_synth(settings)
        if not synth:
            raise ValueError("FluidSynth failed to allocate synth")
        if library.fluid_synth_sfload(
            synth,
            str(soundfont).encode("utf-8"),
            1,
        ) < 0:
            raise ValueError(f"FluidSynth failed to load {soundfont}")

        for channel in range(16):
            midi_channel = mus_to_midi_channel(channel)
            library.fluid_synth_cc(synth, midi_channel, 7, 100)
            library.fluid_synth_cc(synth, midi_channel, 11, 127)

        def render_frames(frame_count: int) -> None:
            nonlocal rendered_frames, truncated
            remaining = max_frames - rendered_frames
            if frame_count > remaining:
                frame_count = remaining
                truncated = True
            while frame_count > 0:
                frames = min(frame_count, 1024)
                stereo = (ctypes.c_short * (frames * 2))()
                pointer = ctypes.cast(stereo, ctypes.c_void_p)
                result = library.fluid_synth_write_s16(
                    synth,
                    frames,
                    pointer,
                    0,
                    2,
                    pointer,
                    1,
                    2,
                )
                if result != 0:
                    raise ValueError("FluidSynth render failed")
                pcm.extend(ctypes.string_at(pointer, frames * 4))
                rendered_frames += frames
                frame_count -= frames

        def render_ticks(ticks: int) -> None:
            nonlocal elapsed_ticks
            if ticks <= 0:
                return
            elapsed_ticks += ticks
            target_frames = (elapsed_ticks * rate) // 140
            render_frames(max(0, target_frames - rendered_frames))

        position = score_start
        score_finished = False
        while position < score_end and rendered_frames < max_frames:
            group_finished = False
            while position < score_end and not group_finished:
                event = data[position]
                position += 1
                group_finished = bool(event & 0x80)
                event_type = (event >> 4) & 0x07
                channel = event & 0x0F
                midi_channel = mus_to_midi_channel(channel)

                if event_type == 0:
                    if position >= score_end:
                        raise ValueError(f"{spec.lump_name}: truncated note-off")
                    note = data[position] & 0x7F
                    position += 1
                    library.fluid_synth_noteoff(synth, midi_channel, note)
                elif event_type == 1:
                    if position >= score_end:
                        raise ValueError(f"{spec.lump_name}: truncated note-on")
                    note_byte = data[position]
                    position += 1
                    note = note_byte & 0x7F
                    if note_byte & 0x80:
                        if position >= score_end:
                            raise ValueError(f"{spec.lump_name}: truncated velocity")
                        last_volume[channel] = data[position] & 0x7F
                        position += 1
                    library.fluid_synth_noteon(
                        synth,
                        midi_channel,
                        note,
                        max(1, last_volume[channel]),
                    )
                elif event_type == 2:
                    if position >= score_end:
                        raise ValueError(f"{spec.lump_name}: truncated pitch bend")
                    bend = data[position] << 6
                    position += 1
                    if hasattr(library, "fluid_synth_pitch_bend"):
                        library.fluid_synth_pitch_bend(synth, midi_channel, bend)
                elif event_type == 3:
                    if position >= score_end:
                        raise ValueError(f"{spec.lump_name}: truncated system event")
                    controller = data[position]
                    position += 1
                    midi_controller = MUS_CONTROLLER_TO_MIDI.get(controller)
                    if midi_controller is not None:
                        library.fluid_synth_cc(synth, midi_channel, midi_controller, 0)
                elif event_type == 4:
                    if position + 1 >= score_end:
                        raise ValueError(f"{spec.lump_name}: truncated controller event")
                    controller = data[position]
                    value = data[position + 1] & 0x7F
                    position += 2
                    if controller == 0:
                        library.fluid_synth_program_change(synth, midi_channel, value)
                    else:
                        midi_controller = MUS_CONTROLLER_TO_MIDI.get(controller)
                        if midi_controller is not None:
                            library.fluid_synth_cc(
                                synth,
                                midi_channel,
                                midi_controller,
                                value,
                            )
                elif event_type == 6:
                    score_finished = True
                    break

            if score_finished or position >= score_end:
                break
            delay, position = read_var_len(data, position, score_end)
            render_ticks(delay)

        if rendered_frames >= max_frames and not score_finished:
            truncated = True
        for channel in range(16):
            library.fluid_synth_cc(
                synth,
                mus_to_midi_channel(channel),
                123,
                0,
            )
        render_frames(int(tail_seconds * rate))
    finally:
        if synth:
            library.delete_fluid_synth(synth)
        library.delete_fluid_settings(settings)

    if rendered_frames == 0:
        raise ValueError(f"{spec.lump_name}: score generated no audio frames")
    with wave.open(str(output), "wb") as wav_output:
        wav_output.setnchannels(2)
        wav_output.setsampwidth(2)
        wav_output.setframerate(rate)
        wav_output.writeframes(pcm)
    return MusicRender(rendered_frames, rate, truncated)


def delta_n_for_rate(rate: int) -> int:
    return max(1, min(0xFFFF, int(round(rate * 65536.0 / 55555.0))))


def check_address(start: int, stop: int, label: str) -> None:
    if start < 0 or stop < start or stop >= YM2610_ADDRESS_SPACE:
        raise ValueError(
            f"{label}: invalid YM2610 V-ROM range 0x{start:06x}..0x{stop:06x}"
        )


def pack_audio(
    work_dir: Path,
    vrom_size: int,
    music_rate: int,
) -> tuple[bytes, list[PackedSfx], list[PackedMusic], list[str]]:
    packed = bytearray()
    packed_sfx: list[PackedSfx] = []
    packed_music: list[PackedMusic] = []
    notes: list[str] = []

    for spec in SFX_SPECS:
        data = (work_dir / f"sfx_{spec.name.lower()}.adpcma").read_bytes()
        if len(data) > ADPCMA_PAGE_SIZE:
            raise ValueError(
                f"{spec.lump_name}: {len(data)}-byte ADPCM-A sample exceeds one 1 MiB page"
            )
        padding = align(packed, ADPCM_BLOCK_SIZE)
        start = len(packed)
        stop = start + len(data) - 1
        if start // ADPCMA_PAGE_SIZE != stop // ADPCMA_PAGE_SIZE:
            page_padding = align(packed, ADPCMA_PAGE_SIZE)
            padding += page_padding
            notes.append(
                f"page-pad {spec.name}: {page_padding} bytes to 0x{len(packed):06x}"
            )
            start = len(packed)
            stop = start + len(data) - 1
        if start // ADPCMA_PAGE_SIZE != stop // ADPCMA_PAGE_SIZE:
            raise ValueError(f"{spec.lump_name}: ADPCM-A sample crosses a 1 MiB page")
        check_address(start, stop, spec.lump_name)
        packed.extend(data)
        packed_sfx.append(PackedSfx(
            spec,
            start >> 8,
            stop >> 8,
            len(data),
            start // ADPCMA_PAGE_SIZE,
            padding,
        ))
        if len(packed) > vrom_size:
            raise ValueError(
                f"V-ROM exceeds configured size while packing {spec.lump_name}: "
                f"{len(packed)} > {vrom_size}"
            )

    for spec in MUSIC_SPECS:
        data = (work_dir / f"music_{spec.name.lower()}.adpcmb").read_bytes()
        align(packed, ADPCM_BLOCK_SIZE)
        start = len(packed)
        stop = start + len(data) - 1
        check_address(start, stop, spec.lump_name)
        packed.extend(data)
        packed_music.append(PackedMusic(
            spec,
            start >> 8,
            stop >> 8,
            len(data),
            delta_n_for_rate(music_rate),
        ))
        if len(packed) > vrom_size:
            raise ValueError(
                f"V-ROM exceeds configured size while packing {spec.lump_name}: "
                f"{len(packed)} > {vrom_size}"
            )

    align(packed, ADPCM_BLOCK_SIZE)
    if len(packed) > vrom_size:
        raise ValueError(f"packed V-ROM is {len(packed)} bytes; limit is {vrom_size}")
    for item in packed_sfx:
        start = item.start_unit << 8
        stop = (item.stop_unit << 8) | 0xFF
        if start // ADPCMA_PAGE_SIZE != stop // ADPCMA_PAGE_SIZE:
            raise ValueError(f"{item.spec.lump_name}: final table range crosses 1 MiB")
    return bytes(packed), packed_sfx, packed_music, notes


def write_inc(
    path: Path,
    sfx: list[PackedSfx],
    music: list[PackedMusic],
) -> None:
    with path.open("w", encoding="ascii", newline="\n") as output:
        output.write("; generated by tools/gen_neogeo_audio.py\n")
        output.write(f".equ    DOOM_AUDIO_CMD_SFX_BASE,   0x{SFX_CMD_BASE:02x}\n")
        output.write(
            f".equ    DOOM_AUDIO_CMD_SFX_END,    0x{SFX_CMD_BASE + len(sfx):02x}\n"
        )
        output.write(f".equ    DOOM_AUDIO_SFX_COUNT,      {len(sfx)}\n")
        for index, item in enumerate(sfx):
            output.write(
                f".equ    DOOM_AUDIO_CMD_SFX_{item.spec.name}, "
                f"0x{SFX_CMD_BASE + index:02x}\n"
            )
        output.write(f".equ    DOOM_AUDIO_CMD_MUSIC_BASE, 0x{MUSIC_CMD_BASE:02x}\n")
        output.write(
            f".equ    DOOM_AUDIO_CMD_MUSIC_END,  0x{MUSIC_CMD_BASE + len(music):02x}\n"
        )
        output.write(f".equ    DOOM_AUDIO_MUSIC_COUNT,    {len(music)}\n")
        for index, item in enumerate(music):
            output.write(
                f".equ    DOOM_AUDIO_CMD_MUSIC_{item.spec.name}, "
                f"0x{MUSIC_CMD_BASE + index:02x}\n"
            )
        output.write(f".equ    DOOM_AUDIO_CMD_SFX_VOLUME_BASE,   0x{SFX_VOLUME_CMD_BASE:02x}\n")
        output.write(f".equ    DOOM_AUDIO_CMD_SFX_VOLUME_END,    0x{SFX_VOLUME_CMD_BASE + 16:02x}\n")
        output.write(f".equ    DOOM_AUDIO_CMD_MUSIC_VOLUME_BASE, 0x{MUSIC_VOLUME_CMD_BASE:02x}\n")
        output.write(f".equ    DOOM_AUDIO_CMD_MUSIC_VOLUME_END,  0x{MUSIC_VOLUME_CMD_BASE + 16:02x}\n")
        output.write(f".equ    DOOM_AUDIO_CMD_STOP,       0x{CMD_STOP:02x}\n")
        output.write(f".equ    DOOM_AUDIO_CMD_MUSIC_STOP, 0x{CMD_STOP:02x}\n")
        output.write(f".equ    DOOM_AUDIO_CMD_ALL_OFF,    0x{CMD_ALL_OFF:02x}\n\n")

        output.write("doom_sfx_table:\n")
        for item in sfx:
            output.write(
                "        .db     "
                f"0x{item.start_unit & 0xFF:02x}, "
                f"0x{item.start_unit >> 8:02x}, "
                f"0x{item.stop_unit & 0xFF:02x}, "
                f"0x{item.stop_unit >> 8:02x}, "
                f"0x{item.spec.driver_priority:02x}, "
                f"0x{item.spec.pan_volume:02x}"
                f"   ; {item.spec.name} {item.spec.lump_name}\n"
            )

        output.write("\ndoom_music_table:\n")
        for item in music:
            output.write(
                "        .db     "
                "0x01, "
                f"0x{item.start_unit & 0xFF:02x}, "
                f"0x{item.start_unit >> 8:02x}, "
                f"0x{item.stop_unit & 0xFF:02x}, "
                f"0x{item.stop_unit >> 8:02x}, "
                f"0x{item.delta_n & 0xFF:02x}, "
                f"0x{item.delta_n >> 8:02x}, "
                f"0x{item.pan:02x}, "
                f"0x{item.volume:02x}, "
                f"0x{item.repeat:02x}"
                f"   ; {item.spec.name} {item.spec.lump_name}\n"
            )


def write_header(
    path: Path,
    sfx: list[PackedSfx],
    music: list[PackedMusic],
) -> None:
    with path.open("w", encoding="ascii", newline="\n") as output:
        output.write("/* generated by tools/gen_neogeo_audio.py */\n")
        output.write("#ifndef DOOM_AUDIO_GENERATED_H\n")
        output.write("#define DOOM_AUDIO_GENERATED_H\n\n")
        output.write(f"#define DOOM_SOUND_CMD_SFX_BASE 0x{SFX_CMD_BASE:02X}\n")
        output.write(
            f"#define DOOM_SOUND_CMD_SFX_END 0x{SFX_CMD_BASE + len(sfx):02X}\n"
        )
        output.write(f"#define DOOM_SOUND_SFX_COUNT {len(sfx)}\n")
        output.write(
            "#define DOOM_SOUND_SFX_COMMAND(sfx_id) "
            "(DOOM_SOUND_CMD_SFX_BASE + (sfx_id) - 1)\n\n"
        )
        for index, item in enumerate(sfx):
            output.write(
                f"#define DOOM_SOUND_CMD_SFX_{item.spec.name} "
                f"0x{SFX_CMD_BASE + index:02X}\n"
            )

        output.write("\n")
        output.write(f"#define DOOM_SOUND_CMD_MUSIC_BASE 0x{MUSIC_CMD_BASE:02X}\n")
        output.write(
            f"#define DOOM_SOUND_CMD_MUSIC_END 0x{MUSIC_CMD_BASE + len(music):02X}\n"
        )
        output.write(f"#define DOOM_SOUND_MUSIC_COUNT {len(music)}\n")
        output.write(
            "#define DOOM_SOUND_MUSIC_COMMAND(music_id) "
            "(DOOM_SOUND_CMD_MUSIC_BASE + (music_id) - 1)\n"
        )
        for index, item in enumerate(music):
            output.write(
                f"#define DOOM_SOUND_CMD_MUSIC_{item.spec.name} "
                f"0x{MUSIC_CMD_BASE + index:02X}\n"
            )
        output.write("\n")
        output.write(f"#define DOOM_SOUND_CMD_SFX_VOLUME_BASE 0x{SFX_VOLUME_CMD_BASE:02X}\n")
        output.write(
            "#define DOOM_SOUND_SFX_VOLUME_COMMAND(volume) "
            "(DOOM_SOUND_CMD_SFX_VOLUME_BASE + (volume))\n"
        )
        output.write(f"#define DOOM_SOUND_CMD_MUSIC_VOLUME_BASE 0x{MUSIC_VOLUME_CMD_BASE:02X}\n")
        output.write(
            "#define DOOM_SOUND_MUSIC_VOLUME_COMMAND(volume) "
            "(DOOM_SOUND_CMD_MUSIC_VOLUME_BASE + (volume))\n"
        )
        output.write(f"#define DOOM_SOUND_CMD_STOP 0x{CMD_STOP:02X}\n")
        output.write("#define DOOM_SOUND_CMD_MUSIC_STOP DOOM_SOUND_CMD_STOP\n")
        output.write(f"#define DOOM_SOUND_CMD_ALL_OFF 0x{CMD_ALL_OFF:02X}\n\n")
        output.write("#endif /* DOOM_AUDIO_GENERATED_H */\n")


def validate_static_contract() -> None:
    expected_sfx = (
        "PISTOL", "SHOTGN", "SGCOCK", "SAWUP", "SAWIDL", "SAWFUL",
        "SAWHIT", "RLAUNC", "RXPLOD", "FIRSHT", "FIRXPL", "PSTART",
        "PSTOP", "DOROPN", "DORCLS", "STNMOV", "SWTCHN", "SWTCHX",
        "PLPAIN", "DMPAIN", "POPAIN", "SLOP", "ITEMUP", "WPNUP",
        "OOF", "TELEPT", "POSIT1", "POSIT2", "POSIT3", "BGSIT1",
        "BGSIT2", "SGTSIT", "BRSSIT", "SGTATK", "CLAW", "PLDETH",
        "PDIEHI", "PODTH1", "PODTH2", "PODTH3", "BGDTH1", "BGDTH2",
        "SGTDTH", "BRSDTH", "POSACT", "BGACT", "DMACT", "NOWAY",
        "BAREXP", "PUNCH", "TINK", "GETPOW",
    )
    expected_music = (
        "E1M1", "E1M2", "E1M3", "E1M4", "E1M5", "E1M6", "E1M7",
        "E1M8", "E1M9", "INTER", "INTRO", "VICTOR", "INTROA",
    )
    if tuple(item.name for item in SFX_SPECS) != expected_sfx:
        raise ValueError("internal SFX order no longer matches sounds.h")
    if tuple(item.name for item in MUSIC_SPECS) != expected_music:
        raise ValueError("internal music order no longer matches sounds.h")
    if SFX_CMD_BASE + len(SFX_SPECS) > MUSIC_CMD_BASE:
        raise ValueError("SFX commands overlap music commands")
    if MUSIC_CMD_BASE + len(MUSIC_SPECS) > CMD_STOP:
        raise ValueError("music commands overlap control commands")
    if MUSIC_CMD_BASE + len(MUSIC_SPECS) > SFX_VOLUME_CMD_BASE:
        raise ValueError("music commands overlap SFX volume commands")
    if SFX_VOLUME_CMD_BASE + 16 > MUSIC_VOLUME_CMD_BASE:
        raise ValueError("SFX and music volume commands overlap")
    if MUSIC_VOLUME_CMD_BASE + 16 > CMD_STOP:
        raise ValueError("music volume commands overlap control commands")


def generate(args: argparse.Namespace) -> None:
    validate_static_contract()
    if args.music_rate <= 0:
        raise ValueError("--music-rate must be positive")
    if args.music_max_seconds <= 0:
        raise ValueError("--music-max-seconds must be positive")
    if args.music_tail_seconds < 0:
        raise ValueError("--music-tail-seconds cannot be negative")
    if args.vrom_size <= 0 or args.vrom_size > YM2610_ADDRESS_SPACE:
        raise ValueError(
            f"--vrom-size must be between 1 and {YM2610_ADDRESS_SPACE} bytes"
        )

    iwad_path = Path(args.iwad).expanduser().resolve()
    if not iwad_path.is_file():
        raise ValueError(f"IWAD not found: {iwad_path}")
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    sox = resolve_executable(args.sox)
    adpcmtool = resolve_executable(args.adpcmtool)
    soundfont = choose_soundfont(args.soundfont)
    fluidsynth, fluidsynth_name = load_fluidsynth()
    wad = Wad(iwad_path)
    iwad_sha256 = sha256_bytes(wad.data)
    identity = KNOWN_IWADS.get(iwad_sha256, "unrecognized Doom IWAD")

    report: list[str] = [
        f"IWAD: {iwad_path}",
        f"IWAD identity: {identity}",
        f"IWAD bytes: {len(wad.data)}",
        f"IWAD lumps: {len(wad.lumps)}",
        f"IWAD SHA-256: {iwad_sha256}",
        f"SoX: {sox} ({command_version([sox, '--version'])})",
        f"adpcmtool: {adpcmtool} SHA-256={sha256_file(Path(adpcmtool))}",
        f"FluidSynth library: {fluidsynth_name}",
        f"SoundFont: {soundfont} SHA-256={sha256_file(soundfont)}",
        f"SFX rate: {SFX_RATE} Hz",
        f"SFX source gain: {args.sfx_gain_db:g} dB",
        f"Music rate: {args.music_rate} Hz",
        f"Music source gain: {args.music_gain_db:g} dB",
        f"V-ROM limit: {args.vrom_size} bytes",
        "",
        "SFX conversion (sounds.h order):",
    ]

    with tempfile.TemporaryDirectory(prefix=".doom_audio_", dir=out_dir) as temp_name:
        work_dir = Path(temp_name)
        for index, spec in enumerate(SFX_SPECS):
            raw_wav = work_dir / f"sfx_{spec.name.lower()}_raw.wav"
            converted_wav = work_dir / f"sfx_{spec.name.lower()}.wav"
            encoded = work_dir / f"sfx_{spec.name.lower()}.adpcma"
            source_rate, pcm = extract_dmx_sound(wad, spec.lump_name)
            write_u8_wav(raw_wav, source_rate, pcm)
            convert_wav(sox, raw_wav, converted_wav, SFX_RATE, args.sfx_gain_db)
            encode_adpcm(adpcmtool, "-a", converted_wav, encoded)
            report.append(
                f"  {index + 1:02d} {spec.name:<7} {spec.lump_name} "
                f"{source_rate}Hz {len(pcm)} PCM -> {encoded.stat().st_size} ADPCM-A"
            )

        report.extend(("", "Music conversion (musicenum_t order):"))
        for index, spec in enumerate(MUSIC_SPECS):
            stereo_wav = work_dir / f"music_{spec.name.lower()}_stereo.wav"
            mono_wav = work_dir / f"music_{spec.name.lower()}.wav"
            encoded = work_dir / f"music_{spec.name.lower()}.adpcmb"
            render = synth_mus_to_stereo_wav(
                fluidsynth,
                wad,
                spec,
                stereo_wav,
                args.music_rate,
                args.music_max_seconds,
                args.music_tail_seconds,
                soundfont,
            )
            convert_wav(
                sox,
                stereo_wav,
                mono_wav,
                args.music_rate,
                args.music_gain_db,
            )
            encode_adpcm(adpcmtool, "-b", mono_wav, encoded)
            report.append(
                f"  {index + 1:02d} {spec.name:<6} {spec.lump_name} "
                f"{render.seconds:.3f}s -> {encoded.stat().st_size} ADPCM-B "
                f"truncated={int(render.truncated)}"
            )

        vrom, packed_sfx, packed_music, packing_notes = pack_audio(
            work_dir,
            args.vrom_size,
            args.music_rate,
        )
        vrom_path = work_dir / "doom_audio.vrom"
        inc_path = work_dir / "doom_audio_generated.inc"
        header_path = work_dir / "doom_audio_generated.h"
        report_path = work_dir / "doom_audio_report.txt"
        vrom_path.write_bytes(vrom)
        write_inc(inc_path, packed_sfx, packed_music)
        write_header(header_path, packed_sfx, packed_music)

        report.extend(("", "SFX V-ROM ranges (validated within 1 MiB pages):"))
        for item in packed_sfx:
            start = item.start_unit << 8
            stop = (item.stop_unit << 8) | 0xFF
            report.append(
                f"  {item.spec.name:<7} 0x{start:06x}..0x{stop:06x} "
                f"page={item.page} bytes={item.byte_size} pad={item.padding_before}"
            )
        report.extend(("", "Music V-ROM ranges (looping ADPCM-B):"))
        for item in packed_music:
            start = item.start_unit << 8
            stop = (item.stop_unit << 8) | 0xFF
            report.append(
                f"  {item.spec.name:<6} cmd=0x{MUSIC_CMD_BASE + MUSIC_SPECS.index(item.spec):02x} "
                f"0x{start:06x}..0x{stop:06x} bytes={item.byte_size} "
                f"delta_n=0x{item.delta_n:04x}"
            )
        if packing_notes:
            report.extend(("", "Packing adjustments:", *(f"  {note}" for note in packing_notes)))
        report.extend([
            "",
            f"SFX command range: 0x{SFX_CMD_BASE:02x}.."
            f"0x{SFX_CMD_BASE + len(packed_sfx) - 1:02x}",
            f"Music command range: 0x{MUSIC_CMD_BASE:02x}.."
            f"0x{MUSIC_CMD_BASE + len(packed_music) - 1:02x}",
            f"SFX volume command range: 0x{SFX_VOLUME_CMD_BASE:02x}.."
            f"0x{SFX_VOLUME_CMD_BASE + 15:02x}",
            f"Music volume command range: 0x{MUSIC_VOLUME_CMD_BASE:02x}.."
            f"0x{MUSIC_VOLUME_CMD_BASE + 15:02x}",
            f"STOP command: 0x{CMD_STOP:02x}",
            f"ALL_OFF command: 0x{CMD_ALL_OFF:02x}",
            f"V-ROM used: {len(vrom)} / {args.vrom_size} bytes",
            f"V-ROM SHA-256: {sha256_file(vrom_path)}",
            f"ASM include SHA-256: {sha256_file(inc_path)}",
            f"C header SHA-256: {sha256_file(header_path)}",
        ])
        report_path.write_text("\n".join(report) + "\n", encoding="ascii")

        for name in OUTPUT_NAMES:
            os.replace(work_dir / name, out_dir / name)

    for line in report:
        print(line)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract authentic Doom SFX and MUS scores from a Doom IWAD, "
            "render music with libFluidSynth, and build YM2610 metadata/V-ROM."
        )
    )
    parser.add_argument("--iwad", required=True, help="path to a retail Doom IWAD")
    parser.add_argument("--out-dir", required=True, help="output directory")
    parser.add_argument("--sox", default="sox", help="SoX executable")
    parser.add_argument(
        "--adpcmtool",
        default="adpcmtool.py",
        help="ngdevkit adpcmtool.py executable",
    )
    parser.add_argument(
        "--soundfont",
        help="GM SF2/SF3 for libFluidSynth; falls back to TimGM6mb.sf2",
    )
    parser.add_argument("--music-rate", type=parse_int, default=11025)
    parser.add_argument(
        "--music-max-seconds",
        type=float,
        default=600.0,
        help="per-track safety limit; retail Doom tracks are shorter than 600s",
    )
    parser.add_argument(
        "--music-tail-seconds",
        type=float,
        default=0.0,
        help="optional release tail; zero gives immediate hardware loops",
    )
    parser.add_argument(
        "--sfx-gain-db",
        type=float,
        default=-3.0,
        help="SoX gain applied to SFX before ADPCM-A encoding",
    )
    parser.add_argument(
        "--music-gain-db",
        type=float,
        default=2.0,
        help="SoX gain applied to music before ADPCM-B encoding",
    )
    parser.add_argument(
        "--vrom-size",
        type=parse_int,
        default=YM2610_ADDRESS_SPACE,
        help="maximum V-ROM bytes, decimal or 0x-prefixed",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        generate(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
