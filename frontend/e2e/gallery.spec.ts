import { test, expect, type Page } from '@playwright/test';

const stamp = '2026-01-02T03:04:05';
const game = { id: 1, name: 'Regression Gallery', folder_name: 'regression', screenshot_count: 180, is_public: true };
const items = Array.from({ length: 180 }, (_, i) => ({
  id: i + 1, game_id: 1, filename: `frame-${i + 1}.png`, media_version: 'v1',
  uploaded_at: stamp, taken_at: stamp, variant_count: i === 0 ? 2 : 1,
  has_annotation: false, is_favorite: false, format: 'png', width: 1920, height: 1080,
}));
const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAKAAAABaCAIAAACwpMoFAAABCElEQVR4nO3cwQmEUAxAwa9sK7JnexD2bqG2YgE2s0WICMObeyDwyDXT97eNuOa3F8izCowrMK7AuALjCowrMK7AuALjCowrMK7AuALjCowrMK7AuALjCowrMK7AuALjCowrMO5zZ/hY1sHZr3NAumBcgXEFxhUYV2BcgXEFxhUYV2BcgXEFxhUYV2BcgXEFxhUYV2BcgXEFxhUYV2BcgXEFxhUYV2BcgXEFxhUYV2BcgXEFxhUYV2BcgXEFxhUYV2BcgXEFxhUYV2BcgXEFxhUYV2BcgXEFxhUYV2BcgXEFxhUYV2BcgXG3Xvpj3+9JXTCuwLgC4wqMKzCuwLgC4wqMKzCuwLgC4wqMKzCuwLgC4wqMKzCuwLgC4wqMKzCuwMP2B6/VBmb5upeMAAAAAElFTkSuQmCC', 'base64');

async function gallery(page: Page) {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (/\/(thumb|preview|image|cover)(\/|$)/.test(path)) {
      return route.fulfill({ contentType: 'image/png', body: pixel });
    }
    let body: unknown = {};
    if (path.endsWith('/auth/verify')) body = { authenticated: true, auth_disabled: true, setup_required: false };
    else if (path === '/api/games/1') body = game;
    else if (path === '/api/games/1/screenshots') {
      const start = Number(url.searchParams.get('cursor') || 0);
      const ordered = url.searchParams.get('sort') === 'date_asc' ? [...items].reverse() : items;
      body = { screenshots: ordered.slice(start, start + 50), total: items.length,
        next_cursor: start + 50 < items.length ? String(start + 50) : null };
    } else if (/\/screenshots\/\d+\/annotation$/.test(path)) {
      if (route.request().method() === 'POST') return route.fulfill({ status: 500, json: { detail: 'Save failed' } });
      body = null;
    } else if (/\/screenshots\/\d+$/.test(path)) {
      const item = items[Number(path.split('/').pop()) - 1];
      body = { ...item, variants: item.id === 1 ? [item, { ...item, id: 999, format: 'jxr', filename: 'frame-1.jxr' }] : [item] };
    }
    await route.fulfill({ json: body });
  });
  await page.goto('/games/1');
  await expect(page.getByRole('heading', { name: game.name })).toBeVisible();
  expect(await page.locator('main').evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
}

test('annotation typing, pane toggles, navigation and failed save preserve drafts', async ({ page }) => {
  await gallery(page);
  await page.getByRole('img', { name: 'frame-1.png', exact: true }).click();
  await page.getByTitle('Toggle info (i)').click();
  await page.getByRole('button', { name: 'Add annotation...' }).click();
  const editor = page.getByPlaceholder('Write your annotation... (supports Markdown)');
  await editor.fill('This is a high quality screenshot');
  await editor.press('i');
  await editor.press('h');
  await editor.press('ArrowLeft');
  await editor.press('Escape');
  const draft = await editor.inputValue();
  await expect(editor).toBeVisible();
  await page.getByTitle('Toggle info (i)').click();
  await page.getByTitle('Toggle info (i)').click();
  await expect(editor).toHaveValue(draft);
  await page.getByTitle('Toggle info (i)').click();
  await page.locator('body').click({ position: { x: 5, y: 5 } });
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowLeft');
  await page.getByTitle('Toggle info (i)').click();
  await expect(editor).toHaveValue(draft);
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('draft has been kept');
  await expect(editor).toHaveValue(draft);
});

test('loaded rows remain visible after scrolling back and sorting resets the cursor', async ({ page }, testInfo) => {
  await gallery(page);
  for (let i = 0; i < 7; i++) {
    await page.locator('main').evaluate((element) => { element.scrollTop = element.scrollHeight; });
    await page.waitForTimeout(180);
  }
  await expect(page.getByRole('img', { name: 'frame-180.png', exact: true })).toBeVisible();
  await page.locator('main').evaluate((element) => { element.scrollTop = 0; });
  await expect(page.getByRole('img', { name: 'frame-1.png', exact: true })).toBeVisible();
  await page.getByLabel('Sort screenshots').selectOption('date_asc');
  await expect(page.getByRole('img', { name: 'frame-180.png', exact: true })).toBeVisible();
  await expect.poll(() => page.getByRole('img', { name: 'frame-180.png', exact: true }).evaluate((image) => (image as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await page.screenshot({ path: testInfo.outputPath('gallery.png'), fullPage: true });
});

test('ungroup applies to the selected HDR variant', async ({ page }) => {
  await gallery(page);
  await page.getByRole('img', { name: 'frame-1.png', exact: true }).click();
  await page.getByRole('button', { name: 'JXR', exact: true }).click();
  const request = page.waitForRequest((request) => request.url().endsWith('/screenshots/999/ungroup') && request.method() === 'POST');
  await page.getByTitle('Ungroup selected variant').click();
  await request;
  await expect(page.getByTitle('Close (Esc)')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: game.name })).toBeVisible();
});
