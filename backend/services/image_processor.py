"""Image processing: thumbnails, EXIF extraction, hashing.

HDR-aware: JPEG XR (.jxr) and 16-bit-per-channel PNGs are detected and
routed through ``hdr_processor`` for tone-mapped SDR thumbnails. The
original file is always preserved on disk for download.
"""

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

import dhash
from PIL import ExifTags, Image

from backend.config import settings
from backend.services import hdr_processor
from backend.services.performance_diagnostics import measure_sync

logger = logging.getLogger(__name__)


# Pillow's default DecompressionBombWarning fires at ~89 megapixels and
# DecompressionBombError at 2x that. Set an explicit ceiling that's well
# above any reasonable screenshot (8K is ~33 MP) but well below where a
# crafted image could exhaust memory during decode. A 12K x 12K PNG is
# 144 MP — that's our cap.
Image.MAX_IMAGE_PIXELS = 144_000_000


# Map EXIF tag IDs to names — compatible with Pillow 10+ (Base) and older (TAGS)
if hasattr(ExifTags, "Base"):
    EXIF_TAG_NAMES = {tag.value: tag.name for tag in ExifTags.Base}
else:
    EXIF_TAG_NAMES = {v: k for k, v in ExifTags.TAGS.items()}


_HASH_CHUNK_BYTES = 1024 * 1024
_HASH_CACHE_LIMIT = 512
# (resolved path, size, mtime_ns) -> sha256. The import pipelines hash a file and
# then hash it again to name its derivatives; this avoids rereading multi-MB HDR
# files. A changed file has a new size/mtime and misses the cache.
_hash_cache: dict[tuple[str, int, int], str] = {}


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    stat = os.stat(file_path)
    key = (str(Path(file_path).resolve()), stat.st_size, stat.st_mtime_ns)
    cached = _hash_cache.get(key)
    if cached is not None:
        return cached
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_BYTES), b""):
            h.update(chunk)
    digest = h.hexdigest()
    if len(_hash_cache) >= _HASH_CACHE_LIMIT:
        _hash_cache.pop(next(iter(_hash_cache)))
    _hash_cache[key] = digest
    return digest


def compute_sha256_bytes(data: bytes) -> str:
    """Compute SHA256 hash from bytes."""
    return hashlib.sha256(data).hexdigest()


def get_image_dimensions(file_path: Path) -> tuple[int, int] | None:
    """Get image width and height. Handles JXR via hdr_processor."""
    if hdr_processor.is_jxr(file_path):
        return hdr_processor.get_hdr_dimensions(file_path)
    try:
        with Image.open(file_path) as img:
            return img.size  # (width, height)
    except Exception:
        return None


def get_image_format(file_path: Path) -> str | None:
    """Detect actual image format from file content.

    Pillow can't open JXR — we detect it by magic bytes and return ``"jxr"``.
    """
    if hdr_processor.is_jxr(file_path):
        return "jxr"
    try:
        with Image.open(file_path) as img:
            return img.format.lower() if img.format else None
    except Exception:
        return None


def _exif_to_dict(exif_data) -> dict:
    result = {}
    for tag_id, value in exif_data.items():
        tag_name = EXIF_TAG_NAMES.get(tag_id, f"Tag_{tag_id}")
        # Convert to string, skip binary data
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8", errors="replace")
            except Exception:
                continue
        elif isinstance(value, (int, float)):
            value = str(value)
        elif not isinstance(value, str):
            try:
                value = str(value)
            except Exception:
                continue
        result[tag_name] = value
    return result


def _exif_date(exif_data) -> datetime | None:
    # DateTimeOriginal, DateTimeDigitized, then DateTime
    for tag in (0x9003, 0x9004, 0x0132):
        value = exif_data.get(tag)
        if value and isinstance(value, str):
            try:
                return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
            except ValueError:
                continue
    return None


def describe_image(file_path: Path) -> dict:
    """Read dimensions, format, EXIF, capture date and HDR class in one open.

    The pipelines used to open each file separately for every one of these, and
    fully decoded JPEG XR files just to learn their size.
    """
    info = {
        "width": None, "height": None, "format": None,
        "exif": {}, "taken_at": None,
        "is_hdr": hdr_processor.is_hdr_source(file_path),
    }
    if hdr_processor.is_jxr(file_path):
        info["format"] = "jxr"
        dims = hdr_processor.get_hdr_dimensions(file_path)
        if dims:
            info["width"], info["height"] = dims
        return info
    try:
        with Image.open(file_path) as img:
            info["width"], info["height"] = img.size
            info["format"] = img.format.lower() if img.format else None
            exif_data = img.getexif()
            if exif_data:
                info["exif"] = _exif_to_dict(exif_data)
                info["taken_at"] = _exif_date(exif_data)
    except Exception as e:  # noqa: BLE001 - unreadable files are reported by validation
        logger.debug("Could not read image header for %s: %s", file_path, e)
    return info


