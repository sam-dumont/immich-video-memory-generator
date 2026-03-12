/**
 * Playwright script to capture screenshots of the Immich Memories UI.
 *
 * Prerequisites:
 *   1. Start the NiceGUI app (uses your normal config):
 *      uv run immich-memories ui --port 8099
 *
 *   2. Install Playwright browser:
 *      cd docs-site && npm install && npx playwright install chromium
 *
 *   3. Run this script:
 *      npx tsx scripts/take-screenshots.ts
 *
 * The script redacts sensitive info (server URL, API key, email, file paths,
 * person names) before saving screenshots to static/img/screenshots/.
 *
 * After running, use blur-faces.ts to blur faces in thumbnails.
 */

import {chromium, type Page} from '@playwright/test';
import path from 'path';
import fs from 'fs';

const BASE_URL = 'http://localhost:8099';
const SCREENSHOT_DIR = path.join(__dirname, '..', 'static', 'img', 'screenshots');

// Redaction: replace sensitive text in the DOM before screenshots
const REDACTIONS: Array<{selector: string; value: string}> = [
  {selector: 'input[aria-label="Immich Server URL"]', value: 'https://photos.example.com'},
  {selector: 'input[aria-label="API Key"]', value: 'your-api-key-here'},
  {selector: 'input[aria-label="Output filename"]', value: 'alice_2025_memories.mp4'},
];

// Text replacements applied via JS on the page
const TEXT_REDACTIONS: Array<{find: RegExp; replace: string}> = [
  {find: /Connected as: .+/, replace: 'Connected as: user@example.com'},
  {find: /\/Users\/\w+\/Videos\/Memories\/.*/, replace: '/home/user/Videos/Memories/alice_2025_memories.mp4'},
  {find: /http:\/\/\d+\.\d+\.\d+\.\d+:\d+/, replace: 'https://photos.example.com'},
  {find: /Will be saved to: .*/, replace: 'Will be saved to: /home/user/Videos/Memories/alice_2025_memories.mp4'},
];

async function redactPage(page: Page) {
  // Redact input fields
  for (const {selector, value} of REDACTIONS) {
    await page.evaluate(
      ({sel, val}) => {
        const el = document.querySelector(sel) as HTMLInputElement | null;
        if (el) {
          // Set visual value only — do NOT dispatch events or NiceGUI
          // will sync the fake value to the Python backend
          el.value = val;
        }
      },
      {sel: selector, val: value}
    );
  }

  // Redact visible text nodes
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
}

async function redactPersonNames(page: Page) {
  // Quasar/Vue reactivity overwrites direct DOM text changes.
  // Use CSS to hide real text and overlay fake names instead.
  const genericNames = [
    'All people', 'Alice', 'Bob', 'Carol', 'David', 'Emma',
    'Frank', 'Grace', 'Henry', 'Iris', 'Jack', 'Kate',
    'Liam', 'Mia', 'Noah', 'Olivia', 'Paul', 'Quinn',
    'Rose', 'Sam', 'Tina', 'Uma', 'Victor', 'Wendy',
    'Xander', 'Yara', 'Zane', 'Amy', 'Ben', 'Chloe',
    'Dylan', 'Ella', 'Finn', 'Gina', 'Hugo', 'Ivy',
    'Jules', 'Kira', 'Leo', 'Nora', 'Owen',
  ];

  await page.evaluate((names) => {
    // Build CSS that hides real text and shows fake names via ::after
    let css = '';
    const options = document.querySelectorAll('[role="option"]');
    options.forEach((opt, i) => {
      if (i >= names.length) return;
      // Add a unique data attribute for CSS targeting
      opt.setAttribute('data-redact-idx', String(i));
      const div = opt.querySelector('div');
      if (div) {
        div.setAttribute('data-redact-idx', String(i));
      }
    });

    // Create a style element that uses CSS to replace visible text
    // Only target the inner div (text container), not the option wrapper
    names.forEach((name, i) => {
      css += `[role="option"][data-redact-idx="${i}"] > div {
        font-size: 0 !important;
        line-height: normal !important;
      }
      [role="option"][data-redact-idx="${i}"] > div::after {
        content: "${name}" !important;
        font-size: 14px !important;
      }
      `;
    });

    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
  }, genericNames);

  // Small delay to let CSS take effect
  await page.waitForTimeout(100);
}

async function waitForReady(page: Page) {
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(1500);
}

async function screenshot(page: Page, name: string, fullPage = false) {
  const filePath = path.join(SCREENSHOT_DIR, `${name}.png`);
  await page.screenshot({path: filePath, fullPage});
  console.log(`  Saved: ${name}.png`);
}

