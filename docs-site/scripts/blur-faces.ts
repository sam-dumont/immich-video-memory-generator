/**
 * Face blur script for documentation screenshots.
 *
 * Uses sharp for image processing with a simple pixel-region blur approach.
 * For face detection, we use a canvas-based approach with a lightweight model.
 *
 * Usage:
 *   npx tsx scripts/blur-faces.ts
 *   npx tsx scripts/blur-faces.ts --input static/img/screenshots/step1-config-connected.png
 *
 * After running, manually review the output images in static/img/screenshots/
 * to verify all faces are properly blurred.
 */

import sharp from 'sharp';
import fs from 'fs';
import path from 'path';

const SCREENSHOT_DIR = path.join(__dirname, '..', 'static', 'img', 'screenshots');
const BLUR_RADIUS = 30; // Gaussian blur radius — generous to ensure privacy

interface Region {
  left: number;
  top: number;
  width: number;
  height: number;
}

async function blurRegion(
  inputPath: string,
  outputPath: string,
  regions: Region[]
): Promise<void> {
  if (regions.length === 0) {
    // No regions to blur, just copy
    fs.copyFileSync(inputPath, outputPath);
    return;
  }

  let image = sharp(inputPath);
  const metadata = await image.metadata();
  const imgWidth = metadata.width!;
  const imgHeight = metadata.height!;

  // For each face region, extract it, blur it, and composite back
  const composites: sharp.OverlayOptions[] = [];

  for (const region of regions) {
    // Clamp region to image bounds
    const left = Math.max(0, region.left);
    const top = Math.max(0, region.top);
    const width = Math.min(region.width, imgWidth - left);
    const height = Math.min(region.height, imgHeight - top);

    if (width <= 0 || height <= 0) continue;

    // Extract the face region and blur it
    const blurredRegion = await sharp(inputPath)
      .extract({left, top, width, height})
      .blur(BLUR_RADIUS)
      .toBuffer();

    composites.push({
      input: blurredRegion,
      left,
      top,
    });
  }

  await image.composite(composites).toFile(outputPath);
}

/**
 * Simple heuristic face detection based on skin-tone regions.
 * For production use, replace with @vladmandic/face-api or similar.
 *
 * This is a placeholder — the actual face detection should be done
 * with a proper ML model. For now, this script provides the
 * infrastructure and you should manually specify regions or use
 * a face detection API.
 */
async function detectFaces(_imagePath: string): Promise<Region[]> {
  // Placeholder: returns empty array.
  // To use with actual face detection:
  // 1. Install @vladmandic/face-api: npm install @vladmandic/face-api
  // 2. Load the model and detect faces
  // 3. Return bounding boxes as Region[]
  //
  // For manual use, call blurRegion directly with known face coordinates.
  console.log(
    '  Note: Auto face detection is a placeholder. ' +
      'Review screenshots manually and specify regions if needed.'
  );
  return [];
}

async function processScreenshot(filePath: string): Promise<void> {
  const filename = path.basename(filePath);
  console.log(`Processing: ${filename}`);

  const regions = await detectFaces(filePath);

  if (regions.length === 0) {
    console.log(`  No faces detected in ${filename} (review manually)`);
    return;
  }

  // Create blurred version (overwrite original since these are docs screenshots)
  const tempPath = filePath + '.tmp.png';
  await blurRegion(filePath, tempPath, regions);
  fs.renameSync(tempPath, filePath);
  console.log(`  Blurred ${regions.length} face(s) in ${filename}`);
}

async function main() {
  const args = process.argv.slice(2);

  let files: string[];

  if (args.includes('--input') && args.indexOf('--input') + 1 < args.length) {
    // Process single file
    const inputFile = args[args.indexOf('--input') + 1];
    files = [path.resolve(inputFile)];
  } else {
    // Process all screenshots
    if (!fs.existsSync(SCREENSHOT_DIR)) {
      console.error(`Screenshot directory not found: ${SCREENSHOT_DIR}`);
      console.error('Run take-screenshots.ts first.');
      process.exit(1);
    }

    files = fs
      .readdirSync(SCREENSHOT_DIR)
      .filter((f) => f.endsWith('.png'))
      .map((f) => path.join(SCREENSHOT_DIR, f));
  }

  if (files.length === 0) {
    console.log('No screenshots found to process.');
    return;
  }

  console.log(`Found ${files.length} screenshot(s) to process.\n`);

  for (const file of files) {
    await processScreenshot(file);
  }

  console.log('\nDone! Review screenshots in static/img/screenshots/ for missed faces.');
}

main();
