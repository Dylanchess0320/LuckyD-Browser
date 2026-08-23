#!/usr/bin/env node
// autopaginate.js
// ---------------------------------------------------------------
// Makes a processed COPY of a markdown file for Marp. Two jobs:
//
// 1) PAGINATION
//    If the file already has slide breaks ("---" on its own line,
//    outside the frontmatter block), it's treated as an authored
//    deck. If it does NOT, it's treated as a raw export (e.g. a
//    pasted AI chat transcript) and gets auto-split:
//      - frontmatter (theme: ...) is preserved if present, else added
//      - "Thinking" / reasoning blockquote blocks are stripped
//      - stray "---" dividers used as plain content separators are
//        stripped (chat exports use these; they'd otherwise collide
//        with Marp's own slide-break syntax and fracture slides)
//      - conversational role headers ("## You" / "## Nexus" etc.) are
//        demoted to inline labels so they don't become empty slides
//      - a title slide is generated from the first H1
//      - remaining content is chunked into slides at H2 AND H3
//        headings (so a subsection heading always stays with its own
//        content), and long chunks are split further so nothing
//        overflows a slide
//      - slide classes (lead / compact / quote) are assigned based
//        on what's actually on the slide
//
// 2) AUTO-IMAGE INSERTION
//    If an images folder is passed as the 3rd argument and it has
//    any image files in it, every slide that doesn't already reference
//    an image (real or pending ![gen: ...]) and isn't a lead/big/quote/
//    stat/cta/split/image-right/image-left layout gets one:
//      - if a slide's heading/text contains an image's filename
//        (e.g. "AAPL" matches images/aapl.png), that image is used
//      - otherwise the least-recently-used image is picked, so
//        images cycle round-robin across the deck
//      - slides with a single short block of text get a proper
//        side-by-side "image-right"/"image-left" layout (alternating
//        for visual rhythm), keeping any existing font-size class
//        (e.g. "compact") alongside it
//      - denser slides (but not ones containing a table) get the image
//        appended full-width below their content instead - build.js's
//        overflow check will shrink/split anything that doesn't fit
//    This step runs on authored decks too, not just auto-paginated
//    ones - any eligible plain slide missing an image can get one.
//
// Usage: node autopaginate.js <input.md> <output.md> [imagesDir]
// Never overwrites the input. Prints a one-line summary to stdout.
// ---------------------------------------------------------------

const fs = require('fs');
const path = require('path');

const [, , inPath, outPath, imagesDirArg] = process.argv;

if (!inPath || !outPath) {
  console.log('[paginate] usage: node autopaginate.js <input.md> <output.md> [imagesDir]');
  process.exit(0);
}

if (!fs.existsSync(inPath)) {
  console.log(`[paginate] input not found: ${inPath}`);
  process.exit(0);
}

const raw = fs.readFileSync(inPath, 'utf8');

const MAX_LINES_PER_SLIDE = 26;

// Raw markdown line count is a poor proxy for how much VERTICAL space a
// slide actually needs - a single long bullet, or a link-heavy
// reference list where each list item renders as a full text line,
// wraps into several visual lines even though it's one line of
// markdown source. Weight longer lines (and markdown links, which take
// a full line of their own in a list) more heavily so dense sections
// get split BEFORE they overflow the fixed 720px slide height - Marp
// clips overflow, which was silently cutting content (including
// clickable links) off the bottom of the slide. Defined at the top
// level (not just inside the auto-pagination branch below) so the
// image-insertion step further down can use it too, on authored decks
// as well as auto-split ones.
function lineWeight(line) {
  const linkBonus = (line.match(/\[[^\]]*\]\([^)]+\)/g) || []).length;
  return Math.max(1, Math.ceil(line.length / 90)) + linkBonus;
}
function weighOf(lines) {
  return lines.reduce((sum, l) => sum + lineWeight(l), 0);
}

let frontmatter = null;
let body = raw;
const fmMatch = raw.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
if (fmMatch) {
  frontmatter = fmMatch[0];
  body = raw.slice(fmMatch[0].length);
}

