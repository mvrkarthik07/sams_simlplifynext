# Entitlement register review

Implemented on `feat/entitlement-register-polish`. The app is React 19.2 with Vite 8 and Tailwind 3.4; it is not Next.js. This is a local implementation and browser rehearsal, not an AWS deployment. Existing API operation signatures are unchanged.

## Decisions

- Keep dark as the initial theme; preserve a saved light preference. Self-host the five IBM Plex font weights as WOFF2 with their OFL licenses.
- Raise secondary, disabled and error text above the supplied primitive values where necessary. The final light disabled/T0 value is `#585B60`. Disabled rows also have a dashed boundary and a lock icon. No opacity reduction is used for disabled text.
- Use native select semantics and a 120ms picker fade where `appearance: base-select` is supported. Other browsers retain their native picker. Reduced motion removes the fade.
- Add a compact mobile sort select so sorting remains available after the table becomes a semantic list of cards.
- Sort identity groups by their first finding under the selected sort; use finding ID to break ties. Collapse state survives filtering and searching for the life of the register. Stage sorts by its numeric pipeline position.
- Use five pipeline positions: detected/scored, planned, approval, executing, verified/rolled back. Keep the explicitly requested middle dot in stage text; use it otherwise only in metrics and result counts.
- Show five relevant health metrics. Omit the unwired sandbox billing tile from this review screen. Unavailable capture times stay explicitly unavailable, including an initially empty response that carries no timestamp.
- Refresh reloads persisted captures without changing their timestamps. A configured live source can run its read-only connection scan. Fixture or unconfigured queues lead to Connections to set up a source.
- Queue a review decision for eight seconds before submission. Undo cancels that pending submission, not an already executed provider action. Navigating away cancels the pending timer. The sheet discloses the API's rehearsal-only decision behavior.
- Move the unauthenticated preview label into the main footer. Authentication enforcement and provider-secret handling are unchanged.
- Provide a separate, explicit 12-finding HTTP review fixture. The PRD's 20-planted-finding scenario, backend scoring, captured customer/provider data, and golden hashes are unchanged. This fixture contains nine identities, all four risk bands and tiers, a stale row, failed verification, and an unavailable row. Its health data includes 85.7% recall and 80% reversibility.
- Use memoized rows/cards with `content-visibility`. Above 200 findings, a small windowing component mounts the viewport plus a 600px buffer; filtering and counts still use every record. No virtualization dependency was added. Card intrinsic content height is 106px, plus 24px padding and 2px border, matching the rendered 132px card.
- Keep existing unrelated workspace edits out of the new commits where separable. The existing App shell change is included because the responsive sidebar depends on that shell.

## Files

