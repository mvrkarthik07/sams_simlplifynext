import { chromium } from 'playwright';
import { writeFile } from 'node:fs/promises';
const browser = await chromium.launch();
const results = [];
for (const size of [12, 5000]) {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  if (size === 5000) {
    const data = await (
      await fetch('http://127.0.0.1:8018/api/findings')
    ).text();
    await page.route('**/api/findings', (route) =>
      route.fulfill({ contentType: 'application/json', body: data }),
    );
  }
  await page.addInitScript(() => {
    window.registerCommits = [];
    window.addEventListener('deadbolt:profile', (event) =>
      window.registerCommits.push(event.detail),
    );
  });
  await page.goto('http://127.0.0.1:5177/?profile');
  await page
    .locator('.result-summary')
    .filter({ hasText: `${size} findings` })
    .waitFor();
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  const sample = await page.evaluate(async () => {
    const select = document.querySelector('select');
    const summary = document.querySelector('.result-summary');
    window.registerCommits = [];
    const start = performance.now();
    const change = new Promise((resolve) =>
      new MutationObserver((records, observer) => {
        if (
          !summary.textContent.startsWith('12 findings') &&
          !summary.textContent.startsWith('5000 findings')
        ) {
          observer.disconnect();
          requestAnimationFrame(() =>
            requestAnimationFrame(() => resolve(performance.now() - start)),
          );
        }
      }).observe(summary, {
        childList: true,
        subtree: true,
        characterData: true,
      }),
    );
    select.value = 'T2';
    select.dispatchEvent(new Event('change', { bubbles: true }));
    const visualMs = await change;
    return {
      visualMs,
      commits: window.registerCommits,
      mountedRows: document.querySelectorAll('.finding-row').length,
      matchingRows: Number(summary.textContent.split(' ')[0]),
      summary: summary.textContent,
    };
  });
  results.push({ size, ...sample });
  await page.close();
}
console.log(JSON.stringify(results, null, 2));
await writeFile(
  '../backend/artifacts/register-review/profiler.json',
  JSON.stringify(results, null, 2),
);
await browser.close();
