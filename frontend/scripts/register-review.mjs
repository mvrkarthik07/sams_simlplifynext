import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import axe from 'axe-core';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
const output = resolve('../backend/artifacts/register-review');
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 1,
});
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
await page.goto(process.env.REGISTER_URL ?? 'http://127.0.0.1:5177/?profile');
await page.getByText('12 findings · 9 identities', { exact: true }).waitFor();
await page.evaluate(() => document.fonts.ready);
const report = { screenshots: [], axe: [], errors };
for (const theme of ['dark', 'light']) {
  if ((await page.locator('html').getAttribute('data-theme')) !== theme)
    await page.getByRole('button', { name: `Switch to ${theme} mode` }).click();
  for (const width of [375, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: width === 375 ? 900 : 1000 });
    if (width < 768) {
      await page.locator('.register-cards .finding-row').last().waitFor();
      await page.locator('.register-cards .finding-row').last().scrollIntoViewIfNeeded();
      await page.evaluate(() => scrollTo(0, 0));
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    }
    await page.locator('.register-footer').scrollIntoViewIfNeeded();
    await page.evaluate(() => scrollTo(0, 0));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.screenshot({
      path: `${output}/${theme}-${width}.png`,
      fullPage: true,
    });
    const layout = await page.evaluate(() => ({
      width: innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      font: getComputedStyle(document.body).fontFamily,
      sidebar: document
        .querySelector('.register-sidebar')
        .getBoundingClientRect().width,
    }));
    await page.evaluate(axe.source);
    const result = await page.evaluate(async () =>
      (await window.axe.run()).violations.map(({ id, description, nodes }) => ({
        id,
        description,
        nodes: nodes.map(({ target, failureSummary }) => ({
          target,
          failureSummary,
        })),
      })),
    );
    report.screenshots.push({ theme, ...layout });
    report.axe.push({ theme, width, violations: result });
  }
}
await page
  .getByRole('button', { name: 'Review FIND-001 for alex.chen' })
  .click();
await page.getByRole('heading', { name: 'Proposed plan' }).waitFor();
await page.locator('.plan-actions').waitFor();
await page.screenshot({
  path: `${output}/light-detail-1440.png`,
  fullPage: true,
});
report.axe.push({
  theme: 'light',
  surface: 'detail',
  violations: await page.evaluate(async () =>
    (await window.axe.run()).violations.map(({ id, nodes }) => ({
      id,
      nodes: nodes.map((n) => n.failureSummary),
    })),
  ),
});
await writeFile(
  `${output}/initial-review.json`,
  JSON.stringify(report, null, 2),
);
console.log(JSON.stringify(report, null, 2));
await browser.close();
assert.deepEqual(errors, [], 'No browser errors');
for (const entry of report.axe) assert.deepEqual(entry.violations, [], `Axe ${entry.theme} ${entry.width ?? entry.surface}`);
for (const entry of report.screenshots) assert.equal(entry.scrollWidth, entry.width, 'No horizontal overflow');
