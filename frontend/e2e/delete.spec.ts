import { test, expect, type Page } from '@playwright/test';

const stamp = '2026-01-02T03:04:05';
const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC', 'base64');

/** A game of 6 captures; capture 1 is a JPG grouped with a JXR (id 99). */
async function library(page: Page) {
  let items = Array.from({ length: 6 }, (_, i) => ({
    id: i + 1, game_id: 1, filename: `frame-${i + 1}.jpg`, media_version: 'v1',
    uploaded_at: stamp, taken_at: stamp, variant_count: i === 0 ? 2 : 1,
    has_annotation: false, is_favorite: false, format: 'jpg', width: 1920, height: 1080,
  }));
  const jxr = { ...items[0], id: 99, filename: 'frame-1.jxr', format: 'jxr' };
  let jxrOnly = false;
  const deletes: string[] = [];

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (/\/(thumb|preview|image|full|cover)(\/|$)/.test(path)) {
      return route.fulfill({ contentType: 'image/png', body: pixel });
    }
    let body: unknown = {};
    if (path.endsWith('/auth/verify')) body = { authenticated: true, auth_disabled: true, setup_required: false };
    else if (path === '/api/games/1') {
      body = { id: 1, name: 'Delete Test', folder_name: 'delete', screenshot_count: items.length, is_public: false };
    } else if (path === '/api/games/1/screenshots') {
      body = { screenshots: items, total: items.length, next_cursor: null };
    } else if (path === '/api/screenshots/delete' && request.method() === 'POST') {
      const ids: number[] = request.postDataJSON().ids;
      deletes.push(`bulk:${[...ids].sort().join(',')}`);
      items = items.filter((item) => !ids.includes(item.id));
      body = { deleted_files: ids.length, deleted_captures: ids.length, removed_from_disk: ids.length };
    } else if (/\/screenshots\/\d+$/.test(path) && request.method() === 'DELETE') {
      const id = Number(path.split('/').pop());
      const variant = url.searchParams.get('variant') === 'true';
      deletes.push(`${id}${variant ? ':variant' : ''}`);
      if (variant && id === 1) {
        // The JPG went; the JXR now stands for the capture.
        jxrOnly = true;
        items = items.map((item) => (item.id === 1 ? { ...jxr, variant_count: 1 } : item));
        body = { deleted_files: 1, deleted_captures: 0, removed_from_disk: 4, remaining_id: 99 };
      } else {
        items = items.filter((item) => item.id !== id);
        body = { deleted_files: 1, deleted_captures: 1, removed_from_disk: 4 };
      }
    } else if (/\/screenshots\/\d+$/.test(path)) {
      const id = Number(path.split('/').pop());
      if (id === 99 || (id === 1 && jxrOnly)) body = { ...jxr, variant_count: 1, variants: [jxr] };
      else {
        const item = items.find((entry) => entry.id === id)!;
        body = { ...item, variants: id === 1 ? [item, jxr] : [item] };
      }
    } else if (/\/screenshots\/\d+\/(annotation|share)$/.test(path)) body = null;
    await route.fulfill({ json: body });
  });
  await page.goto('/games/1');
  await expect(page.getByRole('heading', { name: 'Delete Test' })).toBeVisible();
  return deletes;
}

test('the viewer deletes one file of a grouped capture, then a whole capture', async ({ page }) => {
  const deletes = await library(page);
  await page.getByRole('img', { name: 'frame-1.jpg', exact: true }).click();

  // Grouped capture: choose between the shown file and every file.
  await page.getByRole('button', { name: 'Delete screenshot' }).click();
  const dialog = page.getByRole('dialog', { name: 'Delete this capture?' });
  await expect(dialog).toContainText('2 files: JPG, JXR');
  await dialog.getByRole('button', { name: 'Only the JPG file' }).click();
  await expect(dialog).toHaveCount(0);
  expect(deletes).toEqual(['1:variant']);
  // Still on the same capture, now shown by its JXR.
  await expect(page.getByText('1 / 6')).toBeVisible();
  await expect(page.getByText('frame-1.jxr').first()).toBeVisible();

  // Escape closes the dialog but not the viewer.
  await page.keyboard.press('ArrowRight');
  await expect(page.getByText('2 / 6')).toBeVisible();
  // Delete waits for the capture's file list, so the dialog can say what goes.
  await expect(page.getByRole('button', { name: 'Delete screenshot' })).toBeEnabled();
  await page.keyboard.press('Delete');
  const single = page.getByRole('dialog', { name: 'Delete this screenshot?' });
  await expect(single).toContainText('frame-2.jpg');
  await page.keyboard.press('Escape');
  await expect(single).toHaveCount(0);
  await expect(page.getByText('2 / 6')).toBeVisible();

  // Enter confirms; the next capture slides into place.
  await page.keyboard.press('Delete');
  await page.keyboard.press('Enter');
  await expect(page.getByText('2 / 5')).toBeVisible();
  await expect(page.getByText('frame-3.jpg').first()).toBeVisible();
  expect(deletes).toEqual(['1:variant', '2']);
});

test('multi-select deletes a shift-clicked range from the grid', async ({ page }, testInfo) => {
  const deletes = await library(page);
  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('img', { name: 'frame-2.jpg', exact: true }).click();
  await page.getByRole('img', { name: 'frame-4.jpg', exact: true }).click({ modifiers: ['Shift'] });
  await expect(page.getByText('3 selected')).toBeVisible();
  // Selecting doesn't open the viewer.
  await expect(page.getByTitle('Close (Esc)')).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('selecting.png') });

  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Delete 3 captures?' });
  await dialog.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(deletes).toEqual(['bulk:2,3,4']);
  await expect(page.getByRole('img', { name: 'frame-3.jpg', exact: true })).toHaveCount(0);
  await expect(page.getByRole('img', { name: 'frame-5.jpg', exact: true })).toBeVisible();
  await expect(page.getByText('selected')).toHaveCount(0);
});
