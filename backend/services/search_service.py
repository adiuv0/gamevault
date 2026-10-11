"""Full-text search across logical captures, including every file variant."""

from backend.database import get_db
from backend.services.media_cache_service import media_version

SEARCH_SORT_CLAUSES = {
    "relevance": "bm25_score ASC, s.id ASC",
    "date_desc": "COALESCE(s.taken_at, s.uploaded_at) DESC, s.id DESC",
    "date_asc": "COALESCE(s.taken_at, s.uploaded_at) ASC, s.id ASC",
}
SEARCH_LIST_ALL_SORT_CLAUSES = {key: SEARCH_SORT_CLAUSES[key] for key in ("date_desc", "date_asc")}


async def search_screenshots(
    query: str, game_id: int | None = None, date_from: str | None = None,
    date_to: str | None = None, favorites_only: bool = False, sort: str = "relevance",
    page: int = 1, limit: int = 50,
) -> tuple[list[dict], int]:
    db = await get_db()
    params = []
    prefix = ""
    hits_join = ""
    score = "0.0"
    if query.strip():
        # Materialize bm25 before aggregation: SQLite's FTS auxiliary functions
        # must run directly against the MATCH cursor.
        prefix = """WITH matched AS MATERIALIZED (
            SELECT m.game_id, COALESCE(m.capture_group_id, m.id) AS capture_id,
                   bm25(screenshots_fts, 5.0, 1.0, 3.0, 10.0) AS score
            FROM screenshots_fts JOIN screenshots m ON m.id = screenshots_fts.rowid
            WHERE screenshots_fts MATCH ?
        ), hits AS (
            SELECT game_id, capture_id, MIN(score) AS bm25_score
            FROM matched GROUP BY game_id, capture_id
        )"""
        params.append(_build_fts_query(query))
        hits_join = """JOIN hits h ON h.game_id = s.game_id
                       AND h.capture_id = COALESCE(s.capture_group_id, s.id)"""
        score = "h.bm25_score"
    base = f"""FROM logical_captures s JOIN games g ON g.id = s.game_id
               {hits_join} WHERE 1=1"""
    if game_id is not None:
        base += " AND s.game_id = ?"
        params.append(game_id)
    if date_from:
        base += " AND COALESCE(s.taken_at, s.uploaded_at) >= ?"
        params.append(date_from)
    if date_to:
        base += " AND COALESCE(s.taken_at, s.uploaded_at) <= ?"
        params.append(date_to)
    if favorites_only:
        base += " AND s.capture_favorite = 1"
    cursor = await db.execute(f"{prefix} SELECT COUNT(*) {base}", params)
    total = (await cursor.fetchone())[0]
    sorts = SEARCH_SORT_CLAUSES if query.strip() else SEARCH_LIST_ALL_SORT_CLAUSES
    order = sorts.get(sort, sorts.get("relevance", SEARCH_LIST_ALL_SORT_CLAUSES["date_desc"]))
    page_order = order.replace("s.id", "page.screenshot_id").replace("s.", "page.")
    # Annotation text is joined only for the visible page, never for the count.
    cursor = await db.execute(f"""{prefix} SELECT page.*, a.content AS annotation_content
        FROM (SELECT
            s.id AS screenshot_id, s.game_id, g.name AS game_name, s.filename, s.file_path,
            s.thumbnail_path_sm, s.thumbnail_path_md, s.taken_at, s.uploaded_at,
            s.updated_at AS media_updated_at,
            s.capture_favorite AS is_favorite, s.width, s.height, s.file_size,
            s.annotation_id, {score} AS bm25_score, {score} AS relevance_score
            {base} ORDER BY {order} LIMIT ? OFFSET ?) AS page
        LEFT JOIN annotations a ON a.id = page.annotation_id
        ORDER BY {page_order}""", params + [limit, (page - 1) * limit])
    results = [dict(zip((column[0] for column in cursor.description), row)) for row in await cursor.fetchall()]
    for result in results:
        result["has_annotation"] = result.pop("annotation_id") is not None
        result["media_version"] = media_version(result.pop("media_updated_at"))
        content = result.pop("annotation_content")
        result["annotation_preview"] = _truncate(content, 150) if content else None
        result.pop("bm25_score")
    return results, total


def _build_fts_query(query: str) -> str:
    """Build a safe FTS5 query string from user input.

    Escapes special FTS5 characters and creates a query that matches
    each word as a prefix for partial matching. Splits on hyphens
    since the porter unicode61 tokenizer treats them as separators.
    """
    import re

    cleaned = query.strip()
    if not cleaned:
        return '""'

    # Remove FTS5 special characters
    cleaned = re.sub(r'["\'\*\(\)\:\^\{\}]', ' ', cleaned)
    # Split on whitespace and hyphens (tokenizer treats hyphens as separators)
    words = re.split(r'[\s\-]+', cleaned)

    parts = []
    for word in words:
        word = word.strip()
        if word:
            parts.append(f'"{word}"*')

    if not parts:
        return '""'

    return " ".join(parts)


def _truncate(text: str, max_length: int) -> str:
    """Truncate text to max_length, adding ellipsis if needed."""
    if len(text) <= max_length:
        return text
    return text[:max_length].rsplit(" ", 1)[0] + "..."
