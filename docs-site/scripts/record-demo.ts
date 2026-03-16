/**
 * Playwright script to record a demo video of the Immich Memories UI.
 *
 * Records one video per UI section (separate browser contexts for clean cuts),
 * with demo-mode blur, redacted sensitive info, and an animated CSS cursor.
 *
 * Prerequisites:
 *   1. Start the NiceGUI app:
 *      uv run immich-memories ui --port 8099
 *
 *   2. Install Playwright browser:
 *      cd docs-site && npm install && npx playwright install chromium
 *
 *   3. Run this script:
 *      npx tsx scripts/record-demo.ts
 *
 * Output: docs-site/static/demo/raw/ (one .webm per section)
 * Then run assemble-demo.sh to produce the final video.
 */

import {chromium, type Page, type BrowserContext, type Browser} from '@playwright/test';
import path from 'path';
import fs from 'fs';

const BASE_URL = 'http://localhost:8099';
const RAW_DIR = path.join(__dirname, '..', 'static', 'demo', 'raw');
const VIDEO_SIZE = {width: 1920, height: 1080};

// ---------------------------------------------------------------------------
// Redaction (reused from take-screenshots.ts)
// ---------------------------------------------------------------------------

const REDACTIONS: Array<{selector: string; value: string}> = [
  {selector: 'input[aria-label="Immich Server URL"]', value: 'https://photos.example.com'},
  {selector: 'input[aria-label="API Key"]', value: 'your-api-key-here'},
  {selector: 'input[aria-label="Output filename"]', value: 'alice_2025_memories.mp4'},
];

