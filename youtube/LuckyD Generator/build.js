#!/usr/bin/env node
// build.js
// ---------------------------------------------------------------
// Full deck build with a REAL overflow guarantee baked in.
//
// Runs the normal pipeline once (paginate -> AI images -> word
// filter -> video embed -> Marp render), then actually measures the
// rendered HTML in a headless browser (verify-fit.js). If any slide
// overflows the fixed 720px frame, it repairs JUST that slide -
// first by shrinking its text (compact, then tiny), and if it's
// STILL too dense even at tiny size, by splitting it into two
// slides - then re-renders (fast: just the Marp step, no need to
// regenerate AI images or re-run the filter). Repeats until every
// slide verifiably fits, or a safety cap on attempts is hit (in
// which case it tells you exactly which slide(s) still don't fit,
// rather than silently shipping a clipped one).
//
// Your original .md is never touched - all of this happens on a
// temp copy that gets deleted at the end.
//
// Usage:
//   node build.js <input.md> <output.html> <imagesDir> <bannedWordsPath> <themeDir> <nodePath> <marpCliPath>
// ---------------------------------------------------------------

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const [
  ,
  ,
  inPath,
  outHtmlPath,
  imagesDirArg,
  bannedWordsPath,
  themeDir,
  nodePath,
  marpCliPath,
] = process.argv;

if (!inPath || !outHtmlPath) {
  console.log(
    '[build] usage: node build.js <input.md> <output.html> <imagesDir> <bannedWordsPath> <themeDir> <nodePath> <marpCliPath>'
  );
  process.exit(1);
}

const here = __dirname;
const base = path.basename(inPath, path.extname(inPath));
const workDir = path.dirname(inPath);
const tmp = (tag) => path.join(workDir, `_${tag}_${base}.md`);

let puppeteerAvailable = true;
try {
  require.resolve('puppeteer');
} catch (e) {
  puppeteerAvailable = false;
}

function run(cmd, args) {
  execFileSync(cmd, args, { stdio: 'inherit' });
}
function runCapture(cmd, args) {
  return execFileSync(cmd, args, { encoding: 'utf8' });
}
function cleanup(paths) {
  for (const p of paths) {
    try {
      if (p && fs.existsSync(p)) fs.unlinkSync(p);
    } catch (e) {
      /* best effort */
    }
  }
}

// ---- pipeline steps (same scripts generator.bat used to call directly) ----
function paginate() {
  const out = tmp('paginated');
  run(nodePath, [path.join(here, 'autopaginate.js'), inPath, out, imagesDirArg || '']);
  return fs.existsSync(out) ? out : inPath;
}
function genImages(srcPath) {
  const out = tmp('imaged');
  const imgOutDir = path.join(path.dirname(outHtmlPath), 'images');
  run(nodePath, [path.join(here, 'genimage.js'), srcPath, out, imgOutDir]);
  return fs.existsSync(out) ? out : srcPath;
}
function filterWords(srcPath) {
  const out = tmp('filtered');
  run(nodePath, [path.join(here, 'filter-check.js'), srcPath, bannedWordsPath || '', out]);
  return fs.existsSync(out) ? out : srcPath;
}
function embedVideo(srcPath) {
  const embedScript = path.join(here, 'embedvideo.js');
  if (!fs.existsSync(embedScript)) return srcPath;
  const out = tmp('video');
  run(nodePath, [embedScript, srcPath, out]);
  return fs.existsSync(out) ? out : srcPath;
}
function renderHtml(srcPath) {
  run(nodePath, [
    marpCliPath,
    srcPath,
    '-o',
    outHtmlPath,
    '--theme-set',
    themeDir,
    '--allow-local-files',
    '--html',
  ]);
}
function checkOverflow() {
  if (!puppeteerAvailable) return null; // signal: can't check
  const out = runCapture(nodePath, [path.join(here, 'verify-fit.js'), outHtmlPath]);
  return JSON.parse(out.trim() || '[]');
}

// ---- slide-level helpers (operate on the FINAL rendered markdown) ----
function splitSlides(md) {
  return md.split(/\r?\n[ \t]*---[ \t]*\r?\n/);
}
function joinSlides(slides) {
  return slides.join('\n\n---\n\n');
}
function getClasses(slide) {
  const m = slide.match(/<!--\s*_class:\s*(.*?)\s*-->/);
  if (!m) return [];
  const raw = m[1].trim().replace(/^"(.*)"$/, '$1');
  return raw.split(/\s+/).filter(Boolean);
}
function setClasses(slide, classes) {
  const newLine = classes.length
    ? `<!-- _class: ${classes.length > 1 ? `"${classes.join(' ')}"` : classes[0]} -->`
    : '';
  const hasLine = /<!--\s*_class:\s*(.*?)\s*-->/.test(slide);
  if (hasLine) {
    return newLine
      ? slide.replace(/<!--\s*_class:\s*(.*?)\s*-->/, newLine)
      : slide.replace(/[ \t]*<!--\s*_class:\s*(.*?)\s*-->[ \t]*\n?/, '\n');
  }
  return newLine ? `${newLine}\n\n${slide.replace(/^\s+/, '')}` : slide;
}