| File | Change |
|---|---|
| `frontend/src/pages/Dashboard.tsx` | Compact health line, capture states, independent filters, deferred search, sorting, identity grouping, memoized rows/cards, lazy detail sheet, optimistic decision scheduling and feedback |
| `frontend/src/lib/register.ts` | One-pass derivation, exported risk thresholds, human-readable entitlement and stage labels, metric states, explicit SGT times and error copy |
| `frontend/src/components/register/DetailPanel.tsx` | Modal sheet, focus loop/return, Escape/outside close, evidence, proposed plan, identifiers, decision controls and cancellation feedback |
| `frontend/src/components/Sidebar.tsx` | 220px sidebar, 48px rail, mobile menu, per-icon SVG imports, active leading edge, footer theme preference |
| `frontend/src/lib/api.ts` | Include existing data-change announcements and no-store reads required by the register refresh behavior; operation signatures stay unchanged |
| `frontend/src/App.tsx` | Include the existing shell classes needed by responsive navigation |
| `frontend/src/components/AuthGate.tsx` | Remove duplicate preview banner; use per-icon import. Existing owner logo edits remain separate |
| `frontend/src/styles/tokens.css` | Primitive → semantic → component colors, both themes, six row states, compatibility aliases |
| `frontend/src/styles/register.css` | Register and shell layout, 0/4/6 radii, rails, responsive cards, sheets, focus and reduced motion |
| `frontend/src/styles/fonts.css` | Local IBM Plex font faces |
| `frontend/public/fonts/` | Sans 400/500/600 and Mono 400/500 WOFF2 assets and licenses |
| `frontend/src/index.css` | Remove runtime Google Fonts import; load local font and register styles |
| `frontend/index.html` | Preload the five font assets |
| `frontend/tailwind.config.js` | IBM Plex Sans family |
| `frontend/src/lib/theme.ts` | Default to dark when no preference is saved |
| `frontend/src/lucide-icons.d.ts` | Typed declaration for Lucide's individual ESM modules |
| `frontend/package.json`, `frontend/package-lock.json` | Development-only Playwright and axe-core dependencies |
| `backend/tools/register_fixture.py` | Local HTTP fixture and synthetic 5,000-record mode; no AWS/provider calls |
| `frontend/scripts/register-review.mjs` | Eight screenshots, width assertions, browser errors and axe checks |
| `frontend/scripts/register-states.mjs` | Loading/CLS, filters, focus, undo, errors, empty/stale states and mobile checks |
| `frontend/scripts/register-perf.mjs` | React Profiler commit counts and double-animation-frame feedback timing |
| `frontend/scripts/register-contrast.mjs` | Browser-resolved token contrast matrix with assertions |
| `frontend/scripts/register-windowing.mjs` | Verify top/middle/bottom rendering in desktop/tablet/mobile and search across all 5,000 records |
| `frontend/scripts/register-zoom.mjs` | Actual Chromium 200% zoom through a temporary local extension; native viewport screenshot |
| `backend/DECISIONS.md` | Append the authorization, contrast, fixture and API behavior decisions |

## Verification

`cd backend && make gate-m12r && make guard` passes. The gate runs frontend lint, TypeScript, production build, backend lint/format, strict mypy, architecture checks, 138 non-live tests, coverage and the protected-path guard. Coverage is **85.12%**. No live AWS calls were made.

Axe reports **zero violations** at 375, 768, 1024 and 1440 in both themes, plus the reviewed dialog, empty, stale, failed, and mobile states. The state suite verifies group persistence, keyboard sorting, a complete focus loop, focus restoration, outside close, and zero writes after Undo. Final desktop loading CLS was **0.00078** (the earlier run was also below the floor at 0.02773).

Actual browser zoom was set to **2.0** in a 1440px Chromium window. The resulting viewport was **720 CSS pixels**, with document scroll width **720**, DPR **2**, and all 12 cards present. The screenshot was captured directly from the browser viewport to avoid the full-page screenshot API's zoom cropping behavior.

### Performance

Measured in Chromium 153 on this macOS arm64 workspace, React development build with StrictMode and React Profiler. Interaction: all findings → tier T2. Visual feedback runs from the dispatched filter event to two animation frames after the result summary changes.

| Source records | Matching records | React commits | React render duration | Visual feedback |
|---:|---:|---:|---:|---:|
| 12 | 3 | 1 | 2.4ms | 32.9ms |
| 5,000 | 1,251 | 1 | 21.9ms | 49.2ms |

The large fixture mounted 30 rows for this interaction, while all 5,000 records participated in derivation and 1,251 matched. Before windowing, isolated and concurrent verification runs reached 146ms; that regression led to the windowing change. These are recorded interaction samples, not estimates. [Raw profiler samples](../artifacts/register-review/profiler.json).

### Contrast

Every text token/surface pairing that occurs in the screen passes 4.5:1. Functional control boundaries, rings and risk graphics pass 3:1. Subtle separators are decorative; they are not the sole way to identify controls.

| Text role | Dark minimum across its used surfaces | Light minimum across its used surfaces |
|---|---:|---:|
| Primary | 11.13:1 | 10.67:1 |
| Secondary | 5.19:1 | 4.80:1 |
| Disabled / T0 | 4.88:1 | 4.79:1 |
| T1 | 6.40:1 | 5.43:1 |
| T2 | 8.56:1 | 7.79:1 |
| T3 | 11.13:1 | 10.67:1 |
| Healthy header status | 8.16:1 | 5.72:1 |
| Stale header status | 8.44:1 | 5.69:1 |
| Error notices / sheet feedback | 6.19:1 | 4.91:1 |

