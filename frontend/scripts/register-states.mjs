import { chromium } from 'playwright';
import axe from 'axe-core';
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
const output = '../backend/artifacts/register-review';
const browser = await chromium.launch();
const payload = await (
  await fetch('http://127.0.0.1:8017/api/findings')
).json();
const metrics = await (await fetch('http://127.0.0.1:8017/api/metrics')).json();
const results = [];
const envelope = (data) => JSON.stringify({ data, error: null });
async function newPage({
  rows = payload.data,
  health = metrics.data,
  delay = 0,
} = {}) {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  await page.route('**/api/findings', async (route) => {
    if (delay) await new Promise((r) => setTimeout(r, delay));
    await route.fulfill({
      contentType: 'application/json',
      body: envelope(rows),
    });
  });
  await page.route('**/api/metrics', (route) =>
    route.fulfill({ contentType: 'application/json', body: envelope(health) }),
  );
  await page.addInitScript(() => {
    window.shifts = [];
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries())
        if (!entry.hadRecentInput) window.shifts.push(entry.value);
    }).observe({ type: 'layout-shift', buffered: true });
  });
  await page.goto('http://127.0.0.1:5177/?profile');
  return page;
}
async function ready(page) {
  await page.locator('.register-table-container[aria-busy="false"]').waitFor();
  await page.evaluate(() => document.fonts.ready);
}
async function checkAxe(page, name) {
  await page.evaluate(axe.source);
  const violations = await page.evaluate(async () =>
    (await window.axe.run()).violations.map(({ id, nodes }) => ({
      id,
      nodes: nodes.map((n) => ({
        target: n.target,
        failure: n.failureSummary,
      })),
    })),
  );
  results.push({ name, axe: violations });
  assert.deepEqual(violations, [], `${name}: axe`);
}
// Loading and layout stability.
let page = await newPage({ delay: 1200 });
await page.locator('.register-skeleton-row').first().waitFor();
assert.equal(await page.locator('.register-skeleton-row').count(), 12);
await page.screenshot({ path: `${output}/dark-loading.png` });
await ready(page);
const cls = await page.evaluate(() => window.shifts.reduce((a, b) => a + b, 0));
assert(cls < 0.1, `CLS ${cls}`);
results.push({ name: 'loading', cls });
// Independent filters, search shortcut, group persistence, keyboard sorting.
await page.locator('[data-identity="sam.rivera"]').click();
await page
  .locator('[data-identity="sam.rivera"][aria-expanded="false"]')
  .waitFor();
await page
  .getByRole('combobox', { name: 'Filter by tier', exact: true })
  .selectOption('T1');
await page.getByText('4 findings · 4 identities', { exact: true }).waitFor();
await page
  .getByRole('combobox', { name: 'Filter by tier', exact: true })
  .selectOption('all');
await page
  .locator('[data-identity="sam.rivera"][aria-expanded="false"]')
  .waitFor();
await page.keyboard.press('Control+k');
assert.equal(
  await page
    .getByRole('searchbox', { name: 'Search findings' })
    .evaluate((el) => el === document.activeElement),
  true,
);
await page.getByRole('searchbox').fill('does not exist');
await page
  .getByRole('heading', { name: 'No findings match “does not exist”' })
  .waitFor();
await page.screenshot({ path: `${output}/dark-filtered-empty.png` });
await checkAxe(page, 'filtered empty');
await page.getByRole('button', { name: 'Clear filters' }).click();
await page.getByRole('button', { name: 'Risk', exact: true }).focus();
await page.keyboard.press('Enter');
await page
  .locator('th[aria-sort="ascending"]')
  .filter({ hasText: 'Risk' })
  .waitFor();
await page.getByRole('button', { name: 'Risk', exact: true }).click();
// Disabled row cannot open. Rows focus and dialog Escape/outside/focus trap.
assert.equal(
  await page
    .getByRole('button', { name: 'Review FIND-012 for devon.reed' })
    .isDisabled(),
  true,
);
const row = page.locator('[data-finding-id="FIND-002"]');
await row.focus();
await page.keyboard.press('Enter');
await page.locator('.plan-actions').waitFor();
assert.equal(
  await page
    .getByRole('button', { name: 'Close finding details' })
    .evaluate((el) => el === document.activeElement),
  true,
);
for (let i = 0; i < 14; i++) {
  await page.keyboard.press('Tab');
  assert.equal(
    await page.evaluate(() => !!document.activeElement?.closest('dialog')),
    true,
  );
}
await checkAxe(page, 'dark dialog');
await page.keyboard.press('Escape');
await page.locator('dialog').waitFor({ state: 'detached' });
assert.equal(await row.evaluate((el) => el === document.activeElement), true);
await row.click();
await page.locator('.plan-actions').waitFor();
await page.mouse.click(300, 120);
await page.locator('dialog').waitFor({ state: 'detached' });
// Undo does not send a mutation; success is visible inside modal; failed writes recover.
let writes = 0;
let fail = false;
await page.route('**/api/findings/*/decision', async (route) => {
  writes++;
  if (fail)
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        data: null,
        error: { code: 'NETWORK_ERROR', message: 'Provider unavailable' },
      }),
    });
  else
    await route.fulfill({
      contentType: 'application/json',
      body: envelope({
        ...payload.data[1],
        current_stage: 'Verified',
        stage_status: 'passed',
      }),
    });
});
await row.click();
await page.locator('.plan-actions').waitFor();
await page.getByRole('button', { name: 'Approve plan', exact: true }).click();
await page
  .locator('dialog')
  .getByRole('button', { name: 'Undo', exact: true })
  .click();
