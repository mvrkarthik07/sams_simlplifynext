# Deadbolt brand assets

Geometry: 24-unit grid, uniform 3.5-unit stroke, 0.5-unit radius. Three rectangles.
Colours: ink #16181A, paper #FBFBFA, open-state #B3261E.

| File | Use |
|---|---|
| `logo.svg` | primary mark, ink on light surfaces |
| `logo-inverse.svg` | mark on dark surfaces |
| `logo-tile.svg` | sidebar tile, app icon source |
| `logo-open.svg` / `logo-open-tile.svg` | "bolt retracted" state indicator |
| `favicon.ico` | 16/32/48 multi-resolution |
| `apple-touch-icon.png` | 180 |
| `icon-192.png`, `icon-512.png` | PWA manifest |
| `tile-*.png` | derived raster, all from the same vector |

## Lockup
Mark at cap height. Gap = 0.4x mark width. Wordmark "Deadbolt" in Public Sans 600,
tracking -0.01em, #16181A. Baseline-aligned. No tagline.

## HTML head
```html
<link rel="icon" href="/brand/favicon.ico" sizes="any">
<link rel="icon" href="/brand/logo-tile.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/brand/apple-touch-icon.png">
```

## Clear space
Minimum margin on all sides = 1x stroke weight (3.5 units at 24, i.e. ~15% of mark width).
Never place the mark on a photographic background or apply a shadow, glow, or gradient.