// raw AI chat exports (ChatGPT/Claude/Nexus/etc.) commonly use "---" as
// an ordinary markdown horizontal rule between sections, which collides
// with Marp's slide-break syntax. Detect chat-export signatures and, if
// found, always treat the file as needing full auto-pagination instead
// of trusting the naive "---" check.
const looksLikeChatExport =
  /^_Exported from .+·.+_$/m.test(body) ||
  /^>\s*\*\*Thinking\*\*/m.test(body) ||
  /^##\s+(You|User|Assistant|Nexus|ChatGPT|Claude|Human|AI)\s*$/im.test(body);
const hasBreaks = !looksLikeChatExport && /^[ \t]*---[ \t]*$/m.test(body);

let finalBody;
let paginateSummary;

if (hasBreaks) {
  finalBody = body;
  paginateSummary = 'slide breaks found - deck left as authored';
} else {
  function stripThinkingBlocks(text) {
    const lines = text.split(/\r?\n/);
    const out = [];
    let i = 0;
    while (i < lines.length) {
      if (/^>\s*/.test(lines[i])) {
        let j = i;
        const block = [];
        while (j < lines.length && /^>\s*/.test(lines[j])) {
          block.push(lines[j]);
          j++;
        }
        const firstLine = block[0].replace(/^>\s*/, '').replace(/\*\*/g, '').trim();
        if (/^thinking/i.test(firstLine)) {
          i = j;
          continue;
        } else {
          out.push(...block);
          i = j;
          continue;
        }
      }
      out.push(lines[i]);
      i++;
    }
    return out.join('\n');
  }

  body = stripThinkingBlocks(body);

  // ---- strip stray "---" horizontal rules from the source content ----
  // these are ordinary decorative dividers in a raw export, not slide
  // breaks - our own chunking logic decides where slides split, so any
  // leftover "---" would be misread by Marp as an extra, unintended
  // slide break in the middle of a chunk.
  body = body.replace(/^[ \t]*---[ \t]*$/gm, '');

  function stripRoleScaffolding(text) {
    const USER_ROLE = /^##\s+(You|User|Human)\s*$/i;
    const ASSISTANT_ROLE = /^##\s+(Assistant|ChatGPT|Claude|Nexus|AI)\s*$/i;
    const lines = text.split(/\r?\n/);
    const out = [];
    let skippingUserTurn = false;
    for (const line of lines) {
      if (USER_ROLE.test(line)) {
        skippingUserTurn = true;
        continue;
      }
      if (skippingUserTurn) {
        if (/^##\s+/.test(line)) {
          skippingUserTurn = false;
        } else {
          continue;
        }
      }
      if (ASSISTANT_ROLE.test(line)) {
        continue;
      }
      out.push(line);
    }
    return out.join('\n');
  }

  // ---- remove conversational scaffolding entirely ("## You" prompt turn, ----
  // "## Nexus"/"## Claude"/etc. role header) - the deck should contain only
  // the actual answer content, with no assistant name mentioned anywhere
  // and no leftover copy of the original question.
  if (looksLikeChatExport) {
    body = body.replace(/^_Exported from .+·.+_$/gm, '');
    body = stripRoleScaffolding(body);
  }

  const lines = body.split(/\r?\n/);
  let title = null;
  let subtitle = null;
  let titleLineIdx = -1;
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^#\s+(.+)/);
    if (m) {
      title = m[1].trim();
      titleLineIdx = i;
      if (lines[i + 1] && /^##\s+/.test(lines[i + 1])) {
        subtitle = lines[i + 1].replace(/^##\s+/, '').trim();
      }
      break;
    }
  }
  if (titleLineIdx !== -1) {
    const removeCount = subtitle ? 2 : 1;
    lines.splice(titleLineIdx, removeCount);
    body = lines.join('\n');
  }

  function splitOnHeadings(text) {
    // split on H2 ("## ") AND H3 ("### ") - many docs (especially AI
    // chat answers) use ### for each numbered subsection, and treating
    // only ## as a boundary let line-count chunking cut a heading away
    // from its own content.
    const secLines = text.split(/\r?\n/);
    const sections = [];
    let current = { heading: null, lines: [] };
    for (const line of secLines) {
      if (/^#{2,3}\s+/.test(line)) {
        if (current.heading !== null || current.lines.some(l => l.trim())) {
          sections.push(current);
        }
        current = { heading: line.replace(/^#{2,3}\s+/, '').trim(), lines: [] };
      } else {
        current.lines.push(line);
      }
    }
    if (current.heading !== null || current.lines.some(l => l.trim())) {
      sections.push(current);
    }
    return sections;
  }

  // ---- table-aware splitting (repeats the header row) ------------------
  // A wide/tall markdown table doesn't have any blank lines inside it, so
  // the paragraph-boundary logic below sees it as ONE block - if that
  // block alone is heavier than a full slide, there was previously
  // nothing to split it against, and Marp silently clipped the extra
  // rows off the bottom. Split long tables into row-groups instead, each
  // with the header/separator repeated so every piece still renders as a
  // valid, readable table.
  const MAX_TABLE_ROWS_PER_SLIDE = 6;

  function extractTable(lines) {
    const rowRe = /^\s*\|.*\|\s*$/;
    const sepRe = /^\s*\|?[\s:|-]+\|?\s*$/;
    let start = -1;
    for (let i = 0; i < lines.length - 1; i++) {
      if (rowRe.test(lines[i]) && sepRe.test(lines[i + 1]) && /-/.test(lines[i + 1])) {
        start = i;
        break;
      }
    }
    if (start === -1) return null;
    let end = start + 2;
    while (end < lines.length && rowRe.test(lines[end])) end++;
    return {
      before: lines.slice(0, start),
      header: lines[start],
      separator: lines[start + 1],
      bodyRows: lines.slice(start + 2, end),
      after: lines.slice(end),
    };
  }

  function chunkTableSection(section) {
    const content = section.lines.join('\n').replace(/^\s+|\s+$/g, '');
    if (!content) return null;
    const contentLines = content.split(/\r?\n/);
    const table = extractTable(contentLines);
    if (!table) return null;

    // Table cells are far narrower than the full slide width (split
    // across N columns), and cells here often pack several <br />
    // separated items into ONE row - each forces its own hard line break
    // regardless of length. The generic lineWeight() assumes prose
    // wrapping at full slide width, so it badly undercounts a row like
    // this. Weight rows with a column-aware estimate instead: ~1100px of
    // usable slide width split across columns, at roughly 9px/char for
    // the compact table font (17px) tables actually render at.
    // Real geometry, computed from the theme CSS (not guessed):
    //   content area  = 1140x620px (1280x720 slide minus 70px/50px padding)
    //   compact table font/padding = 17px text, 4px/8px cell padding, 1px border
    // A column's usable text width is its share of 1140px minus that cell's
    // own padding+border, and ~8.5px/char is a reasonable average glyph
    // width for 17px Segoe UI/Arial.
    const numCols = Math.max(1, table.header.split('|').map(s => s.trim()).filter(Boolean).length);
    const usableColWidthPx = Math.max(60, Math.floor(1140 / numCols) - 18);
    const approxCharsPerLine = Math.max(20, Math.floor(usableColWidthPx / 8.5));

    // A markdown table row is "| cell1 | cell2 | ... |" - split on the
    // column delimiter FIRST, then measure each cell independently. The
    // row's actual rendered height is set by whichever single cell wraps
    // to the most lines - it is NOT the sum of every cell's line count.
    // (That was the bug: summing col1 + every <br>-separated item in col2
    // together massively overcounted height for any row where one column
    // just holds a short label next to a column packed with several
    // <br>-separated items.)
    function splitRowCells(row) {
      let r = row.trim();
      if (r.startsWith('|')) r = r.slice(1);
      if (r.endsWith('|')) r = r.slice(0, -1);
      return r.split('|');
    }
    function cellLineCount(cell) {
      const segments = cell.split(/<br\s*\/?>/i);
      const linkBonus = (cell.match(/\[[^\]]*\]\([^)]+\)/g) || []).length;
      let lines = 0;
      for (const seg of segments) {
        const text = seg.replace(/<[^>]+>/g, '').replace(/[*_`]/g, '').trim();
        lines += Math.max(1, Math.ceil(text.length / approxCharsPerLine));
      }
      return lines + linkBonus;
    }
    // Budget is now in real rendered-line units (~20.4px/line at 17px
    // compact font), calibrated against the actual available space left
    // after the heading + source-citation line on a compact slide: ~472px
    // for the table, ~30px for the header row, ~442px left for body rows
    // -> ~21-22 real lines of body budget. A 6-item <br>-packed row (like
    // the "Sub-Topic / Sample Episode Angles" table) needs ~6-7 lines, so
    // this correctly caps that table at ~3 rows/slide instead of
    // overcounting to 1 (too sparse) or undercounting to 4+ (cutoff).
    // Simple short-cell tables are unaffected - they're already capped by
    // MAX_TABLE_ROWS_PER_SLIDE=6 for readability well before this budget
    // would bind.
    const MAX_TABLE_ROW_WEIGHT = 22;
    function tableRowWeight(row) {
      const cells = splitRowCells(row);
      return Math.max(1, ...cells.map(cellLineCount));
    }

    // Split on ROW COUNT *or* WEIGHT overflow - a table can have few rows
    // but still be too dense (long/multi-line cell text) to fit one
    // slide. Whichever table falls through here unsplit must actually
    // fit, or it risks getting cut apart later by the non-table-aware
    // paragraph splitter, which severs body rows from the header/
    // separator they need to still be recognized as a table at all.
    const totalRowWeight = table.bodyRows.reduce((sum, r) => sum + tableRowWeight(r), 0);
    const exceedsRows = table.bodyRows.length > MAX_TABLE_ROWS_PER_SLIDE;
    const exceedsWeight = totalRowWeight > MAX_TABLE_ROW_WEIGHT;
    if (!exceedsRows && !exceedsWeight) return null;

    // Pack rows by weight (like explodeOversizedParagraph), reserving
    // budget for the repeated header+separator on every slide, and still
    // capping at MAX_TABLE_ROWS_PER_SLIDE rows for readability. Use the
    // same per-cell, max-based measurement as body rows (the separator
    // row itself renders as a hairline, not text, so it doesn't add height).
    const headerWeight = tableRowWeight(table.header);
    const rowGroups = [];
    let current = [];
    let currentWeight = headerWeight;
    for (const row of table.bodyRows) {
      const w = tableRowWeight(row);
      if (
        current.length > 0 &&
        (current.length >= MAX_TABLE_ROWS_PER_SLIDE || currentWeight + w > MAX_TABLE_ROW_WEIGHT)
      ) {
        rowGroups.push(current);
        current = [];
        currentWeight = headerWeight;
      }
      current.push(row);
      currentWeight += w;
    }
    if (current.length) rowGroups.push(current);

    return rowGroups.map((rows, idx) => {
      const tableLines = [table.header, table.separator, ...rows];
      const lines = idx === 0 ? [...table.before, ...tableLines] : tableLines;
      if (idx === rowGroups.length - 1) lines.push(...table.after);
      return {
        heading: rowGroups.length > 1 ? `${section.heading} (${idx + 1}/${rowGroups.length})` : section.heading,
        lines,
      };
    });
  }

  // ---- oversized-paragraph splitting -----------------------------------
  // Similarly, one very long paragraph (a dense wall of prose, a long
  // blockquote, a long code block) has no internal blank lines either, so
  // it could sail past a slide's weight budget as a single unsplittable
  // unit. If a paragraph alone is heavier than a full slide: split it at
  // its own line breaks if it has any (code blocks, multi-line quotes),
  // or - for one truly giant single line - wrap it at word boundaries so
  // it can be spread across as many slides as it actually needs.
  function explodeOversizedParagraph(para) {
    if (weighOf(para) <= MAX_LINES_PER_SLIDE) return [para];

    if (para.length > 1) {
      const pieces = [];
      let current = [];
      let currentWeight = 0;
      for (const line of para) {
        const w = lineWeight(line);
        if (currentWeight > 0 && currentWeight + w > MAX_LINES_PER_SLIDE) {
          pieces.push(current);
          current = [];
          currentWeight = 0;
        }
        current.push(line);
        currentWeight += w;
      }
      if (current.length) pieces.push(current);
      return pieces;
    }

    const maxCharsPerLine = Math.floor(MAX_LINES_PER_SLIDE * 90 * 0.85);
    const words = para[0].split(' ');
    const wrapped = [];
    let cur = '';
    for (const w of words) {
      if (cur && cur.length + 1 + w.length > maxCharsPerLine) {
        wrapped.push(cur);
        cur = w;
      } else {
        cur = cur ? `${cur} ${w}` : w;
      }
    }
    if (cur) wrapped.push(cur);
    return wrapped.map((line) => [line]);
  }

  function chunkSection(section) {
    const tableChunks = chunkTableSection(section);
    if (tableChunks) return tableChunks;

    let content = section.lines.join('\n').replace(/^\s+|\s+$/g, '');
    if (!content) return [];

    const contentLines = content.split(/\r?\n/);
    if (weighOf(contentLines) <= MAX_LINES_PER_SLIDE) {
      return [{ heading: section.heading, lines: contentLines }];
    }

    const paras = [];
    let buf = [];
    for (const l of contentLines) {
      if (l.trim() === '') {
        if (buf.length) { paras.push(buf); buf = []; }
      } else {
        buf.push(l);
      }
    }
    if (buf.length) paras.push(buf);

    const explodedParas = paras.flatMap(explodeOversizedParagraph);

    const chunks = [];
    let current = [];
    let currentWeight = 0;

    for (const para of explodedParas) {
      const paraWeight = weighOf(para);
      if (currentWeight > 0 && currentWeight + paraWeight > MAX_LINES_PER_SLIDE) {
        chunks.push(current);
        current = [];
        currentWeight = 0;
      }
      current.push(...para, '');
      currentWeight += paraWeight + 1;
    }
    if (current.length) chunks.push(current);

    return chunks.map((lines, idx) => ({
      heading: chunks.length > 1 ? `${section.heading} (${idx + 1}/${chunks.length})` : section.heading,
      lines,
    }));
  }

  const rawSections = splitOnHeadings(body);
  let slideChunks = [];
  for (const sec of rawSections) {
    slideChunks.push(...chunkSection(sec));
  }
  slideChunks = slideChunks.filter(c => c.lines.some(l => l.trim()) || c.heading);

  function classify(chunk) {
    const text = chunk.lines.join('\n').trim();
    const nonEmpty = chunk.lines.filter(l => l.trim());
    const isAllBlockquote = nonEmpty.length > 0 && nonEmpty.every(l => /^>\s*/.test(l));
    const hasTable = /^\s*\|.*\|\s*$/m.test(text);
    const linkCount = (text.match(/\[[^\]]*\]\([^)]+\)/g) || []).length;
    const listItemCount = nonEmpty.filter(l => /^[\s]*[-*]\s+|^[\s]*\d+\.\s+/.test(l)).length;
    if (isAllBlockquote) return 'quote';
    if (hasTable) return 'compact';
    // link-heavy slides (reference/source lists) and long bullet lists
    // overflow the fixed 720px slide height at normal font size - shrink
    // them so every link stays inside the visible, clickable area instead
    // of getting clipped off the bottom.
    if (linkCount >= 4 || listItemCount >= 7) return 'compact';
    return null;
  }

  const outLines = [];
  if (title) {
    outLines.push('<!-- _class: lead -->');
    outLines.push('');
    outLines.push(`# ${title}`);
    if (subtitle) outLines.push(`## ${subtitle}`);
    outLines.push('');
  }
  for (const chunk of slideChunks) {
    if (outLines.length > 0) {
      outLines.push('---');
      outLines.push('');
    }
    const cls = classify(chunk);
    if (cls) {
      outLines.push(`<!-- _class: ${cls} -->`);
      outLines.push('');
    }
    if (chunk.heading) {
      outLines.push(`## ${chunk.heading}`);
      outLines.push('');
    }
    outLines.push(...chunk.lines);
    outLines.push('');
  }

  finalBody = outLines.join('\n').replace(/\n{3,}/g, '\n\n');
  paginateSummary = `no slide breaks found - auto-split into ${slideChunks.length + (title ? 1 : 0)} slides`;
}

function slideTextForPrompt(slide) {
  let t = slide.replace(/<!--[\s\S]*?-->/g, ' ');
  t = t.replace(/^#{1,3}\s+/gm, '');
  t = t.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1');
  t = t.replace(/[*_`>]/g, '');
  t = t.replace(/\s+/g, ' ').trim();
  return t;
}

// A rotating set of comedic angles so slides don't all get the same
// flavor of joke. Rotation is by SCENE NUMBER in buildGenPrompt (not a
// text hash), so every slide in the deck gets its own distinct look.
const COMEDIC_STYLES = [
  'absurd visual punchline, exaggerated comedic facial expressions',
  'slapstick energy, over-the-top comedic timing, silly sight gag',
  'deadpan comedic irony, a mundane thing treated as a huge dramatic deal',
  'meme-style absurdist humor, unexpected ridiculous visual twist',
  'goofy exaggerated proportions for comic effect, playful chaos',
  'literal/on-the-nose visual pun, taken way too far for a laugh',
];

function buildGenPrompt(slide, sceneNo) {
  const text = slideTextForPrompt(slide).slice(0, 160);
  // Style rotates by SCENE NUMBER (not text hash) so adjacent slides never
  // share a look, and "unique scene N" guarantees the prompt itself is
  // distinct per slide — which keeps the genimage cache from ever handing
  // two slides the same picture.
  const style = COMEDIC_STYLES[(sceneNo - 1) % COMEDIC_STYLES.length];
  return `${text}, hilarious ${style}, vivid concrete comedic illustration, cinematic lighting, dark background with neon green accents, unique scene ${sceneNo}`;
}

function getAvailableImages(imagesDir) {
  if (!imagesDir) return [];
  try {
    if (!fs.existsSync(imagesDir)) return [];
    const exts = new Set(['.jpg', '.jpeg', '.png', '.gif', '.webp']);
    return fs.readdirSync(imagesDir)
      .filter(f => exts.has(path.extname(f).toLowerCase()))
      .sort();
  } catch (e) {
    return [];
  }
}

function countContentBlocks(text) {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  return trimmed.split(/\r?\n\s*\r?\n/).filter(b => b.trim()).length;
}

function matchImageByName(slideText, images) {
  const lower = slideText.toLowerCase();
  for (const img of images) {
    const base = path.basename(img, path.extname(img)).toLowerCase();
    if (base.length >= 3 && lower.includes(base)) return img;
  }
  return null;
}

function insertImagesIntoSlides(text, images) {
  const parts = text.split(/\r?\n[ \t]*---[ \t]*\r?\n/);
  // Local images already claimed by an earlier slide in THIS deck. Each
  // image is used at most once — repeats are exactly what we're avoiding.
  const usedLocal = new Set();
  let sideToggle = 0;
  let insertedCount = 0;

  // Side-by-side (image-right/image-left) gives the text roughly HALF
  // the slide's width, so the same text wraps into roughly double the
  // number of visual lines compared to full-width. Cap side-by-side
  // placement to content short enough to survive that halving;
  // MAX_LINES_PER_SLIDE assumes full slide width, so this budget is
  // roughly half of it.
  const SIDE_BY_SIDE_MAX_WEIGHT = Math.round(MAX_LINES_PER_SLIDE * 0.45);

  // Slides too dense for side-by-side don't have to go image-less -
  // they can still take a full-width image appended BELOW their
  // content, as long as there's some headroom left. build.js's
  // real-browser overflow check auto-shrinks (then splits) anything
  // that still doesn't fit, so this is a safe upper bound, not a
  // guarantee - it just means "dense but not console-log oversized".
  const BOTTOM_IMAGE_MAX_WEIGHT = Math.round(MAX_LINES_PER_SLIDE * 0.8);

  // Layouts with a fixed, image-free shape in the CSS (lead/big/quote/
  // stat/cta/split, or a slide that's already side-by-side) - unsafe to
  // bolt an image onto these. A pure font-size modifier like "compact"
  // is NOT in this set: it layers fine on top of image-right/left or a
  // bottom-appended image, so a dense/link-heavy/table-classified slide
  // isn't automatically disqualified from getting an image too.
  const LAYOUT_LOCKED = new Set(['lead', 'big', 'quote', 'stat', 'cta', 'split', 'image-right', 'image-left']);

  function getExistingClasses(slide) {
    const m = slide.match(/<!--\s*_class:\s*(.*?)\s*-->/);
    if (!m) return [];
    return m[1].trim().replace(/^"(.*)"$/, '$1').split(/\s+/).filter(Boolean);
  }
  function stripClassComment(slide) {
    return slide.replace(/[ \t]*<!--\s*_class:\s*(.*?)\s*-->[ \t]*\n?/, '');
  }

  const outParts = parts.map((slide, slideIdx) => {
    if (!slide.trim()) return slide;
    if (/!\[[^\]]*\]\([^)]+\)/.test(slide)) return slide; // already has a real image
    if (/!\[gen:/.test(slide)) return slide; // already has a pending AI-image tag

    const existingClasses = getExistingClasses(slide);
    if (existingClasses.some((c) => LAYOUT_LOCKED.has(c))) return slide;

    const withoutHeading = stripClassComment(slide).replace(/^#{1,3}\s+.*$/m, '');
    const hasTable = /^\s*\|.*\|\s*$/m.test(withoutHeading);
    const blocks = countContentBlocks(withoutHeading);
    const weight = weighOf(withoutHeading.split(/\r?\n/));

    // UNIQUE IMAGES POLICY: a local image is used at most once per deck.
    // Name-matched images win first; otherwise the slide takes a fresh,
    // never-yet-used local image if one remains. When the folder runs dry,
    // the slide gets its own AI-generated image (scene-numbered prompt)
    // instead of repeating one another deck's picture.
    let imgLine;
    const matched = matchImageByName(slide, images);
    if (matched) {
      imgLine = `![](images/${matched})`;
      usedLocal.add(matched);
    } else {
      const fresh = images.find((img) => !usedLocal.has(img));
      if (fresh) {
        imgLine = `![](images/${fresh})`;
        usedLocal.add(fresh);
      } else {
        imgLine = `![gen: ${buildGenPrompt(slide, slideIdx + 1)} | flux | 1280x720]`;
      }
    }

    // Fits comfortably beside the text at half-width - proper side-by-
    // side layout, alternating left/right for visual rhythm. Any
    // existing font-size class (e.g. "compact") is preserved alongside
    // the new layout class instead of being discarded.
    if (blocks <= 1 && weight <= SIDE_BY_SIDE_MAX_WEIGHT) {
      const side = sideToggle % 2 === 0 ? 'image-right' : 'image-left';
      sideToggle++;
      insertedCount++;
      const mergedClasses = [...existingClasses, side];
      const body = stripClassComment(slide).trim();
      return `<!-- _class: ${mergedClasses.join(' ')} -->\n\n${body}\n\n${imgLine}`;
    }

    // Too dense for side-by-side, and not a table (a table plus an image
    // below it gets cramped fast) - append a full-width image under the
    // existing content instead of leaving the slide with no image at all.
    if (!hasTable && weight <= BOTTOM_IMAGE_MAX_WEIGHT) {
      insertedCount++;
      return `${slide.trim()}\n\n${imgLine}`;
    }

    return slide;
  });

  return { body: outParts.join('\n\n---\n\n'), insertedCount };
}

const imagesDir = imagesDirArg || null;
const availableImages = getAvailableImages(imagesDir);
let imageSummary = '';

// always run image insertion, even with zero existing photos - any
// plain slide left without one gets an AI-generated ![gen: ...] tag
// instead, which genimage.js resolves into a real picture afterward.
const result = insertImagesIntoSlides(finalBody, availableImages);
finalBody = result.body;
if (result.insertedCount > 0) {
  imageSummary = availableImages.length > 0
    ? `, inserted ${result.insertedCount} image(s)`
    : `, added ${result.insertedCount} AI image prompt(s)`;
}

if (hasBreaks && imageSummary === '') {
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, raw, 'utf8');
  console.log(`[paginate] ${paginateSummary}.`);
  process.exit(0);
}

const fm = frontmatter ? frontmatter.trim() : '---\ntheme: black-green\n---';
const output = `${fm}\n\n${finalBody.trim()}\n`;

fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, output, 'utf8');
console.log(`[paginate] ${paginateSummary}${imageSummary}.`);
