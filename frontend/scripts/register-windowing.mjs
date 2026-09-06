import { chromium } from 'playwright';
import axe from 'axe-core';
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const data = await (await fetch('http://127.0.0.1:8018/api/findings')).text();
await page.route('**/api/findings', (route) =>
  route.fulfill({ contentType: 'application/json', body: data }),
);
await page.goto('http://127.0.0.1:5177/');
await page
  .locator('.result-summary')
  .filter({ hasText: '5000 findings' })
  .waitFor();
const measurements = [];
for (const width of [1440, 768, 375]) {
  await page.setViewportSize({ width, height: 1000 });
  if (width === 375) await page.locator('.register-cards').waitFor();
  else await page.locator('.register-table').waitFor();
  for (const fraction of [0, 0.5, 1]) {
    await page.evaluate(
      (fraction) =>
        scrollTo(
          0,
          (document.documentElement.scrollHeight - innerHeight) * fraction,
        ),
      fraction,
    );
    await page.evaluate(
      () =>
        new Promise((r) =>
          requestAnimationFrame(() =>
            requestAnimationFrame(() => requestAnimationFrame(r)),
          ),
        ),
    );
    const info = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      width: innerWidth,
      mounted: document.querySelectorAll('.finding-row').length,
      visible: [...document.querySelectorAll('.finding-row')].filter((row) => {
        const r = row.getBoundingClientRect();
        return r.bottom > 0 && r.top < innerHeight;
      }).length,
      first: document
        .querySelector('.finding-row')
        ?.getAttribute('data-finding-id'),
      last: [...document.querySelectorAll('.finding-row')]
        .at(-1)
        ?.getAttribute('data-finding-id'),
    }));
    assert.equal(info.scrollWidth, width);
    assert(info.visible > 0, 'No blank viewport');
    assert(info.mounted < 80, 'Window remains bounded');
    measurements.push({ width, fraction, ...info });
  }
}
await page.keyboard.press('Control+k');
await page.getByRole('searchbox').fill('FIND-5000');
await page.getByText('1 finding · 1 identity', { exact: true }).waitFor();
assert.equal(await page.locator('[data-finding-id="FIND-5000"]').count(), 1);
await page.getByRole('searchbox').fill('');
await page
  .locator('.result-summary')
  .filter({ hasText: '5000 findings' })
  .waitFor();
await page.setViewportSize({ width: 1440, height: 1000 });
await page.locator('.register-table').waitFor();
await page.evaluate(() => scrollTo(0, 0));
await page.evaluate(axe.source);
const violations = await page.evaluate(async () =>
  (await window.axe.run()).violations.map((v) => ({
    id: v.id,
    nodes: v.nodes.map((n) => n.failureSummary),
  })),
);
assert.deepEqual(violations, []);
await writeFile(
  '../backend/artifacts/register-review/windowing.json',
  JSON.stringify(
    { measurements, searchAllRecords: true, axe: violations },
    null,
    2,
  ),
);
console.log(
  'Windowing passed at top, middle and bottom in desktop, tablet and mobile; search finds record 5000; axe: 0.',
);
await browser.close();