await page.waitForTimeout(8200);
assert.equal(writes, 0);
await page.getByRole('button', { name: 'Approve plan', exact: true }).click();
await page
  .locator('dialog')
  .getByText('Approve plan recorded for FIND-002.', { exact: true })
  .waitFor({ timeout: 12000 });
assert.equal(writes, 1);
await page.screenshot({ path: `${output}/dark-action-success.png` });
fail = true;
await page.getByRole('button', { name: 'Defer 30 days', exact: true }).click();
await page
  .locator('dialog')
  .getByText(/Defer 30 days was not recorded/)
  .waitFor({ timeout: 12000 });
assert.equal(writes, 2);
results.push({
  name: 'interactions',
  filters: true,
  collapsePersistence: true,
  keyboardSort: true,
  focusTrap: true,
  focusReturn: true,
  outsideClose: true,
  undoZeroWrites: true,
  successAndFailureFeedback: true,
});
await page.close();
// No-events and not-wired values must stay distinct, no bare numeric zeros.
page = await newPage({
  rows: [],
  health: {
    ...metrics.data,
    counts: {
      planted: 0,
      detected: 0,
      executed: 0,
      rollback_success: 0,
      revocations: 0,
    },
  },
});
await ready(page);
await page.getByRole('heading', { name: 'Queue is clear.' }).waitFor();
assert.equal(await page.locator('.metric-no-events').count(), 3);
assert.equal(await page.locator('.metric-not-wired').count(), 2);
await page.screenshot({ path: `${output}/dark-true-empty.png` });
await checkAxe(page, 'true empty');
await page.close();
// Stale uses source time; failed refresh preserves and explicitly dates prior data.
page = await newPage({
  rows: payload.data.map((row) => ({
    ...row,
    captured_at: new Date(Date.now() - 3600000).toISOString(),
  })),
});
await ready(page);
await page.locator('.capture-status').filter({ hasText: 'Stale' }).waitFor();
await page.screenshot({ path: `${output}/dark-stale.png` });
await checkAxe(page, 'stale capture');
await page.unroute('**/api/findings');
await page.route('**/api/findings', (route) =>
  route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({
      data: null,
      error: { code: 'TIMEOUT', message: 'Request timed out' },
    }),
  }),
);
await page
  .getByRole('button', { name: 'Refresh capture', exact: true })
  .click();
await page.getByText(/Showing last successful capture from/).waitFor();
assert.equal(await page.locator('.finding-row').count(), 12);
await page.screenshot({ path: `${output}/dark-failed.png` });
await checkAxe(page, 'failed capture');
await page.close();
// Mobile menu, full-screen dialog and reduced motion.
page = await newPage();
await ready(page);
await page.setViewportSize({ width: 375, height: 900 });
await page.getByRole('button', { name: 'Open navigation' }).click();
assert.equal(
  await page
    .getByRole('link', { name: 'Connections', exact: true })
    .isVisible(),
  true,
);
await page.keyboard.press('Escape');
assert.equal(
  await page
    .getByRole('link', { name: 'Connections', exact: true })
    .isVisible(),
  false,
);
await page.emulateMedia({ reducedMotion: 'reduce' });
await page
  .getByRole('button', { name: 'Review FIND-001 for alex.chen' })
  .click();
await page.locator('.plan-actions').waitFor();
assert.equal(
  await page
    .locator('.detail-sheet')
    .evaluate((el) => el.getBoundingClientRect().width),
  375,
);
assert.equal(
  await page
    .locator('.detail-sheet')
    .evaluate((el) => getComputedStyle(el).transitionDuration),
  '0s',
);
await page.screenshot({ path: `${output}/dark-detail-375.png` });
await checkAxe(page, 'mobile dialog');
await page.close();
// At 200% zoom, 1440 physical pixels provide a 720 CSS pixel layout.
const context = await browser.newContext({
  viewport: { width: 720, height: 500 },
  deviceScaleFactor: 2,
});
page = await context.newPage();
await page.goto('http://127.0.0.1:5177/');
await ready(page);
assert.equal(
  await page.evaluate(() => document.documentElement.scrollWidth),
  720,
);
await page.screenshot({
  path: `${output}/dark-1440-at-200-percent.png`,
  fullPage: true,
});
await checkAxe(page, '1440 at 200% equivalent viewport');
results.push({
  name: 'zoom',
  physicalWidth: 1440,
  cssWidth: 720,
  deviceScaleFactor: 2,
  noOverflow: true,
});
await context.close();
await writeFile(`${output}/states.json`, JSON.stringify(results, null, 2));
console.log(JSON.stringify(results, null, 2));
await browser.close();