Strong-boundary minimum: **3.11:1 dark / 3.03:1 light**. The complete [token-by-surface matrix](../artifacts/register-review/contrast.md) reports every measured ratio and states which tokens are text versus graphics.

## Screenshot checklist

Reviewed the page header, health line, toolbar, navigation, complete table/card queue, footer and detail sheet in the eight screenshots below. All 16 requested tells are absent from this screen.

| Tell | Result | Where checked |
|---|---|---|
| Tracked uppercase eyebrows | Absent | Header, sidebar, health line and card labels |
| Repeated “WORD — fragment” captions | Absent | Health line and notices |
| Three-noun subtitle | Absent | Page header has no subtitle |
| Instructions for existing affordances | Absent | Header and queue; notices only explain state/recovery |
| One accent doing unrelated jobs | Absent | Healthy green, neutral tiers, semantic risk rails and required blue focus/selection |
| Monospace labels, badges or numerals | Absent | Queue/health/footer are Plex Sans; mono is limited to literal identifiers in the sheet |
| Blue-tinted near-black canvas | Absent | Dark base/raised/overlay graphite surfaces |
| Inter/Geist/Roboto/system-ui primary face | Absent | Computed font and self-hosted WOFF2 requests |
| One radius used everywhere | Absent | Controls/badges 4px, containers/cards 6px, table rows 0 |
| Soft shadows under containers | Absent | Sidebar, register and sheet; sheet only has the allowed short edge shadow |
| Dividers as the only structure | Absent | Base, raised register/cards and overlay sheet |
| Decorative dot-separated metadata | Absent | Dots only separate health/result data and the required stage grammar |
| Text buttons with appended arrow | Absent | Standalone SVG review icon; plain action labels |
| Load or scroll animation | Absent | Static skeletons, content-visibility, motion rules |
| Emoji icons | Absent | Lucide SVGs and specified text status glyphs |
| Perfect, identical fixture | Absent | 12 scores from 12–92, four bands/tiers, partial health, stale/failed/unavailable rows |

## Screenshots

| Width | Dark | Light |
|---:|---|---|
| 375 | [Dark mobile](../artifacts/register-review/dark-375.png) | [Light mobile](../artifacts/register-review/light-375.png) |
| 768 | [Dark tablet](../artifacts/register-review/dark-768.png) | [Light tablet](../artifacts/register-review/light-768.png) |
| 1024 | [Dark desktop](../artifacts/register-review/dark-1024.png) | [Light desktop](../artifacts/register-review/light-1024.png) |
| 1440 | [Dark desktop](../artifacts/register-review/dark-1440.png) | [Light desktop](../artifacts/register-review/light-1440.png) |

[Actual 200% zoom](../artifacts/register-review/dark-1440-actual-200-percent.png), [detail sheet](../artifacts/register-review/light-detail-1440.png), [mobile sheet](../artifacts/register-review/dark-detail-375.png), [loading](../artifacts/register-review/dark-loading.png), [filtered empty](../artifacts/register-review/dark-filtered-empty.png), [true empty](../artifacts/register-review/dark-true-empty.png), [stale capture](../artifacts/register-review/dark-stale.png), [failed capture](../artifacts/register-review/dark-failed.png).

## Reproduce locally

Start these in separate terminals from the repository root:

```sh
python3 backend/tools/register_fixture.py --port 8017
python3 backend/tools/register_fixture.py --port 8018 --rows 5000
```

```sh
cd frontend
npm ci --no-audit --no-fund
npx playwright install chromium
VITE_API_BASE=http://127.0.0.1:8017/api npm run dev -- --host 127.0.0.1 --port 5177
```

Then run from `frontend`:

```sh
node scripts/register-review.mjs
node scripts/register-states.mjs
node scripts/register-contrast.mjs
node scripts/register-perf.mjs
node scripts/register-zoom.mjs
node scripts/register-windowing.mjs
```

The screenshot/JSON outputs are generated under `backend/artifacts/register-review/`. Do not run browser state tests while editing source: development hot reload intentionally cleans up pending decision timers.