const TEXT_REDACTIONS: Array<{find: RegExp; replace: string}> = [
  // Connection & server info
  {find: /Connected as: .+/, replace: 'Connected as: user@example.com'},
  {find: /http:\/\/\d+\.\d+\.\d+\.\d+:\d+/, replace: 'https://photos.example.com'},
  // File paths (macOS and Linux patterns)
  {find: /\/Users\/\w+\/Videos\/Memories\/.*/, replace: '/home/user/Videos/Memories/alice_2025_memories.mp4'},
  {find: /\/Users\/\w+\/\.immich-memories\/.*/, replace: '/home/user/.immich-memories/config.yaml'},
  {find: /Will be saved to: .*/, replace: 'Will be saved to: /home/user/Videos/Memories/alice_2025_memories.mp4'},
  {find: /Saved to: .*/, replace: 'Saved to: /home/user/Videos/Memories/alice_2025_memories.mp4'},
  {find: /Config file: .*/, replace: 'Config file: /home/user/.immich-memories/config.yaml'},
  // Birthday info (e.g. "Using Alice's birthday: March 15, 1990")
  {find: /Using \w+'s birthday: .+/, replace: "Using Alice's birthday: June 15, 1995"},
  // GPS coordinates that might appear in debug/detail views
  {find: /\d+\.\d{4,},\s*-?\d+\.\d{4,}/, replace: '48.8566, 2.3522'},
];

async function redactPage(page: Page) {
  for (const {selector, value} of REDACTIONS) {
    await page.evaluate(
      ({sel, val}) => {
        const el = document.querySelector(sel) as HTMLInputElement | null;
        if (el) el.value = val;
      },
      {sel: selector, val: value}
    );
  }

  for (const {find, replace} of TEXT_REDACTIONS) {
    await page.evaluate(
      ({pattern, repl}) => {
        const regex = new RegExp(pattern);
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node: Text | null;
        while ((node = walker.nextNode() as Text | null)) {
          if (regex.test(node.textContent || '')) {
            node.textContent = (node.textContent || '').replace(regex, repl);
          }
        }
      },
      {pattern: find.source, repl: replace}
    );
  }

  // Also redact locations on every pass
  await redactLocations(page);
}

/** Redact location names that appear in clip cards, trip dropdowns, etc. */
async function redactLocations(page: Page) {
  const fakeLocations = [
    'Paris, France', 'Tokyo, Japan', 'Barcelona, Spain',
    'Amsterdam, Netherlands', 'Lisbon, Portugal', 'Prague, Czech Republic',
    'Vienna, Austria', 'Copenhagen, Denmark', 'Berlin, Germany',
  ];

  await page.evaluate((fakes) => {
    // Replace "City, Country" patterns in text nodes (common in clip detail labels)
    // Match patterns like "New York, United States" or "Paris, France"
    const locationPattern = /^[A-Z][a-z]+(?:\s[A-Z][a-z]+)*,\s[A-Z][a-z]+(?:\s[A-Z][a-z]+)*$/;
    let fakeIdx = 0;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node: Text | null;
    while ((node = walker.nextNode() as Text | null)) {
      const text = (node.textContent || '').trim();
      if (locationPattern.test(text)) {
        node.textContent = fakes[fakeIdx % fakes.length];
        fakeIdx++;
      }
    }

    // Also redact trip dropdown options that contain location names with dates
    // Pattern: "Location Name (2025-01-01 to 2025-01-07, N assets)"
    const tripPattern = /^(.+?)\s*\(\d{4}-\d{2}-\d{2}\s+to\s+\d{4}-\d{2}-\d{2}/;
    const walker2 = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    fakeIdx = 0;
    while ((node = walker2.nextNode() as Text | null)) {
      const text = node.textContent || '';
      const match = text.match(tripPattern);
      if (match) {
        node.textContent = text.replace(match[1], fakes[fakeIdx % fakes.length]);
        fakeIdx++;
      }
    }
  }, fakeLocations);
}

async function redactPersonNames(page: Page) {
  const genericNames = [
    'All people', 'Alice', 'Bob', 'Carol', 'David', 'Emma',
    'Frank', 'Grace', 'Henry', 'Iris', 'Jack', 'Kate',
    'Liam', 'Mia', 'Noah', 'Olivia', 'Paul', 'Quinn',
    'Rose', 'Sam', 'Tina', 'Uma', 'Victor', 'Wendy',
  ];

  await page.evaluate((names) => {
    let css = '';
    const options = document.querySelectorAll('[role="option"]');
    options.forEach((opt, i) => {
      if (i >= names.length) return;
      opt.setAttribute('data-redact-idx', String(i));
      const div = opt.querySelector('div');
      if (div) div.setAttribute('data-redact-idx', String(i));
    });

    names.forEach((name, i) => {
      css += `[role="option"][data-redact-idx="${i}"] > div {
        font-size: 0 !important; line-height: normal !important;
      }
      [role="option"][data-redact-idx="${i}"] > div::after {
        content: "${name}" !important; font-size: 14px !important;
      }
      `;
    });

    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
  }, genericNames);

  await page.waitForTimeout(100);
}

// ---------------------------------------------------------------------------
// Animated CSS cursor — visible dot that moves to click targets
// ---------------------------------------------------------------------------

async function injectCursor(page: Page) {
  await page.evaluate(() => {
    const cursor = document.createElement('div');
    cursor.id = 'demo-cursor';
    cursor.style.cssText = `
      position: fixed; z-index: 99999; pointer-events: none;
      width: 24px; height: 24px; border-radius: 50%;
      background: rgba(66, 80, 175, 0.7);
      border: 2px solid rgba(255, 255, 255, 0.9);
      box-shadow: 0 2px 8px rgba(0,0,0,0.3);
      transform: translate(-50%, -50%);
      transition: left 0.4s cubic-bezier(0.22, 1, 0.36, 1),
                  top 0.4s cubic-bezier(0.22, 1, 0.36, 1),
                  transform 0.1s ease;
      left: 50%; top: 50%;
    `;
    document.body.appendChild(cursor);
  });
}

/** Animate cursor to element, pause, then click it. */
async function cursorClick(page: Page, selector: string, options?: {delay?: number}) {
  const delay = options?.delay ?? 300;

  // Move cursor to element center
  const box = await page.locator(selector).first().boundingBox();
  if (!box) {
    // Fallback: just click
    await page.locator(selector).first().click();
    return;
  }

  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;

  await page.evaluate(({x, y}) => {
    const cursor = document.getElementById('demo-cursor');
    if (cursor) {
      cursor.style.left = `${x}px`;
      cursor.style.top = `${y}px`;
    }
  }, {x, y});

  // Wait for cursor to arrive
  await page.waitForTimeout(500);

  // Click animation (scale down)
  await page.evaluate(() => {
    const cursor = document.getElementById('demo-cursor');
    if (cursor) cursor.style.transform = 'translate(-50%, -50%) scale(0.7)';
  });
  await page.waitForTimeout(100);
  await page.evaluate(() => {
    const cursor = document.getElementById('demo-cursor');
    if (cursor) cursor.style.transform = 'translate(-50%, -50%) scale(1)';
  });

  // Actually click
  await page.locator(selector).first().click();
  await page.waitForTimeout(delay);
}

/** Animate cursor to a Locator target and click. */
async function cursorClickLocator(page: Page, locator: ReturnType<Page['locator']>, options?: {delay?: number}) {
  const delay = options?.delay ?? 300;
  const box = await locator.boundingBox();
  if (!box) {
    await locator.click();
    return;
  }

  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;

  await page.evaluate(({x, y}) => {
    const cursor = document.getElementById('demo-cursor');
    if (cursor) { cursor.style.left = `${x}px`; cursor.style.top = `${y}px`; }
  }, {x, y});

  await page.waitForTimeout(500);
  await page.evaluate(() => {
    const c = document.getElementById('demo-cursor');
    if (c) c.style.transform = 'translate(-50%, -50%) scale(0.7)';
  });
  await page.waitForTimeout(100);
  await page.evaluate(() => {
    const c = document.getElementById('demo-cursor');
    if (c) c.style.transform = 'translate(-50%, -50%) scale(1)';
  });

  await locator.click();
  await page.waitForTimeout(delay);
}

// ---------------------------------------------------------------------------
// Smooth scrolling helper
// ---------------------------------------------------------------------------

async function smoothScroll(page: Page, y: number) {
  await page.evaluate((scrollY) => {
    window.scrollTo({top: scrollY, behavior: 'smooth'});
  }, y);
  await page.waitForTimeout(800);
}

async function smoothScrollToBottom(page: Page) {
  await page.evaluate(() => {
    window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});
  });
  await page.waitForTimeout(800);
}

