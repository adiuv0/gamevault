"""HDR image processing: JXR decode, HDR PNG handling, tone-mapping to SDR.

Special K writes HDR captures as either:
  - JPEG XR (.jxr) — 16-bit-per-channel scRGB encoded, with HDR10 metadata
  - 16-bit PNG (.png) — typically scRGB linear or PQ-encoded values

Browsers can't render either of these as HDR, so we generate SDR JPEG
thumbnails by tone-mapping. The original file is preserved on disk so the
user can download the true HDR asset.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


# JXR file magic — the "GUID" at offset 0 for a Windows Media Photo /
# JPEG XR container. The first 3 bytes are always 0x49 0x49 0xBC.
# https://en.wikipedia.org/wiki/JPEG_XR#File_format
JXR_MAGIC = b"\x49\x49\xbc"

# Lazily import imagecodecs — heavy dep, only needed for JXR.
_imagecodecs = None


def _get_imagecodecs():
    global _imagecodecs
    if _imagecodecs is None:
        import imagecodecs  # noqa: WPS433

        _imagecodecs = imagecodecs
    return _imagecodecs


# ── Detection ────────────────────────────────────────────────────────────────


def is_jxr(file_path: Path) -> bool:
    """Return True if the file is a JPEG XR by magic bytes."""
    try:
        with open(file_path, "rb") as f:
            head = f.read(3)
        return head == JXR_MAGIC
    except OSError:
        return False


def is_hdr_png(file_path: Path) -> bool:
    """Return True if the PNG is HDR (16 bits per channel)."""
    try:
        with Image.open(file_path) as img:
            if img.format != "PNG":
                return False
            # 16-bit PNG modes Pillow uses: "I", "I;16", "I;16B", "RGB" with
            # 16-bit channels (rare). The reliable signal is bit depth.
            depth = img.info.get("bit-depth") or img.info.get("bits")
            if depth and int(depth) > 8:
                return True
            return img.mode in ("I", "I;16", "I;16B", "I;16L")
    except Exception:
        return False


def is_hdr_source(file_path: Path) -> bool:
    """Return True if a file should go through the HDR tone-map pipeline."""
    return is_jxr(file_path) or is_hdr_png(file_path)


# ── Decoding ─────────────────────────────────────────────────────────────────


def decode_jxr(file_path: Path) -> np.ndarray:
    """Decode a JPEG XR file to a float32 RGB array in linear-ish space.

    Returns an HxWx3 array. Values typically span [0, 1] for SDR content
    but may exceed 1.0 for HDR highlights — that's the entire point.
    """
    codecs = _get_imagecodecs()
    data = file_path.read_bytes()
    arr = codecs.jpegxr_decode(data)

    # imagecodecs returns the decoded array in its native dtype:
    # uint8 / uint16 / float16 / float32 depending on the source.
    # Normalize to float32 in roughly [0, ~scrgb_max] range.
    if arr.dtype == np.uint8:
        out = arr.astype(np.float32) / 255.0
    elif arr.dtype == np.uint16:
        out = arr.astype(np.float32) / 65535.0
    elif arr.dtype == np.float16:
        out = arr.astype(np.float32)
    elif arr.dtype == np.float32:
        out = arr
    else:
        out = arr.astype(np.float32)

    # Drop alpha channel if present
    if out.ndim == 3 and out.shape[2] == 4:
        out = out[:, :, :3]
    elif out.ndim == 2:
        # Grayscale → broadcast to RGB
        out = np.stack([out, out, out], axis=-1)

    return out


def decode_hdr_png(file_path: Path) -> np.ndarray:
    """Decode a 16-bit (or higher) PNG to a float32 RGB array.

    Pillow handles 16-bit PNG natively — we read it raw and normalize.
    """
    with Image.open(file_path) as img:
        if img.mode in ("I", "I;16", "I;16B", "I;16L"):
            arr = np.asarray(img, dtype=np.float32) / 65535.0
            return np.stack([arr, arr, arr], axis=-1)

        # 16-bit RGB / RGBA — convert to "I;16" for each channel
        if img.mode == "RGB":
            arr = np.asarray(img, dtype=np.float32)
            # If max value > 255 we have 16-bit range
            if arr.max() > 255.0:
                return arr / 65535.0
            return arr / 255.0

        if img.mode == "RGBA":
            arr = np.asarray(img, dtype=np.float32)
            arr = arr[:, :, :3]
            if arr.max() > 255.0:
                return arr / 65535.0
            return arr / 255.0

        # Fallback: convert to RGB (loses HDR range, but at least decodes)
        rgb = img.convert("RGB")
        return np.asarray(rgb, dtype=np.float32) / 255.0


# ── Tone-mapping ─────────────────────────────────────────────────────────────

ToneMapAlgorithm = Literal["reinhard", "aces", "clip"]


def _reinhard(rgb: np.ndarray, exposure: float = 1.0) -> np.ndarray:
    """Reinhard tone-mapping — soft rolloff into highlights.

    out = (x * exp) / (1 + x * exp)
    """
    x = rgb * exposure
    return x / (1.0 + x)


def _aces(rgb: np.ndarray, exposure: float = 1.0) -> np.ndarray:
    """ACES filmic tone curve (Narkowicz approximation)."""
    x = rgb * exposure * 0.6  # ACES expects pre-exposure scale
    a = 2.51
    b = 0.03
    c = 2.43
    d = 0.59
    e = 0.14
    return np.clip((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0)


def _clip(rgb: np.ndarray, exposure: float = 1.0) -> np.ndarray:
    """Hard clip at 1.0 after exposure adjustment — fastest, blows highlights."""
    return np.clip(rgb * exposure, 0.0, 1.0)


_TONE_MAPPERS = {
    "reinhard": _reinhard,
    "aces": _aces,
    "clip": _clip,
}


def tone_map(
    rgb: np.ndarray,
    algorithm: ToneMapAlgorithm = "reinhard",
    exposure: float = 1.0,
) -> np.ndarray:
    """Apply tone-mapping to an HDR float RGB array. Returns float32 in [0, 1]."""
    mapper = _TONE_MAPPERS.get(algorithm, _reinhard)
    return mapper(rgb, exposure).astype(np.float32)


def to_8bit_srgb(rgb_linear: np.ndarray) -> np.ndarray:
    """Apply sRGB OETF and convert to uint8.

    The tone-mapped output is approximately scene-linear in [0, 1]; encode it
    with the sRGB transfer function so it looks correct on standard displays.
    """
    a = 0.055
    threshold = 0.0031308
    rgb = np.clip(rgb_linear, 0.0, 1.0)
    encoded = np.where(
        rgb <= threshold,
        rgb * 12.92,
        (1.0 + a) * np.power(rgb, 1.0 / 2.4) - a,
    )
    return (encoded * 255.0 + 0.5).astype(np.uint8)


# ── Public API ───────────────────────────────────────────────────────────────


def decode_hdr_to_array(file_path: Path) -> np.ndarray | None:
    """Decode any HDR source (JXR or HDR PNG) to a float32 RGB array.

    Returns None if the file isn't an HDR source we can handle.
    """
    if is_jxr(file_path):
        return decode_jxr(file_path)
    if is_hdr_png(file_path):
        return decode_hdr_png(file_path)
    return None


def render_sdr_pil(
    file_path: Path,
    algorithm: ToneMapAlgorithm = "reinhard",
    exposure: float = 1.0,
) -> Image.Image | None:
    """Decode an HDR source, tone-map, and return a Pillow RGB Image.

    Returns None if the file isn't HDR. Raises on decode/tone-map failure.
    """
    arr = decode_hdr_to_array(file_path)
    if arr is None:
        return None

    mapped = tone_map(arr, algorithm=algorithm, exposure=exposure)
    rgb8 = to_8bit_srgb(mapped)
    return Image.fromarray(rgb8, mode="RGB")


def get_hdr_dimensions(file_path: Path) -> tuple[int, int] | None:
    """Get (width, height) for an HDR source without a full decode if possible."""
    if is_jxr(file_path):
        # imagecodecs has no metadata-only API — decode is the cleanest path
        try:
            arr = decode_jxr(file_path)
            return (arr.shape[1], arr.shape[0])
        except Exception:
            return None
    if is_hdr_png(file_path):
        try:
            with Image.open(file_path) as img:
                return img.size
        except Exception:
            return None
    return None
