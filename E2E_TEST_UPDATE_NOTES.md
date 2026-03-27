# E2E Test Updates Required After UI Overhaul

After merging `feat/ui-density-overhaul`, these test updates are needed.

## 1. Fix broken selector in `test_screenshots.py`

**File**: `tests/e2e/test_screenshots.py`, line 195

The old selector waits for a button containing "clips", but "clips" is now
in a stats bar label (not a button).

```python
# BEFORE
page.wait_for_selector('button:has-text("clips")', timeout=30_000)

# AFTER
page.wait_for_selector('text=/\\d+ clips/', timeout=30_000)
```

## 2. Re-capture all screenshots

The spacing, layout order, and visual density have changed across all pages.
All 50+ screenshots need re-capturing:

```bash
make screenshots
```

Review the new PNGs in `docs-site/static/img/screenshots/` and commit them.

## 3. What should still work (no changes needed)

These selectors are unchanged and should pass as-is:

| Selector | File | Why it works |
|----------|------|-------------|
| `button name="Next: Review Clips"` | both | Button label unchanged |
| `button name="Next: Refine Moments"` | screenshots | Button label unchanged |
| `button name="Continue to Generation"` | screenshots | Button label unchanged |
| `button name="Next: Preview & Export"` | screenshots | Button label unchanged |
| `button name="Generate Video"` | full_generation | Button label unchanged |
| `text=Options` (sidebar) | full_generation | Sidebar nav unchanged |
| `text=Export` (sidebar) | full_generation | Sidebar nav unchanged |
| `grid_view` / `view_list` icons | screenshots | Icons moved to toolbar but selectors still match |
| `button filter(\d+ clips?)` (month) | screenshots | Expansion labels unchanged |
| `text="Your memory video is ready!"` | full_generation | Success message unchanged |
| `.q-linear-progress` | full_generation | Progress bar class unchanged |
| `[role="dialog"]` | both | Loading dialog unchanged |

## 4. Behavioral changes to be aware of

- **Step 2**: Clips now appear ABOVE controls. The "Generate Memories" section
  is now a collapsible expansion panel (collapsed by default). If any future
  test needs to click "Generate Memories", it must first expand the panel.

- **Step 4**: Video preview now renders FIRST (above summary/settings) when
  a generated video exists. The "Upload to Immich" section is merged into the
  "Output" card (no separate section header).

- **All pages**: Separators between sections are removed (only one before
  navigation buttons). Spacing is ~40% tighter globally.

## 5. Delete this file

Once the test updates are done, delete this file — it's not meant to be committed.
