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