@measure_sync("image", "analyze", threshold_ms=3000)
def analyze_image(
    file_path: Path,
    game_folder: str,
    filename_stem: str,
) -> dict:
    """Describe an image and build all derivatives in a single worker-thread hop."""
    info = describe_image(file_path)
    info["file_size"] = os.stat(file_path).st_size
    (
        info["thumbnail_path_sm"],
        info["thumbnail_path_md"],
        info["preview_path"],
        info["perceptual_hash"],
    ) = generate_thumbnails_with_hash(file_path, game_folder, filename_stem)
    return info


def extract_exif(file_path: Path) -> dict:
    """Extract EXIF metadata from an image file.

    Returns a dict with human-readable keys and string values. JXR files
    return an empty dict — Pillow can't read their IFD metadata and
    imagecodecs doesn't expose it.
    """
    if hdr_processor.is_jxr(file_path):
        return {}
    try:
        with Image.open(file_path) as img:
            exif_data = img.getexif()
            return _exif_to_dict(exif_data) if exif_data else {}
    except Exception:
        return {}


def extract_date_taken(file_path: Path) -> datetime | None:
    """Try to extract the date a photo was taken from EXIF data.

    Checks DateTimeOriginal, DateTimeDigitized, then DateTime.
    Returns None for JXR (no EXIF accessible).
    """
    if hdr_processor.is_jxr(file_path):
        return None
    try:
        with Image.open(file_path) as img:
            exif_data = img.getexif()
            return _exif_date(exif_data) if exif_data else None
    except Exception:
        return None


# ── Tone-map settings lookup ─────────────────────────────────────────────────
#
# The tone-map algorithm + exposure are read once per thumbnail. A naive
# "open sqlite, SELECT, close" each call became visible during HDR
# imports — a 100-screenshot import generates 200 thumbnails (sm + md)
# and was opening 200 SQLite connections to read two rarely-changing
# values. Now cached at module level and invalidated whenever the
# settings router writes new values.
#
# Concurrency: the cache is a plain ``dict`` mutated under the GIL. The
# generation thread reads it inside ``asyncio.to_thread`` workers; the
# settings router invalidates it on the event loop. Eventual consistency
# is fine — the next thumbnail picks up the new value.


_tone_map_cache: dict[str, str | float] | None = None


def _load_tone_map_settings() -> dict[str, str | float]:
    """Read tone-map settings directly from sqlite. Used on cache miss."""
    import sqlite3

    algorithm: str = "reinhard"
    exposure: float = 1.0
    try:
        conn = sqlite3.connect(str(settings.db_path))
        try:
            cur = conn.execute(
                "SELECT key, value FROM app_settings "
                "WHERE key IN ('tone_map_algorithm', 'tone_map_exposure')"
            )
            for key, value in cur.fetchall():
                if key == "tone_map_algorithm" and value in ("reinhard", "aces", "clip"):
                    algorithm = value
                elif key == "tone_map_exposure":
                    try:
                        exposure = max(0.05, min(8.0, float(value)))
                    except (TypeError, ValueError):
                        pass
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return {"algorithm": algorithm, "exposure": exposure}


def _get_tone_map_settings() -> tuple[str, float]:
    """Cached tone-map settings (algorithm, exposure)."""
    global _tone_map_cache
    if _tone_map_cache is None:
        _tone_map_cache = _load_tone_map_settings()
    return (
        _tone_map_cache["algorithm"],  # type: ignore[return-value]
        _tone_map_cache["exposure"],  # type: ignore[return-value]
    )


def invalidate_tone_map_cache() -> None:
    """Drop the cached tone-map settings.

    Call from the settings router after writing new tone-map preferences
    so the next thumbnail rendered uses the fresh values.
    """
    global _tone_map_cache
    _tone_map_cache = None


# ── Thumbnail generation ─────────────────────────────────────────────────────


