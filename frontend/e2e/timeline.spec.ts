import { test, expect, type Page } from '@playwright/test';

// Invented data only: the repository is public.
const shot = (id: number, gameId: number, takenAt: string) => ({
  id, filename: `frame-${id}.png`, file_path: `x/frame-${id}.png`, thumbnail_path_sm: null,
  thumbnail_path_md: null, taken_at: takenAt, uploaded_at: takenAt, is_favorite: false,
  width: 1920, height: 1080, game_id: gameId, game_name: gameId === 1 ? 'Lantern Test' : 'Tide Test',
  has_annotation: false, media_version: 'v1',
});

function day(date: string) {
  const a = shot(1, 1, `${date}T21:00:00`);
  const b = shot(2, 1, `${date}T22:30:00`);
  const c = shot(3, 2, `${date}T18:00:00`);
  return {
    date, screenshot_count: 30, games: ['Lantern Test', 'Tide Test'], session_count: 2, session_minutes: 90,
    sessions: [
      { game_id: 1, game_name: 'Lantern Test', accent_color: '#e8833a', started_at: a.taken_at, ended_at: b.taken_at,
        duration_minutes: 90, capture_count: 29, hdr_count: 4, screenshot_ids: [1, 2] },
      { game_id: 2, game_name: 'Tide Test', accent_color: null, started_at: c.taken_at, ended_at: c.taken_at,
        duration_minutes: 0, capture_count: 1, hdr_count: 0, screenshot_ids: [3] },
    ],
    screenshots: [a, b, c],
  };
}

const insights = {
  year: null, years: [2025], range: { start: '2025-01-05', end: '2025-03-10' },
  total_captures: 30, active_days: 3, games: 2, hdr_captures: 4,
  busiest_day: { date: '2025-03-10', captures: 30, game_id: 1, game_name: 'Lantern Test' },
  best_streak: { days: 2, start: '2025-03-09', end: '2025-03-10' },
  current_streak: { days: 0, start: null, end: null },
  session_count: 3,
  longest_session: { game_id: 1, game_name: 'Lantern Test', date: '2025-03-10', duration_minutes: 90, captures: 29 },
  top_games: [
    { game_id: 1, game_name: 'Lantern Test', accent_color: '#e8833a', captures: 29 },
    { game_id: 2, game_name: 'Tide Test', accent_color: null, captures: 1 },
  ],
  other_games: { games: 0, captures: 0 },
  hours: Array.from({ length: 24 }, (_, h) => (h >= 21 ? 10 : 0)),
  heatmap: { start: '2025-01-01', end: '2025-12-31', days: [['2025-03-09', 2], ['2025-03-10', 30]] },
  on_this_day: { date: '2026-03-10', captures: [shot(9, 2, '2025-03-10T09:00:00')] },
};

async function timeline(page: Page, requests: string[]) {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (path.includes('/thumb/')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="9"/>' });
    let body: unknown = {};
    if (path.endsWith('/auth/verify')) body = { authenticated: true, auth_disabled: true, setup_required: false };
    else if (path === '/api/games') body = { games: [] };
    else if (path === '/api/timeline/insights') body = insights;
    else if (path === '/api/timeline') {
      requests.push(url.search);
      body = { days: [day('2025-03-10')], total_days: 1, page: 1, has_more: false };
    }
    await route.fulfill({ json: body });
  });
  await page.goto('/timeline');
}

test('timeline shows statistics, play sessions and jumps to a calendar day', async ({ page }) => {
  const requests: string[] = [];
  await timeline(page, requests);
  const stats = page.getByRole('region', { name: 'Statistics' });
  await expect(stats.getByText('Play sessions')).toBeVisible();
  await expect(stats.getByText('Night owl: 100% of captures after 9 PM.')).toBeVisible();
  await expect(page.getByText('1 h 30 min · 29 captures · 4 HDR')).toBeVisible();
  await expect(page.getByText('1 capture', { exact: true })).toBeVisible();
  await expect(page.getByText('+27')).toBeVisible();
  expect(await page.locator('main').evaluate((m) => m.scrollWidth <= m.clientWidth)).toBe(true);

  await page.getByRole('button', { name: 'Mar 10, 2025, 30 captures' }).click();
  await expect(page.getByText('Showing Mar 10, 2025 and earlier')).toBeVisible();
  await expect.poll(() => requests.some((q) => q.includes('end_date=2025-03-10'))).toBe(true);
  await page.getByRole('button', { name: 'Back to latest' }).click();
  await expect(page.getByText('and earlier')).toHaveCount(0);
});
