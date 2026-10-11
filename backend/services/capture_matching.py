"""Shared classification helpers for logical screenshot variants."""

from pathlib import Path

HDR_FORMATS = {"jxr", "wdp"}
HDR_SUFFIXES = {".jxr", ".wdp"}


def is_known_hdr_variant(filename: str, image_format: str | None) -> bool:
    return (image_format or "").casefold() in HDR_FORMATS or (
        Path(filename).suffix.casefold() in HDR_SUFFIXES
    )


def is_complementary_variant_pair(left_is_hdr: bool, right_is_hdr: bool) -> bool:
    return left_is_hdr != right_is_hdr


# Perceptual-hash distance (of 128 bits) at or below which two files are treated
# as the same image. Shared by duplicate review and same-name auto-pairing.
LIKELY_HASH_DISTANCE = 4


def hash_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def same_capture_name(left: str, right: str) -> bool:
    """True for ``shot.jxr`` / ``shot.png``: one capture saved in two formats."""
    return Path(left).stem.casefold() == Path(right).stem.casefold()