async function smoothScrollToTop(page: Page) {
  await smoothScroll(page, 0);
}

// ---------------------------------------------------------------------------
// Recording helpers
// ---------------------------------------------------------------------------

async function enableDemoMode(page: Page) {
  await page.evaluate(() => document.body.classList.add('demo-mode'));
}

async function waitForReady(page: Page) {
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(1500);
}

async function createRecordingContext(browser: Browser, name: string): Promise<BrowserContext> {
  const ctx = await browser.newContext({
    viewport: VIDEO_SIZE,
    recordVideo: {dir: RAW_DIR, size: VIDEO_SIZE},
  });
  console.log(`  Recording: ${name}`);
  return ctx;
}

async function finishRecording(context: BrowserContext, page: Page, name: string): Promise<string> {
  // Close the page first to finalize video
  const videoPath = await page.video()?.path();
  await context.close();

  if (!videoPath) {
    console.error(`  WARNING: No video path for ${name}`);
    return '';
  }

  // Rename from random UUID to our section name
  const ext = path.extname(videoPath);
  const dest = path.join(RAW_DIR, `${name}${ext}`);
  fs.renameSync(videoPath, dest);
  console.log(`  Saved: ${dest}`);
  return dest;
}

// ---------------------------------------------------------------------------
// Section recorders
// ---------------------------------------------------------------------------

async function recordSection1(browser: Browser) {
  console.log('\n── Section 1: Configuration ──');
  const ctx = await createRecordingContext(browser, 'section1-config');
  const page = await ctx.newPage();

  await page.goto(BASE_URL);
  await waitForReady(page);
  await enableDemoMode(page);
  await injectCursor(page);
  await redactPage(page);

  // Show the connected state for a moment
  await page.waitForTimeout(2000);

  // Scroll down to preset cards
  const yearPreset = page.getByText('Year in Review');
  if (await yearPreset.isVisible()) {
    await yearPreset.scrollIntoViewIfNeeded();
    await page.waitForTimeout(500);
    await cursorClickLocator(page, yearPreset, {delay: 800});
  }

  // Open person dropdown
  await page.waitForTimeout(500);
  const personCombo = page.getByRole('combobox', {name: 'Person'});
  if (await personCombo.isVisible()) {
    await cursorClickLocator(page, personCombo, {delay: 600});
    await redactPersonNames(page);
    await page.waitForTimeout(1000);

    // Select first real person
    const options = page.getByRole('option');
    const count = await options.count();
    if (count > 1) {
      await cursorClickLocator(page, options.nth(1), {delay: 500});
    } else {
      await page.getByRole('option', {name: 'All people'}).click();
    }
    await page.waitForTimeout(500);
  }

  // Pause at the end to let viewer absorb
  await page.waitForTimeout(1500);

  return finishRecording(ctx, page, 'section1-config');
}

async function recordSection2(browser: Browser) {
  console.log('\n── Section 2: Clip Review ──');

  // Navigate to step 2 through step 1 (preserves app state)
  const ctx = await createRecordingContext(browser, 'section2-review');
  const page = await ctx.newPage();

  await page.goto(BASE_URL);
  await waitForReady(page);
  await enableDemoMode(page);
  await injectCursor(page);
  await redactPage(page);

  // Click through to step 2
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(200);
  const nextBtn = page.getByRole('button', {name: 'Next: Review Clips'});
  await cursorClickLocator(page, nextBtn);

  await page.waitForURL('**/step2', {timeout: 30_000});

  // Wait for loading to finish
  console.log('  Waiting for clips to load...');
  try {
    await page.waitForSelector('[role="dialog"]', {timeout: 5_000});
  } catch {
    // Dialog may have already gone
  }
  await page.waitForFunction(
    () => !document.querySelector('[role="dialog"]'),
    {timeout: 300_000}
  );
  await waitForReady(page);

  // Wait for content
  try {
    await page.waitForSelector('button:has-text("clips")', {timeout: 30_000});
  } catch {
    // Continue anyway
  }

  await enableDemoMode(page);
  await injectCursor(page);
  await page.waitForTimeout(1500);

  // Expand first month
  const monthButton = page.locator('button').filter({hasText: /\(\d+ clips?\)/}).first();
  if (await monthButton.isVisible()) {
    await monthButton.scrollIntoViewIfNeeded();
    await cursorClickLocator(page, monthButton, {delay: 1000});
    await monthButton.scrollIntoViewIfNeeded();
    await page.waitForTimeout(1500);
  }

  // Scroll through the clip grid
  await smoothScroll(page, 400);
  await page.waitForTimeout(1000);

  // End pause
  await page.waitForTimeout(1500);

  return finishRecording(ctx, page, 'section2-review');
}

