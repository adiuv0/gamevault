"""Browser cache policy helpers for screenshot media."""

import hashlib

PRIVATE_MEDIA_CACHE = "private, max-age=86400"
PRIVATE_VERSIONED_MEDIA_CACHE = "private, max-age=31536000, immutable"
PUBLIC_MEDIA_CACHE = "public, max-age=86400"


def media_version(updated_at: str) -> str:
    """Return a short opaque revision for a screenshot's media URLs."""
    return hashlib.blake2s(updated_at.encode("utf-8"), digest_size=8).hexdigest()


def media_response_headers(
    *,
    public: bool,
    requested_version: str | None,
    current_version: str,
) -> dict[str, str]:
    """Build secure media headers with immutable caching for matching private URLs."""
    if public:
        cache_control = PUBLIC_MEDIA_CACHE
    elif requested_version == current_version:
        cache_control = PRIVATE_VERSIONED_MEDIA_CACHE
    else:
        cache_control = PRIVATE_MEDIA_CACHE

    return {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'",
        "Cache-Control": cache_control,
    }
