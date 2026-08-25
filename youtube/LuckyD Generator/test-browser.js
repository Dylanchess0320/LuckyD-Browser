#!/usr/bin/env node
// test-browser.js
// ---------------------------------------------------------------
// One-off sanity check: does Puppeteer actually have a working
// Chromium to launch? Run this after "npm install" / "npm rebuild
// puppeteer" to confirm the overflow-checker in build.js will work,
// without having to go hunt through cache folders by hand.
//
// Usage: node test-browser.js
// Delete this file whenever - it's not part of the build pipeline.
// ---------------------------------------------------------------

const puppeteer = require('puppeteer');

(async () => {
  console.log('[test] launching headless Chromium...');
  try {
    const browser = await puppeteer.launch({ headless: 'new' });
    const version = await browser.version();
    await browser.close();
    console.log(`[test] SUCCESS - launched fine (${version}).`);
    console.log('[test] the overflow checker in build.js is ready to go.');
  } catch (err) {
    console.log('[test] FAILED - Chromium did not launch.');
    console.log(`[test] error: ${err.message}`);
    console.log('[test] try: npm rebuild puppeteer   (or delete node_modules and run npm install fresh)');
    process.exit(1);
  }
})();