async function recordSection3(browser: Browser) {
  console.log('\n── Section 3: Generation Options ──');

  const ctx = await createRecordingContext(browser, 'section3-options');
  const page = await ctx.newPage();

  // Navigate through steps 1 → 2 → 3
  await page.goto(BASE_URL);
  await waitForReady(page);
  await enableDemoMode(page);
  await redactPage(page);

  // Step 1 → 2
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.getByRole('button', {name: 'Next: Review Clips'}).click();
  await page.waitForURL('**/step2', {timeout: 30_000});

  // Wait for loading
  try {
    await page.waitForSelector('[role="dialog"]', {timeout: 5_000});
  } catch { /* already gone */ }
  await page.waitForFunction(
    () => !document.querySelector('[role="dialog"]'),
    {timeout: 300_000}
  );
  await waitForReady(page);

  // Step 2 → Refine → Step 3
  const refineButton = page.getByRole('button', {name: 'Next: Refine Moments'});
  await refineButton.scrollIntoViewIfNeeded();
  await refineButton.click();
  await waitForReady(page);

  const continueButton = page.getByRole('button', {name: 'Continue to Generation'});
  await continueButton.scrollIntoViewIfNeeded();
  await continueButton.click();
  await page.waitForURL('**/step3', {timeout: 30_000});
  await waitForReady(page);

  // Now record with cursor
  await enableDemoMode(page);
  await injectCursor(page);
  await page.waitForTimeout(2000);

  // Scroll through options
  await smoothScroll(page, 300);
  await page.waitForTimeout(1500);
  await smoothScrollToBottom(page);
  await page.waitForTimeout(1500);

  // End pause
  await page.waitForTimeout(1500);

  return finishRecording(ctx, page, 'section3-options');
}

async function recordSection4(browser: Browser) {
  console.log('\n── Section 4: Preview & Export ──');

  const ctx = await createRecordingContext(browser, 'section4-export');
  const page = await ctx.newPage();

  // Navigate through all steps
  await page.goto(BASE_URL);
  await waitForReady(page);
  await enableDemoMode(page);
  await redactPage(page);

  // Step 1 → 2
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.getByRole('button', {name: 'Next: Review Clips'}).click();
  await page.waitForURL('**/step2', {timeout: 30_000});
  try {
    await page.waitForSelector('[role="dialog"]', {timeout: 5_000});
  } catch { /* */ }
  await page.waitForFunction(
    () => !document.querySelector('[role="dialog"]'),
    {timeout: 300_000}
  );
  await waitForReady(page);

  // Step 2 → Refine → Step 3
  const refineBtn = page.getByRole('button', {name: 'Next: Refine Moments'});
  await refineBtn.scrollIntoViewIfNeeded();
  await refineBtn.click();
  await waitForReady(page);

  const contBtn = page.getByRole('button', {name: 'Continue to Generation'});
  await contBtn.scrollIntoViewIfNeeded();
  await contBtn.click();
  await page.waitForURL('**/step3', {timeout: 30_000});
  await waitForReady(page);

  // Step 3 → 4
  await page.getByRole('button', {name: 'Next: Preview & Export'}).click();
  await page.waitForURL('**/step4', {timeout: 30_000});
  await waitForReady(page);

  // Record with cursor
  await enableDemoMode(page);
  await injectCursor(page);
  await redactPage(page);
  await page.waitForTimeout(2000);

  // Scroll to show the full export page
  await smoothScrollToBottom(page);
  await page.waitForTimeout(2000);

  // End pause
  await page.waitForTimeout(1500);

  return finishRecording(ctx, page, 'section4-export');
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  fs.mkdirSync(RAW_DIR, {recursive: true});

  console.log('Launching browser for demo recording...');
  console.log(`Output: ${RAW_DIR}`);
  const browser = await chromium.launch({headless: true});

  try {
    await recordSection1(browser);
    await recordSection2(browser);
    await recordSection3(browser);
    await recordSection4(browser);

    console.log('\n✓ All sections recorded.');
    console.log('Run assemble-demo.sh to produce the final video.');
  } catch (error) {
    console.error('Recording failed:', error);
    process.exit(1);
  } finally {
    await browser.close();
  }
}

main();