async function main() {
  fs.mkdirSync(SCREENSHOT_DIR, {recursive: true});

  console.log('Launching browser...');
  const browser = await chromium.launch({headless: true});
  const context = await browser.newContext({
    viewport: {width: 1280, height: 900},
  });
  const page = await context.newPage();

  try {
    // ── Step 1: Configuration ──
    console.log('\nStep 1: Configuration');
    await page.goto(BASE_URL);
    await waitForReady(page);
    await redactPage(page);
    await screenshot(page, 'step1-config-connected');

    // Screenshot: memory type preset cards
    console.log('  Capturing memory type presets...');
    const presetCard = page.locator('[class*="preset"], [class*="memory-type"]').first();
    if (await presetCard.isVisible()) {
      await screenshot(page, 'step1-preset-cards');
    }

    // Click "Year in Review" preset if visible (for a good screenshot flow)
    const yearPreset = page.getByText('Year in Review');
    if (await yearPreset.isVisible()) {
      await yearPreset.click();
      await page.waitForTimeout(500);
      await screenshot(page, 'step1-preset-selected');
    }

    // Open person dropdown
    const personCombo = page.getByRole('combobox', {name: 'Person'});
    if (await personCombo.isVisible()) {
      await personCombo.click();
      await page.waitForTimeout(500);
      await redactPersonNames(page);
      await screenshot(page, 'step1-person-dropdown');
      // Pick first real person (not "All people")
      const options = page.getByRole('option');
      const count = await options.count();
      if (count > 1) {
        await options.nth(1).click();
      } else {
        await page.getByRole('option', {name: 'All people'}).click();
      }
      await page.waitForTimeout(300);
    }

    // Expand cache management
    const cacheButton = page.getByRole('button', {name: /Cache Management/});
    if (await cacheButton.isVisible()) {
      await cacheButton.scrollIntoViewIfNeeded();
      await cacheButton.click();
      await page.waitForTimeout(500);
      await redactPage(page);
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await page.waitForTimeout(300);
      await screenshot(page, 'step1-cache-panel');
    }

    // ── Step 2: Clip Review ──
    console.log('\nStep 2: Clip Review');
    // Scroll back to top so "Next" button is clickable
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(200);
    await page.getByRole('button', {name: 'Next: Review Clips'}).click();

    // Wait for URL to change to step2
    await page.waitForURL('**/step2', {timeout: 30_000});

    // Wait for loading dialog to disappear (can take a while with many videos)
    console.log('  Waiting for videos to load...');
    await page.waitForFunction(
      () => !document.querySelector('[role="dialog"]'),
      {timeout: 300_000}
    );
    await waitForReady(page);
    // Viewport screenshot: shows top of clip review (stats, generate button, month list)
    await screenshot(page, 'step2-clip-review');

    // Expand first visible month to show clip thumbnails
    const monthButton = page.locator('button').filter({hasText: /\(\d+ clips?\)/}).first();
    if (await monthButton.isVisible()) {
      await monthButton.scrollIntoViewIfNeeded();
      await monthButton.click();
      await page.waitForTimeout(1000);
      // Scroll so expanded grid is centered in viewport
      await monthButton.scrollIntoViewIfNeeded();
      await screenshot(page, 'step2-clip-grid');
    }

    // Navigate to Refine Moments
    const refineButton = page.getByRole('button', {name: 'Next: Refine Moments'});
    await refineButton.scrollIntoViewIfNeeded();
    await refineButton.click();
    await waitForReady(page);
    // Viewport screenshot: shows top of refine view (stats, bulk actions, first few clips)
    await screenshot(page, 'step2-refine-moments');

    // ── Step 3: Generation Options ──
    console.log('\nStep 3: Generation Options');
    const continueButton = page.getByRole('button', {name: 'Continue to Generation'});
    await continueButton.scrollIntoViewIfNeeded();
    await continueButton.click();

    // Wait for actual navigation to /step3
    await page.waitForURL('**/step3', {timeout: 30_000});
    await waitForReady(page);
    // Step 3 fits in viewport — shows output settings, music, and summary
    await screenshot(page, 'step3-options');

    // ── Step 4: Preview & Export ──
    console.log('\nStep 4: Preview & Export');
    await page.getByRole('button', {name: 'Next: Preview & Export'}).click();

    // Wait for actual navigation to /step4
    await page.waitForURL('**/step4', {timeout: 30_000});
    await waitForReady(page);
    await redactPage(page);
    // Step 4 fits in viewport — shows summary, output path, and generate button
    await screenshot(page, 'step4-preview-export');

    console.log('\nDone! Screenshots saved to static/img/screenshots/');
    console.log('Next step: run blur-faces.ts to blur faces in thumbnails.');
  } catch (error) {
    console.error('Error taking screenshots:', error);
    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, 'error-state.png'),
      fullPage: true,
    });
  } finally {
    await browser.close();
  }
}

main();
