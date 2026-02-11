import { api, tokenQs } from './client';
import type { Screenshot, Annotation, ShareLink } from '@/lib/types';

export async function listScreenshots(
  gameId: number,
  page = 1,
  limit = 50,
  sort = 'date_desc',
): Promise<{ screenshots: Screenshot[]; total: number; page: number; limit: number; has_more: boolean }> {
  return api.get(`/games/${gameId}/screenshots?page=${page}&limit=${limit}&sort=${sort}`);
}

export async function getScreenshot(id: number): Promise<Screenshot> {
  return api.get(`/screenshots/${id}`);
}

export async function toggleFavorite(id: number): Promise<{ is_favorite: boolean }> {
  return api.post(`/screenshots/${id}/favorite`);
}

export async function getAnnotation(screenshotId: number): Promise<Annotation | null> {
  return api.get(`/screenshots/${screenshotId}/annotation`);
}

export async function saveAnnotation(screenshotId: number, content: string): Promise<Annotation> {
  return api.post(`/screenshots/${screenshotId}/annotation`, { content });
}

export async function deleteAnnotation(screenshotId: number): Promise<void> {
  return api.delete(`/screenshots/${screenshotId}/annotation`);
}

export function getScreenshotImageUrl(id: number): string {
  return `/api/screenshots/${id}/image${tokenQs()}`;
}

export function getThumbnailUrl(id: number, size: 'sm' | 'md'): string {
  return `/api/screenshots/${id}/thumb/${size}${tokenQs()}`;
}

// ── Share link management ─────────────────────────────────────────────

export async function createShareLink(
  screenshotId: number,
  expiresInDays?: number,
): Promise<ShareLink> {
  const params = expiresInDays ? `?expires_in_days=${expiresInDays}` : '';
  return api.post(`/screenshots/${screenshotId}/share${params}`);
}

export async function getShareLink(screenshotId: number): Promise<ShareLink | null> {
  return api.get(`/screenshots/${screenshotId}/share`);
}

export async function deleteShareLink(screenshotId: number): Promise<void> {
  return api.delete(`/screenshots/${screenshotId}/share`);
}
