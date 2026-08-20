import { API_BASE } from '@/lib/constants';
import type { Game, PublicScreenshot, ScreenshotPage } from '@/lib/types';

/**
 * Public gallery API client — no auth headers needed.
 */

const GALLERY_BASE = `${API_BASE}/gallery`;

function versionQuery(mediaVersion?: string): string {
  return mediaVersion ? `?v=${encodeURIComponent(mediaVersion)}` : '';
}

async function galleryGet<T>(path: string): Promise<T> {
  const res = await fetch(`${GALLERY_BASE}${path}`);
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || 'Request failed');
  }
  return res.json();
}

export async function galleryListGames(
  sort = 'name',
): Promise<{ games: Game[]; total: number }> {
  return galleryGet(`/games?sort=${sort}`);
}

export async function galleryGetGame(gameId: number): Promise<Game> {
  return galleryGet(`/games/${gameId}`);
}

export async function galleryListScreenshots(
  gameId: number,
  page = 1,
  limit = 50,
  sort = 'date_desc',
): Promise<ScreenshotPage> {
  return galleryGet(`/games/${gameId}/screenshots?page=${page}&limit=${limit}&sort=${sort}`);
}

export async function galleryListScreenshotsCursor(
  gameId: number,
  cursor: string | null = null,
  limit = 50,
  sort = 'date_desc',
): Promise<ScreenshotPage> {
  const params = new URLSearchParams({ limit: String(limit), sort });
  if (cursor) params.set('cursor', cursor);
  return galleryGet(`/games/${gameId}/screenshots?${params}`);
}

export async function galleryGetScreenshot(screenshotId: number): Promise<PublicScreenshot> {
  return galleryGet(`/screenshots/${screenshotId}`);
}

export function galleryCoverUrl(gameId: number): string {
  return `${GALLERY_BASE}/games/${gameId}/cover`;
}

export function galleryImageUrl(screenshotId: number, mediaVersion?: string): string {
  return `${GALLERY_BASE}/screenshots/${screenshotId}/image${versionQuery(mediaVersion)}`;
}

export function galleryPreviewUrl(screenshotId: number, mediaVersion?: string): string {
  return `${GALLERY_BASE}/screenshots/${screenshotId}/preview${versionQuery(mediaVersion)}`;
}

export function galleryThumbUrl(screenshotId: number, size: 'sm' | 'md', mediaVersion?: string): string {
  return `${GALLERY_BASE}/screenshots/${screenshotId}/thumb/${size}${versionQuery(mediaVersion)}`;
}
