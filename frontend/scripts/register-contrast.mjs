import { chromium } from 'playwright';
import { writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto('http://127.0.0.1:5177/');
await page.locator('.finding-row').first().waitFor();
const matrix = [];
for (const theme of ['dark', 'light']) {
  if ((await page.locator('html').getAttribute('data-theme')) !== theme)
    await page.getByRole('button', { name: `Switch to ${theme} mode` }).click();
  const data = await page.evaluate(() => {
    const ctx = document.createElement('canvas').getContext('2d');
    const probe = document.createElement('span');
    document.body.append(probe);
    const rgba = (token) => {
      probe.style.color = `var(${token})`;
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = getComputedStyle(probe).color;
      ctx.fillRect(0, 0, 1, 1);
      return Array.from(ctx.getImageData(0, 0, 1, 1).data).slice(0, 3);
    };
    const names = [
      '--text-primary',
      '--text-secondary',
      '--text-disabled',
      '--tier-0',
      '--tier-1',
      '--tier-2',
      '--tier-3',
      '--status-healthy',
      '--status-stale',
      '--status-error',
      '--link',
      '--ring',
      '--border-strong',
      '--risk-low',
      '--risk-elevated',
      '--risk-high',
      '--risk-critical',
    ];
    const surfaces = [
      '--surface-base',
      '--surface-raised',
      '--surface-overlay',
      '--row-bg-selected',
      '--row-bg-selected-hover',
    ];
    const colors = Object.fromEntries(
      [...names, ...surfaces].map((name) => [name, rgba(name)]),
    );
    probe.remove();
    return { names, surfaces, colors };
  });
  const luminance = (rgb) =>
    rgb
      .map((v) => v / 255)
      .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
      .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
  const ratio = (a, b) => {
    const x = luminance(a),
      y = luminance(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const rows = data.names.map((name) => ({
    token: name,
    color:
      '#' +
      data.colors[name].map((x) => x.toString(16).padStart(2, '0')).join(''),
    ratios: Object.fromEntries(
      data.surfaces.map((surface) => [
        surface,
        Number(ratio(data.colors[name], data.colors[surface]).toFixed(2)),
      ]),
    ),
  }));
  matrix.push({ theme, rows });
  for (const row of rows.filter(
    (row) => row.token.startsWith('--text-') || row.token.startsWith('--tier-'),
  ))
    for (const value of Object.values(row.ratios))
      assert(value >= 4.5, `${theme} ${row.token} ${value}`);
  for (const row of rows.filter((row) => row.token.startsWith('--status-')))
    for (const surface of data.surfaces.slice(0, 3))
      assert(
        row.ratios[surface] >= 4.5,
        `${theme} ${row.token} ${surface} ${row.ratios[surface]}`,
      );
  for (const row of rows.filter(
    (row) =>
      row.token === '--ring' ||
      row.token === '--border-strong' ||
      row.token.startsWith('--risk-'),
  ))
    for (const value of Object.values(row.ratios))
      assert(value >= 3, `${theme} UI ${row.token} ${value}`);
}
await writeFile(
  '../backend/artifacts/register-review/contrast.json',
  JSON.stringify(matrix, null, 2),
);
let md =
  '# Contrast measurements\n\nWCAG relative luminance from browser-resolved, rounded sRGB colors. Ratios use actual opaque surfaces, including both selection tints. Disabled text retains the 4.5:1 floor.\n\n';
for (const { theme, rows } of matrix) {
  md += `## ${theme}\n\n| Token | Hex | Base | Raised | Overlay | Selected | Selected + hover |\n|---|---|---:|---:|---:|---:|---:|\n`;
  for (const row of rows)
    md += `| ${row.token} | ${row.color} | ${Object.values(row.ratios)
      .map((x) => x.toFixed(2) + ':1')
      .join(' | ')} |\n`;
  md += '\n';
}
md +=
  'Text-primary, text-secondary, text-disabled and all tier tokens appear across row/sheet surfaces. Healthy/stale status text appears in the header on base; error text appears on base, raised notices and overlay feedback. Link is reserved for links (none in the register); ring, strong border and risk tokens are UI graphics with a 3:1 floor. Risk words use text-secondary, not risk colors. Subtle separators are decorative; controls, focus and disabled boundaries use border-strong.\n';
await writeFile('../backend/artifacts/register-review/contrast.md', md);
console.log('Contrast assertions passed. Full matrix saved.');
await browser.close();
