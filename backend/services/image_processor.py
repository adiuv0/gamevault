"""Image processing: thumbnails, EXIF extraction, hashing."""

import hashlib
import json
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ExifTags

from backend.config import settings


# Map EXIF tag IDs to names — compatible with Pillow 10+ (Base) and older (TAGS)
if hasattr(ExifTags, "Base"):
    EXIF_TAG_NAMES = {tag.value: tag.name for tag in ExifTags.Base}
else:
    EXIF_TAG_NAMES = {v: k for k, v in ExifTags.TAGS.items()}


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_sha256_bytes(data: bytes) -> str:
    """Compute SHA256 hash from bytes."""
    return hashlib.sha256(data).hexdigest()


def get_image_dimensions(file_path: Path) -> tuple[int, int] | None:
    """Get image width and height."""
    try:
        with Image.open(file_path) as img:
            return img.size  # (width, height)
    except Exception:
        return None


def get_image_format(file_path: Path) -> str | None:
    """Detect actual image format from file content."""
    try:
        with Image.open(file_path) as img:
            return img.format.lower() if img.format else None
    except Exception:
        return None


def extract_exif(file_path: Path) -> dict:
    """Extract EXIF metadata from an image file.

    Returns a dict with human-readable keys and string values.
    """
    result = {}
    try:
        with Image.open(file_path) as img:
            exif_data = img.getexif()
            if not exif_data:
                return result

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
    except Exception:
        pass
    return result


def extract_date_taken(file_path: Path) -> datetime | None:
    """Try to extract the date a photo was taken from EXIF data.

    Checks DateTimeOriginal, DateTimeDigitized, then DateTime.
    """
    try:
        with Image.open(file_path) as img:
            exif_data = img.getexif()
            if not exif_data:
                return None

            # Priority order for date fields
            date_tags = [
                0x9003,  # DateTimeOriginal
                0x9004,  # DateTimeDigitized
                0x0132,  # DateTime
            ]

            for tag in date_tags:
                value = exif_data.get(tag)
                if value and isinstance(value, str):
                    try:
                        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                    except ValueError:
                        continue
    except Exception:
        pass
    return None


def generate_thumbnail(
    source_path: Path,
    dest_path: Path,
    max_width: int,
    quality: int | None = None,
) -> bool:
    """Generate a thumbnail with the specified max width, preserving aspect ratio.

    Returns True on success, False on failure.
    """
    if quality is None:
        quality = settings.thumbnail_quality

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(source_path) as img:
            # Convert to RGB if necessary (e.g., RGBA PNGs, palette images)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")

            # Calculate proportional height
            width, height = img.size
            if width <= max_width:
                # Image is smaller than target, just save as-is
                ratio = 1.0
            else:
                ratio = max_width / width

            new_width = int(width * ratio)
            new_height = int(height * ratio)

            # Use high-quality downsampling
            resized = img.resize((new_width, new_height), Image.LANCZOS)
            resized.save(dest_path, "JPEG", quality=quality, optimize=True)
            return True
    except Exception:
        return False


def generate_thumbnails(
    source_path: Path,
    game_folder: str,
    filename_stem: str,
) -> tuple[str | None, str | None]:
    """Generate both small (300px) and medium (800px) thumbnails.

    Returns (sm_relative_path, md_relative_path) relative to library root.
    """
    from backend.services.filesystem import get_thumbnails_dir

    thumb_filename = f"{filename_stem}.jpg"

    # Small thumbnail (300px) for grid cards
    sm_dir = get_thumbnails_dir(game_folder, "300")
    sm_path = sm_dir / thumb_filename
    sm_ok = generate_thumbnail(source_path, sm_path, 300)

    # Medium thumbnail (800px) for gallery preview
    md_dir = get_thumbnails_dir(game_folder, "800")
    md_path = md_dir / thumb_filename
    md_ok = generate_thumbnail(source_path, md_path, 800)

    sm_rel = f"{game_folder}/thumbnails/300/{thumb_filename}" if sm_ok else None
    md_rel = f"{game_folder}/thumbnails/800/{thumb_filename}" if md_ok else None

    return sm_rel, md_rel


def validate_image(file_path: Path) -> bool:
    """Validate that a file is a supported image by opening it with Pillow."""
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
    - thumbnail_path_sm, thumbnail_path_md
    """
    result = {}

    # Dimensions
    dims = get_image_dimensions(file_path)
    if dims:
        result["width"], result["height"] = dims

    # Format
    result["format"] = get_image_format(file_path)

    # File size
    result["file_size"] = file_path.stat().st_size

    # Hash
    result["sha256_hash"] = compute_sha256(file_path)

    # EXIF
    exif = extract_exif(file_path)
    result["exif_data"] = json.dumps(exif) if exif else None

    # Date taken
    date_taken = extract_date_taken(file_path)
    result["taken_at"] = date_taken.isoformat() if date_taken else None

    # Thumbnails
    sm, md = generate_thumbnails(file_path, game_folder, filename_stem)
    result["thumbnail_path_sm"] = sm
    result["thumbnail_path_md"] = md

    return result
