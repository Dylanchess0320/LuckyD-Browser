#!/usr/bin/env node
// verify-fit.js
// ---------------------------------------------------------------
// Loads a rendered Marp HTML export in a real headless browser and
// measures whether each slide's ACTUAL content overflows the fixed
// 1280x720 slide box. This is the only way to know for sure -
// character-count heuristics (like autopaginate.js's weight budget)
// are estimates; this checks real rendered pixels, in the real
// theme, with the real fonts, after images have actually loaded.
//
// Usage: node verify-fit.js <exported.html>
// Prints JSON to stdout: [{ index, overflowPx }, ...] - ONLY the
// slides that actually overflow. Empty array [] = everything fits.
// ---------------------------------------------------------------

const puppeteer = require('puppeteer');
const path = require('path');

async function main() {
  const htmlPath = process.argv[2];
  if (!htmlPath) {
    console.error('[verify-fit] usage: node verify-fit.js <exported.html>');
    process.exit(1);
  }

  const browser = await puppeteer.launch({ headless: 'new' });
  try {
    const page = await browser.newPage();
    await page.goto('file://' + path.resolve(htmlPath).replace(/\\/g, '/'), {
      waitUntil: 'networkidle0',
    });

    // Wait for every image to actually finish loading/decoding - an
    // image still loading reports zero intrinsic height, which would
    // make a slide look artificially short and hide real overflow.
    await page.evaluate(async () => {
      const imgs = Array.from(document.images);
      await Promise.all(
        imgs.map((img) =>
          img.complete
            ? Promise.resolve()
            : new Promise((res) => {
                img.addEventListener('load', res);
                img.addEventListener('error', res);
              })
        )
      );
    });

    const results = await page.evaluate(() => {
      const sections = Array.from(document.querySelectorAll('section'));
      return sections.map((sec, i) => {
        // scrollHeight/clientHeight are layout properties measured
        // BEFORE any CSS transform Marp applies for on-screen scaling,
        // so this is accurate regardless of zoom/window size.
        const overflowPx = Math.round(sec.scrollHeight - sec.clientHeight);
        return { index: i, overflowPx };
      });
    });

    // small tolerance for sub-pixel rounding noise
    const overflowing = results.filter((r) => r.overflowPx > 2);
    console.log(JSON.stringify(overflowing));
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(`[verify-fit] ${err.message}`);
  process.exit(1);
});