def _open_for_thumbnail(source_path: Path, min_width: int | None = None) -> Image.Image | None:
    """Open an image as an SDR Pillow RGB image, tone-mapping HDR sources.

    With ``min_width``, JPEG sources are decoded by libjpeg at the smallest DCT
    scale that is still at least that wide: a 4K Steam JPEG needed for a 1920px
    preview decodes at half size, roughly a quarter of the work.
    """
    if hdr_processor.is_hdr_source(source_path):
        algo, exposure = _get_tone_map_settings()
        try:
            return hdr_processor.render_sdr_pil(source_path, algorithm=algo, exposure=exposure)
        except Exception as e:
            logger.warning("HDR decode failed for %s: %s", source_path, e)
            # Fall through to Pillow — it'll fail on JXR but may handle 16-bit PNG
            try:
                with Image.open(source_path) as img:
                    return img.convert("RGB")
            except Exception:
                return None

    try:
        with Image.open(source_path) as img:
            if min_width and img.format == "JPEG" and img.width > min_width:
                img.draft("RGB", (min_width, -(-img.height * min_width // img.width)))
            if img.mode in ("RGBA", "P", "LA"):
                return img.convert("RGB")
            if img.mode != "RGB":
                return img.convert("RGB")
            return img.copy()
    except Exception:
        return None


def generate_thumbnail(
    source_path: Path,
    dest_path: Path,
    max_width: int,
    quality: int | None = None,
) -> bool:
    """Generate a thumbnail with the specified max width, preserving aspect ratio.

    Routes JXR / 16-bit PNG sources through the HDR tone-mapping pipeline so
    the resulting JPEG is viewable on standard displays.

    Returns True on success, False on failure.
    """
    if quality is None:
        quality = settings.thumbnail_quality

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        img = _open_for_thumbnail(source_path, max_width)
        if img is None:
            return False

        try:
            return _save_thumbnail(img, dest_path, max_width, quality)
        finally:
            img.close()
    except Exception as e:
        logger.warning("Thumbnail generation failed for %s: %s", source_path, e)
        return False


def _save_thumbnail(
    source: Image.Image,
    dest_path: Path,
    max_width: int,
    quality: int,
    subsampling: int = -1,
) -> bool:
    """Resize and save one thumbnail from an already-decoded image.

    ``subsampling=0`` keeps full-resolution chroma (4:4:4); the default lets
    Pillow pick 4:2:0, which is fine for downscaled thumbnails.
    """
    width, height = source.size
    ratio = min(1.0, max_width / width)
    new_size = (int(width * ratio), int(height * ratio))

    image = source.copy()
    try:
        if ratio < 1.0:
            resized = image.resize(new_size, Image.LANCZOS)
            image.close()
            image = resized
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=dest_path.parent, suffix=".tmp")
        os.close(fd)
        try:
            image.save(
                temp_name, "JPEG", quality=quality, optimize=True, subsampling=subsampling
            )
            os.replace(temp_name, dest_path)
        finally:
            Path(temp_name).unlink(missing_ok=True)
        return True
    finally:
        image.close()


def generate_thumbnails(
    source_path: Path,
    game_folder: str,
    filename_stem: str,
) -> tuple[str | None, str | None]:
    """Generate both small (300px) and medium (800px) thumbnails.

    Returns (sm_relative_path, md_relative_path) relative to library root.
    """
    sm, md, _, _ = generate_thumbnails_with_hash(
        source_path, game_folder, filename_stem
    )
    return sm, md


def derivative_filename(source_path: Path) -> str:
    """Versioned, content-addressed cache names keep HDR and SDR files distinct."""
    return f"v2-{compute_sha256(source_path)}.jpg"


@measure_sync("image", "thumbnail_bundle", threshold_ms=2000)
def generate_thumbnails_with_hash(
    source_path: Path,
    game_folder: str,
    filename_stem: str,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Generate thumbnails, a large preview, and perceptual dHash in one decode."""
    from backend.services.filesystem import FULL_RENDER_DIR, get_thumbnails_dir

    thumb_filename = derivative_filename(source_path)

    sm_path = get_thumbnails_dir(game_folder, "300") / thumb_filename
    md_path = get_thumbnails_dir(game_folder, "800") / thumb_filename
    preview_width = settings.preview_max_width
    preview_path = get_thumbnails_dir(game_folder, str(preview_width)) / thumb_filename

    image = _open_for_thumbnail(source_path, max(preview_width, 800))
    if image is None:
        return None, None, None, None
    try:
        row_hash, col_hash = dhash.dhash_row_col(image)
        perceptual_hash = dhash.format_hex(row_hash, col_hash)
        quality = settings.thumbnail_quality
        sm_ok = _save_thumbnail(image, sm_path, 300, quality)
        md_ok = _save_thumbnail(image, md_path, 800, quality)
        preview_ok = _save_thumbnail(image, preview_path, preview_width, quality)
        if needs_browser_render(source_path):
            # The source is already decoded, so the full-size render the viewer
            # shows for JXR/TIFF costs only a JPEG encode here.
            full_path = get_thumbnails_dir(game_folder, FULL_RENDER_DIR) / thumb_filename
            if not _save_thumbnail(
                image, full_path, image.width, FULL_RENDER_QUALITY, subsampling=0
            ):
                logger.warning("Full-size render failed for %s", source_path)
    except Exception as e:
        logger.warning("Thumbnail generation failed for %s: %s", source_path, e)
        sm_ok = md_ok = preview_ok = False
        perceptual_hash = None
    finally:
        image.close()

    sm_rel = f"{game_folder}/thumbnails/300/{thumb_filename}" if sm_ok else None
    md_rel = f"{game_folder}/thumbnails/800/{thumb_filename}" if md_ok else None
    preview_rel = (
        f"{game_folder}/thumbnails/{preview_width}/{thumb_filename}"
        if preview_ok
        else None
    )

    return sm_rel, md_rel, preview_rel, perceptual_hash


@measure_sync("image", "preview", threshold_ms=2000)
def generate_preview(
    source_path: Path,
    game_folder: str,
    filename_stem: str,
) -> str | None:
    """Generate only the disposable large preview for an existing asset."""
    from backend.services.filesystem import get_thumbnails_dir

    preview_width = settings.preview_max_width
    thumb_filename = derivative_filename(source_path)
    preview_path = (
        get_thumbnails_dir(game_folder, str(preview_width))
        / thumb_filename
    )
    if not generate_thumbnail(source_path, preview_path, preview_width):
        return None
    return f"{game_folder}/thumbnails/{preview_width}/{thumb_filename}"


BROWSER_INCOMPATIBLE_SUFFIXES = {".jxr", ".wdp", ".tif", ".tiff"}


def needs_browser_render(source_path: Path) -> bool:
    """True for originals browsers can't display (JPEG XR, TIFF)."""
    return (
        Path(source_path).suffix.lower() in BROWSER_INCOMPATIBLE_SUFFIXES
        or hdr_processor.is_jxr(source_path)
    )


# Full-size renders replace the original in the viewer, so they get more
# quality than grid thumbnails and keep full chroma for HUD text and edges.
FULL_RENDER_QUALITY = 92


@measure_sync("image", "full_render", threshold_ms=2000)
def generate_full_render(source_path: Path, dest_path: Path) -> bool:
    """Write a full-resolution SDR JPEG for a source browsers can't display.

    JPEG XR and TIFF originals can't be shown by browsers, and the bounded
    preview is capped at ``preview_max_width``. This keeps every pixel of the
    original, tone-mapped the same way as the previews.
    """
    image = _open_for_thumbnail(source_path)
    if image is None:
        return False
    try:
        return _save_thumbnail(
            image, dest_path, image.width, FULL_RENDER_QUALITY, subsampling=0
        )
    except Exception as e:
        logger.warning("Full-size render failed for %s: %s", source_path, e)
        return False
    finally:
        image.close()


def compute_perceptual_hash(file_path: Path) -> str | None:
    """Compute the same normalized 128-bit dHash used during import."""
    image = _open_for_thumbnail(file_path, max(settings.preview_max_width, 800))
    if image is None:
        return None
    try:
        row_hash, col_hash = dhash.dhash_row_col(image)
        return dhash.format_hex(row_hash, col_hash)
    except Exception as e:
        logger.warning("Perceptual hash generation failed for %s: %s", file_path, e)
        return None
    finally:
        image.close()


def validate_image(file_path: Path) -> bool:
    """Validate that a file is a supported image (including JXR via hdr_processor)."""
    if hdr_processor.is_jxr(file_path):
        try:
            hdr_processor.decode_jxr(file_path)
            return True
        except Exception:
            return False
    try:
        with Image.open(file_path) as img:
            img.verify()
        return True
    except Exception:
        return False


def process_image(file_path: Path, game_folder: str, filename_stem: str) -> dict:
    """Full image processing pipeline.

    Returns a dict with all extracted/generated metadata:
    - width, height, format, file_size
    - sha256_hash, exif_data (JSON string)
    - taken_at (ISO string or None)
    - thumbnail_path_sm, thumbnail_path_md, preview_path
    """
    sha256 = compute_sha256(file_path)
    info = analyze_image(file_path, game_folder, filename_stem)
    result = {
        "format": info["format"],
        "file_size": info["file_size"],
        "sha256_hash": sha256,
        "exif_data": json.dumps(info["exif"]) if info["exif"] else None,
        "taken_at": info["taken_at"].isoformat() if info["taken_at"] else None,
        "thumbnail_path_sm": info["thumbnail_path_sm"],
        "thumbnail_path_md": info["thumbnail_path_md"],
        "preview_path": info["preview_path"],
        "perceptual_hash": info["perceptual_hash"],
    }
    if info["width"] is not None:
        result["width"], result["height"] = info["width"], info["height"]
    return result
