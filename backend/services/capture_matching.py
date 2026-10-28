"""Shared classification helpers for logical screenshot variants."""

from datetime import datetime
from pathlib import Path

HDR_FORMATS = {"jxr", "wdp"}
HDR_SUFFIXES = {".jxr", ".wdp"}


def is_known_hdr_variant(filename: str, image_format: str | None) -> bool:
    return (image_format or "").casefold() in HDR_FORMATS or (
        Path(filename).suffix.casefold() in HDR_SUFFIXES
    )


def is_complementary_variant_pair(left_is_hdr: bool, right_is_hdr: bool) -> bool:
    return left_is_hdr != right_is_hdr


# Perceptual-hash distance (of 128 bits) at or below which duplicate review calls
# two files a likely match.
LIKELY_HASH_DISTANCE = 4

# One press of a screenshot key can save the same moment several times: Special K
# writes a JXR (and optionally a PNG), Steam a JPG. The copies land a few seconds
# apart and are tone-mapped differently, so their hashes differ a little. On real
# 4K Special K JXR + Steam JPG pairs the distance was 2-8 bits and the recorded
# capture times 1 s apart; separate shots, even 4 s apart, were 40+ bits apart.
# The window stays short so two shots of one dialogue scene, which can hash
# alike, are told apart by time.
VARIANT_HASH_DISTANCE = 12
VARIANT_SECONDS = 5

_FORMAT_ALIASES = {"jpg": "jpeg", "wdp": "jxr", "tif": "tiff"}


def hash_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def same_capture_name(left: str, right: str) -> bool:
    """True for ``shot.jxr`` / ``shot.png``: one capture saved in two formats."""
    return Path(left).stem.casefold() == Path(right).stem.casefold()


def variant_format(filename: str, image_format: str | None) -> str:
    """The file type a variant is saved as: ``jpeg``, ``png``, ``jxr``..."""
    value = (image_format or Path(filename).suffix.lstrip(".")).casefold()
    return _FORMAT_ALIASES.get(value, value)


def seconds_apart(left: str | None, right: str | None) -> float | None:
    if not left or not right:
        return None
    try:
        difference = datetime.fromisoformat(left) - datetime.fromisoformat(right)
    except (TypeError, ValueError):
        return None
    return abs(difference.total_seconds())


def _same_shape(left: dict, right: dict) -> bool:
    lw, lh, rw, rh = left["width"], left["height"], right["width"], right["height"]
    if not (lw and lh and rw and rh):
        return False
    # Same aspect ratio within 1%, so a downscaled copy still matches.
    return abs(lw * rh - rw * lh) <= 0.01 * lw * rh


def variant_match_rank(asset: dict, other: dict) -> tuple[float, int] | None:
    """How well ``other`` fits as another saved copy of ``asset``'s capture.

    Both need ``filename``, ``format``, ``width``, ``height``, ``taken_at`` and
    ``perceptual_hash``. Returns a sort key (closest in time, then most alike), or
    None when they are not the same capture: the same file type, a different
    picture or shape, or capture times more than ``VARIANT_SECONDS`` apart. Without
    a capture time on either side, only the same file name counts.
    """
    if variant_format(asset["filename"], asset.get("format")) == variant_format(
        other["filename"], other.get("format")
    ):
        return None
    if not asset.get("perceptual_hash") or not other.get("perceptual_hash"):
        return None
    if not _same_shape(asset, other):
        return None
    distance = hash_distance(asset["perceptual_hash"], other["perceptual_hash"])
    if distance > VARIANT_HASH_DISTANCE:
        return None
    seconds = seconds_apart(asset.get("taken_at"), other.get("taken_at"))
    if seconds is None:
        if not same_capture_name(asset["filename"], other["filename"]):
            return None
        seconds = float(VARIANT_SECONDS)
    if seconds > VARIANT_SECONDS:
        return None
    return seconds, distance


_CANDIDATE_COLUMNS = """id, COALESCE(capture_group_id, id) AS group_id, filename, format,
    width, height, taken_at, perceptual_hash"""


async def find_variant_group(db, asset: dict) -> int | None:
    """Return the capture group ``asset`` is another saved copy of, if any.

    ``asset`` may be a file about to be inserted (no ``id``) or an existing row,
    possibly already grouped. A group qualifies only when it has no file of the
    same type as any in the asset's own group (one JXR, one PNG, one JPG per
    capture) and none of its files was split from the asset's group by hand.
    """
    if not asset.get("perceptual_hash") or not asset.get("width") or not asset.get("height"):
        return None
    own_group = asset.get("capture_group_id") or asset.get("id")
    if asset.get("taken_at"):
        cursor = await db.execute(
            f"""SELECT {_CANDIDATE_COLUMNS} FROM screenshots
                WHERE game_id = ? AND perceptual_hash IS NOT NULL AND taken_at IS NOT NULL
                  AND ABS(julianday(taken_at) - julianday(?)) * 86400 <= ?
                  AND COALESCE(capture_group_id, id) IS NOT ?
                LIMIT 50""",
            (asset["game_id"], asset["taken_at"], VARIANT_SECONDS, own_group),
        )
    else:
        stem = Path(asset["filename"]).stem
        pattern = stem.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + ".%"
        cursor = await db.execute(
            f"""SELECT {_CANDIDATE_COLUMNS} FROM screenshots
                WHERE game_id = ? AND perceptual_hash IS NOT NULL
                  AND filename LIKE ? ESCAPE '\\'
                  AND COALESCE(capture_group_id, id) IS NOT ?
                LIMIT 50""",
            (asset["game_id"], pattern, own_group),
        )
    columns = [d[0] for d in cursor.description]
    ranked = []
    for row in await cursor.fetchall():
        other = dict(zip(columns, row))
        rank = variant_match_rank(asset, other)
        if rank is not None:
            ranked.append((rank, other["group_id"]))
    if not ranked:
        return None

    own_members = await _group_members(db, asset["game_id"], own_group) if own_group else []
    own_formats = {variant_format(m["filename"], m["format"]) for m in own_members} or {
        variant_format(asset["filename"], asset.get("format"))
    }
    own_ids = [m["id"] for m in own_members]
    for _, group_id in sorted(ranked):
        members = await _group_members(db, asset["game_id"], group_id)
        formats = {variant_format(m["filename"], m["format"]) for m in members}
        if formats & own_formats:
            continue
        if own_ids and await _split_by_hand(db, own_ids, [m["id"] for m in members]):
            continue
        return group_id
    return None


async def _group_members(db, game_id: int, group_id: int) -> list[dict]:
    cursor = await db.execute(
        """SELECT id, filename, format FROM screenshots
           WHERE game_id = ? AND COALESCE(capture_group_id, id) = ?""",
        (game_id, group_id),
    )
    return [{"id": i, "filename": f, "format": fmt} for i, f, fmt in await cursor.fetchall()]


async def _split_by_hand(db, left_ids: list[int], right_ids: list[int]) -> bool:
    """True when the user dismissed or ungrouped any pair across the two groups."""
    left = ",".join("?" * len(left_ids))
    right = ",".join("?" * len(right_ids))
    cursor = await db.execute(
        f"""SELECT 1 FROM duplicate_candidates
            WHERE status = 'dismissed'
              AND ((left_screenshot_id IN ({left}) AND right_screenshot_id IN ({right}))
                OR (left_screenshot_id IN ({right}) AND right_screenshot_id IN ({left})))
            LIMIT 1""",
        (*left_ids, *right_ids, *right_ids, *left_ids),
    )
    return await cursor.fetchone() is not None
