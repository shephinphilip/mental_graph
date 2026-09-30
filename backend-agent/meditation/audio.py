"""Resolve meditation audio from disk. Never invent a missing file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

AUDIO_DIR = Path(__file__).resolve().parent / "meditation audios"

# MPEG-1 Layer III bitrate index → kbps. Enough to estimate CBR guided audio.
_MPEG1_L3_KBPS = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_MPEG2_L3_KBPS = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)


def resolve_audio_file(meditation_id: str) -> Optional[Path]:
    """
    Return an existing ``{id}.mp3``.

    Prefer the file directly under ``meditation audios/``. A same-named copy
    in a subfolder is used only when the top-level file is absent. Names
    like ``311 (1).mp3`` are not treated as session 311.
    """
    session_id = str(meditation_id or "").strip()
    if not session_id or not AUDIO_DIR.is_dir():
        return None
    filename = f"{session_id}.mp3"
    top = AUDIO_DIR / filename
    if top.is_file():
        return top
    for candidate in AUDIO_DIR.rglob(filename):
        if candidate.is_file():
            return candidate
    return None


def measure_duration_seconds(path: Path) -> Optional[int]:
    """Estimate seconds from the first MPEG frame bitrate and file size."""
    try:
        return _measure_cached(str(path), path.stat().st_size)
    except OSError:
        return None


@lru_cache(maxsize=256)
def _measure_cached(path_str: str, size: int) -> Optional[int]:
    raw = Path(path_str).read_bytes()
    start = 0
    if raw[:3] == b"ID3" and len(raw) >= 10:
        start = 10 + (
            ((raw[6] & 0x7F) << 21)
            | ((raw[7] & 0x7F) << 14)
            | ((raw[8] & 0x7F) << 7)
            | (raw[9] & 0x7F)
        )
    end = min(len(raw) - 4, start + 300_000)
    index = start
    while index < end:
        if raw[index] == 0xFF and (raw[index + 1] & 0xE0) == 0xE0:
            version = (raw[index + 1] >> 3) & 0x3
            layer = (raw[index + 1] >> 1) & 0x3
            bitrate_index = (raw[index + 2] >> 4) & 0xF
            sample_index = (raw[index + 2] >> 2) & 0x3
            if (
                version == 1
                or layer != 1
                or bitrate_index in (0, 15)
                or sample_index == 3
            ):
                index += 1
                continue
            table = _MPEG1_L3_KBPS if version == 3 else _MPEG2_L3_KBPS
            bitrate = table[bitrate_index]
            if not bitrate:
                index += 1
                continue
            seconds = (len(raw) - start) * 8 / (bitrate * 1000)
            if seconds <= 0:
                return None
            return int(round(seconds))
        index += 1
    return None


def get_audio_path(meditation_id: str) -> Optional[Path]:
    """
    Metadata says the session can be played, and the file is actually there.

    Returns None when the catalog has no audio, or the referenced file
    is missing. Callers must keep working in that case.
    """
    from meditation.data import get_session_by_id

    session = get_session_by_id(str(meditation_id))
    if not session or not session.get("has_audio"):
        return None
    path = resolve_audio_file(str(meditation_id))
    if path is not None and path.is_file():
        return path
    return None