// Escalate a slide's shrink level: (none) -> compact -> tiny -> split.
const SHRINK_LEVELS = ['compact', 'tiny'];
function escalateSlide(slide) {
  const classes = getClasses(slide);
  const currentLevel = SHRINK_LEVELS.findIndex((c) => classes.includes(c));
  if (currentLevel === -1) {
    const next = classes.filter((c) => !SHRINK_LEVELS.includes(c)).concat('compact');
    return { slide: setClasses(slide, next), needsSplit: false };
  }
  if (currentLevel === 0) {
    const next = classes.filter((c) => c !== 'compact').concat('tiny');
    return { slide: setClasses(slide, next), needsSplit: false };
  }
  // already tiny and STILL overflowing - text alone can't shrink any
  // further without becoming unreadable, so split into two slides.
  return { slide, needsSplit: true };
}

// Split one overflowing slide into two, roughly in half, at a blank-
// line block boundary if there is one (keeps paragraphs/list-groups
// intact); falls back to a straight line-count halving for a single
// unbroken block (e.g. one long bullet list with no blank lines).
function splitSlideInHalf(slide) {
  const classes = getClasses(slide).filter((c) => !SHRINK_LEVELS.includes(c));
  const withoutClass = setClasses(slide, []).trim();
  const headingMatch = withoutClass.match(/^##\s+(.+)$/m);
  const heading = headingMatch ? headingMatch[0] : null;
  const body = heading ? withoutClass.replace(heading, '').trim() : withoutClass;

  const blocks = body.split(/\r?\n\s*\r?\n/).filter((b) => b.trim());
  let firstBody, secondBody;
  if (blocks.length >= 2) {
    const mid = Math.ceil(blocks.length / 2);
    firstBody = blocks.slice(0, mid).join('\n\n');
    secondBody = blocks.slice(mid).join('\n\n');
  } else {
    const lines = body.split(/\r?\n/);
    const mid = Math.ceil(lines.length / 2) || 1;
    firstBody = lines.slice(0, mid).join('\n');
    secondBody = lines.slice(mid).join('\n');
  }

  const first = `${heading ? heading + '\n\n' : ''}${firstBody}`;
  const second = `${heading ? heading + ' (cont.)\n\n' : ''}${secondBody}`;
  return [setClasses(first, classes), setClasses(second, classes)];
}

// =====================================================================
const MAX_ATTEMPTS = 8;

let currentMd = paginate();
currentMd = genImages(currentMd);
currentMd = filterWords(currentMd);
currentMd = embedVideo(currentMd);
renderHtml(currentMd);

if (!puppeteerAvailable) {
  console.log(
    '[build] done - but the real-browser overflow check was SKIPPED because puppeteer isn\'t installed yet.'
  );
  console.log('[build] run "npm install" once in this folder to turn on the overflow guarantee.');
  cleanup([tmp('paginated'), tmp('imaged'), tmp('filtered'), tmp('video')]);
  process.exit(0);
}

let attempt = 0;
let overflowing = checkOverflow();

while (overflowing && overflowing.length > 0 && attempt < MAX_ATTEMPTS) {
  attempt++;
  console.log(`[build] pass ${attempt}: ${overflowing.length} slide(s) overflow - repairing...`);

  const rendered = fs.readFileSync(currentMd, 'utf8');
  const fmMatch = rendered.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  const fm = fmMatch ? fmMatch[0] : '';
  const body = fmMatch ? rendered.slice(fmMatch[0].length) : rendered;

  let slides = splitSlides(body);

  // work from the end backward so splicing in a new slide doesn't
  // shift the indices of overflowing slides we haven't handled yet
  const sorted = [...overflowing].sort((a, b) => b.index - a.index);
  for (const { index } of sorted) {
    if (index == null || index >= slides.length || index < 0) continue;
    const { slide, needsSplit } = escalateSlide(slides[index]);
    if (needsSplit) {
      const [a, b] = splitSlideInHalf(slides[index]);
      slides[index] = a;
      slides.splice(index + 1, 0, b);
    } else {
      slides[index] = slide;
    }
  }

  fs.writeFileSync(currentMd, fm + joinSlides(slides), 'utf8');
  renderHtml(currentMd);
  overflowing = checkOverflow();
}

cleanup([tmp('paginated'), tmp('imaged'), tmp('filtered'), tmp('video')]);

if (overflowing && overflowing.length > 0) {
  console.log(
    `[build] WARNING: ${overflowing.length} slide(s) still don't fit after ${MAX_ATTEMPTS} repair passes (slide index ${overflowing
      .map((o) => o.index)
      .join(', ')}). Every other slide is verified clean.`
  );
} else {
  console.log(
    `[build] done - every slide verified to fit inside the frame in a real browser${
      attempt ? ` (${attempt} repair pass${attempt === 1 ? '' : 'es'})` : ''
    }.`
  );
}
