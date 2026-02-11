"""Full-text search service using FTS5 with BM25 ranking."""

from backend.database import get_db


async def search_screenshots(
    query: str,
    game_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    favorites_only: bool = False,
    sort: str = "relevance",
    page: int = 1,
    limit: int = 50,
) -> tuple[list[dict], int]:
    """Search screenshots using FTS5 full-text search with BM25 ranking.

    BM25 weights: annotation_content(10.0), game_name(5.0),
                  steam_description(3.0), filename(1.0)

    Returns (results_list, total_count).
    """
    db = await get_db()

    if not query.strip():
        # Empty query — return all screenshots with optional filters
        return await _list_all_filtered(
            db, game_id, date_from, date_to, favorites_only, sort, page, limit
        )

    # Sanitize FTS5 query: escape special chars and append wildcard for partial matches
    fts_query = _build_fts_query(query)

    # Base query: join FTS results with screenshots + games
    # BM25 column order: game_name, filename, steam_description, annotation_content
    # Weights: higher = more important (negative values for BM25)
    base_query = """
        FROM screenshots_fts fts
        JOIN screenshots s ON s.id = fts.rowid
        JOIN games g ON g.id = s.game_id
        LEFT JOIN annotations a ON a.screenshot_id = s.id
        WHERE screenshots_fts MATCH ?
    """
    params: list = [fts_query]

    # Apply filters
    if game_id is not None:
        base_query += " AND s.game_id = ?"
        params.append(game_id)
    if date_from:
        base_query += " AND (s.taken_at >= ? OR s.uploaded_at >= ?)"
        params.extend([date_from, date_from])
    if date_to:
        base_query += " AND (s.taken_at <= ? OR s.uploaded_at <= ?)"
        params.extend([date_to, date_to])
    if favorites_only:
        base_query += " AND s.is_favorite = 1"

    # Count total
    count_cursor = await db.execute(
        f"SELECT COUNT(*) {base_query}", params
    )
    total = (await count_cursor.fetchone())[0]

    # Sort
    if sort == "date_desc":
        order = "COALESCE(s.taken_at, s.uploaded_at) DESC"
    elif sort == "date_asc":
        order = "COALESCE(s.taken_at, s.uploaded_at) ASC"
    else:
        # relevance — BM25 with weights: game_name=5, filename=1, steam_desc=3, annotation=10
        order = "bm25(screenshots_fts, 5.0, 1.0, 3.0, 10.0)"

    offset = (page - 1) * limit
    results_cursor = await db.execute(
        f"""SELECT
            s.id as screenshot_id,
            s.game_id,
            g.name as game_name,
            s.filename,
            s.file_path,
            s.thumbnail_path_sm,
            s.thumbnail_path_md,
            s.taken_at,
            s.uploaded_at,
            s.is_favorite,
            s.width,
            s.height,
            s.file_size,
            EXISTS(SELECT 1 FROM annotations ann WHERE ann.screenshot_id = s.id) as has_annotation,
            a.content as annotation_content,
            bm25(screenshots_fts, 5.0, 1.0, 3.0, 10.0) as relevance_score
        {base_query}
        ORDER BY {order}
        LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )
    columns = [desc[0] for desc in results_cursor.description]
    rows = await results_cursor.fetchall()
    results = [dict(zip(columns, row)) for row in rows]

    # Truncate annotation to preview
    for r in results:
        if r.get("annotation_content"):
            r["annotation_preview"] = _truncate(r["annotation_content"], 150)
        else:
            r["annotation_preview"] = None
        r.pop("annotation_content", None)

    return results, total


async def _list_all_filtered(
    db,
    game_id: int | None,
    date_from: str | None,
    date_to: str | None,
    favorites_only: bool,
    sort: str,
    page: int,
    limit: int,
) -> tuple[list[dict], int]:
    """List all screenshots with filters (no FTS query)."""
    base_query = """
        FROM screenshots s
        JOIN games g ON g.id = s.game_id
        LEFT JOIN annotations a ON a.screenshot_id = s.id
        WHERE 1=1
    """
    params: list = []

    if game_id is not None:
        base_query += " AND s.game_id = ?"
        params.append(game_id)
    if date_from:
        base_query += " AND (s.taken_at >= ? OR s.uploaded_at >= ?)"
        params.extend([date_from, date_from])
    if date_to:
        base_query += " AND (s.taken_at <= ? OR s.uploaded_at <= ?)"
        params.extend([date_to, date_to])
    if favorites_only:
        base_query += " AND s.is_favorite = 1"

    count_cursor = await db.execute(f"SELECT COUNT(*) {base_query}", params)
    total = (await count_cursor.fetchone())[0]

    if sort == "date_asc":
        order = "COALESCE(s.taken_at, s.uploaded_at) ASC"
    else:
        order = "COALESCE(s.taken_at, s.uploaded_at) DESC"

    offset = (page - 1) * limit
    results_cursor = await db.execute(
        f"""SELECT
            s.id as screenshot_id,
            s.game_id,
            g.name as game_name,
            s.filename,
            s.file_path,
            s.thumbnail_path_sm,
            s.thumbnail_path_md,
            s.taken_at,
            s.uploaded_at,
            s.is_favorite,
            s.width,
            s.height,
            s.file_size,
            EXISTS(SELECT 1 FROM annotations ann WHERE ann.screenshot_id = s.id) as has_annotation,
            a.content as annotation_content,
            0.0 as relevance_score
        {base_query}
        ORDER BY {order}
        LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )
    columns = [desc[0] for desc in results_cursor.description]
    rows = await results_cursor.fetchall()
    results = [dict(zip(columns, row)) for row in rows]

    for r in results:
        if r.get("annotation_content"):
            r["annotation_preview"] = _truncate(r["annotation_content"], 150)
        else:
            r["annotation_preview"] = None
        r.pop("annotation_content", None)

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
