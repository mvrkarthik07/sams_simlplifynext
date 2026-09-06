import { chromium } from 'playwright';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';
const extension = await mkdtemp(join(tmpdir(), 'register-zoom-'));
await writeFile(
  join(extension, 'manifest.json'),
  JSON.stringify({
    manifest_version: 3,
    name: 'Register zoom verification',
    version: '1.0',
    permissions: ['tabs'],
    background: { service_worker: 'background.js' },
  }),
);
await writeFile(
  join(extension, 'background.js'),
  'chrome.runtime.onInstalled.addListener(() => {});',
);
const context = await chromium.launchPersistentContext('', {
  channel: 'chromium',
  headless: true,
  viewport: { width: 1440, height: 1000 },
  args: [
    `--disable-extensions-except=${extension}`,
    `--load-extension=${extension}`,
  ],
});
try {
  const worker =
    context.serviceWorkers()[0] ??
    (await context.waitForEvent('serviceworker'));
  const page = await context.newPage();
  await page.goto('http://127.0.0.1:5177/');
  await page.locator('.finding-row').first().waitFor();
  await worker.evaluate(async () => {
    const tabs = await chrome.tabs.query({ url: 'http://127.0.0.1:5177/*' });
    await chrome.tabs.setZoom(tabs[0].id, 2);
  });
  await page.waitForFunction(
    () =>
      innerWidth === 720 &&
      document.querySelectorAll('.register-cards .finding-row').length === 12,
  );
  const zoom = await worker.evaluate(async () => {
    const tabs = await chrome.tabs.query({ url: 'http://127.0.0.1:5177/*' });
    return chrome.tabs.getZoom(tabs[0].id);
  });
  const layout = await page.evaluate(() => ({
    innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    dpr: devicePixelRatio,
    rows: document.querySelectorAll('.register-cards .finding-row').length,
  }));
  assert.equal(zoom, 2);
  assert.equal(layout.scrollWidth, 720);
  assert.equal(layout.rows, 12);
  const cdp = await context.newCDPSession(page);
  const screenshot = await cdp.send('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: false,
  });
  await writeFile(
    '../backend/artifacts/register-review/dark-1440-actual-200-percent.png',
    Buffer.from(screenshot.data, 'base64'),
  );
  await writeFile(
    '../backend/artifacts/register-review/zoom.json',
    JSON.stringify({ zoom, physicalViewport: 1440, ...layout }, null, 2),
  );
  console.log({ zoom, physicalViewport: 1440, ...layout });
} finally {
  await context.close();
  await rm(extension, { recursive: true, force: true });
}
