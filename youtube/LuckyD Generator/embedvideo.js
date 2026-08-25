#!/usr/bin/env node
// embedvideo.js
// ---------------------------------------------------------------
// Makes a processed COPY of a markdown file, converting video links
// into real playable embeds. Meant to be run ONLY on the copy that
// feeds the HTML export (video won't play in PPTX/PDF/PNG, so those
// should keep using plain links instead of this output).
//
// Recognizes:
//   - YouTube links (youtube.com/watch?v=, youtu.be/, /shorts/, /embed/)
//   - Vimeo links (vimeo.com/<id>)
//   - Direct video file links (.mp4 .webm .ogg .mov .m4v), whether
//     remote (https://...) or local (e.g. videos/demo.mp4)
//
// Behavior:
//   - If a whole line (bullet/standalone) is just a video link (with
//     or without markdown [text](url) syntax), the line is REPLACED
//     with the embed.
//   - If a video link appears inline inside other text, the
//     surrounding text is left as-is (link stays a normal clickable
//     link) and the embed is inserted as its own block right after.
//   - Non-video links are never touched.
//   - Injects a small <style> block once (video responsive sizing).
//   - Requires the output to be rendered with Marp's --html flag,
//     since these are raw HTML tags.
//
// Usage: node embedvideo.js <input.md> <output.md>
// Never overwrites the input. Prints a one-line summary to stdout.
// ---------------------------------------------------------------

const fs = require('fs');

const [, , inPath, outPath] = process.argv;

if (!inPath || !outPath) {
  console.log('[video] usage: node embedvideo.js <input.md> <output.md>');
  process.exit(0);
}
if (!fs.existsSync(inPath)) {
  console.log(`[video] input not found: ${inPath}`);
  process.exit(0);
}

const raw = fs.readFileSync(inPath, 'utf8');

// ---- split off frontmatter, if present ----
let frontmatter = '';
let body = raw;
const fmMatch = raw.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
if (fmMatch) {
  frontmatter = fmMatch[0];
  body = raw.slice(fmMatch[0].length);
}

const VIDEO_EXT_RE = /\.(mp4|webm|ogg|mov|m4v)(\?[^\s)]*)?$/i;

function detectVideo(url) {
  const yt = url.match(/(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)([a-zA-Z0-9_-]{6,})/i);
  if (yt) return { type: 'youtube', id: yt[1] };
  const vim = url.match(/vimeo\.com\/(\d+)/i);
  if (vim) return { type: 'vimeo', id: vim[1] };
  if (VIDEO_EXT_RE.test(url)) return { type: 'file', url };
  return null;
}

function embedHtml(v) {
  if (v.type === 'youtube') {
    return `<div class="video-embed"><iframe src="https://www.youtube.com/embed/${v.id}" allowfullscreen></iframe></div>`;
  }
  if (v.type === 'vimeo') {
    return `<div class="video-embed"><iframe src="https://player.vimeo.com/video/${v.id}" allowfullscreen></iframe></div>`;
  }
  return `<video class="video-embed-file" controls src="${v.url}"></video>`;
}

// matches [text](url) links
const MD_LINK_RE = /\[([^\]]*)\]\((https?:\/\/[^\s)]+|[^\s()]+\.(?:mp4|webm|ogg|mov|m4v)(?:\?[^\s)]*)?)\)/gi;
// matches bare URLs (not already inside markdown link parens - handled separately below)
const BARE_URL_RE = /(https?:\/\/[^\s)]+)/gi;

let videoCount = 0;

// process line by line, so a single bulleted/standalone video link is
// swapped out cleanly in place, and video links embedded inside other
// text get an embed inserted right after that line instead.
const lines = body.split(/\r?\n/);
const outLines = [];

for (const line of lines) {
  const found = [];
  let strippedForCheck = line;

  let m;
  MD_LINK_RE.lastIndex = 0;
  while ((m = MD_LINK_RE.exec(line)) !== null) {
    const v = detectVideo(m[2]);
    if (v) {
      found.push({ full: m[0], video: v });
      strippedForCheck = strippedForCheck.replace(m[0], '');
    }
  }
  BARE_URL_RE.lastIndex = 0;
  while ((m = BARE_URL_RE.exec(strippedForCheck)) !== null) {
    const v = detectVideo(m[1]);
    if (v && !found.some(f => f.full.includes(m[1]))) {
      found.push({ full: m[1], video: v });
    }
  }

  if (found.length === 0) {
    outLines.push(line);
    continue;
  }

  videoCount += found.length;

  // is this line, once all video links are stripped, just a bullet/
  // quote marker and whitespace (i.e. the line IS the link)?
  let remainder = line;
  for (const f of found) remainder = remainder.split(f.full).join('');
  const isLineJustLinks = remainder.replace(/^[\s>*\-\d.]+|[\s>*\-\d.]+$/g, '').trim() === '';

  const embeds = found.map(f => embedHtml(f.video)).join('\n\n');

  if (isLineJustLinks) {
    outLines.push('');
    outLines.push(embeds);
    outLines.push('');
  } else {
    outLines.push(line);
    outLines.push('');
    outLines.push(embeds);
    outLines.push('');
  }
}

let finalBody = outLines.join('\n').replace(/\n{3,}/g, '\n\n');

if (videoCount > 0) {
  const style = `<style>\n.video-embed { position: relative; padding-bottom: 56.25%; height: 0; overflow: hidden; margin: 0.5em 0; }\n.video-embed iframe { position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: 0; }\n.video-embed-file { max-width: 100%; max-height: 65%; display: block; margin: 0.5em auto; }\n</style>\n\n`;
  finalBody = style + finalBody;
}

// This copy is only ever used to build the HTML export (pptx/pdf/png use
// the plain, un-videoized copy), so it's a safe, single place to make
// sure every external link opens in a NEW TAB instead of navigating the
// presentation window away to that page - which could look like the
// link "doesn't open" if the tab just goes blank/away from the deck.
const linkScript = `<script>\ndocument.addEventListener('DOMContentLoaded', () => {\n  document.querySelectorAll('a[href^="http"]').forEach((a) => {\n    a.target = '_blank';\n    a.rel = 'noopener noreferrer';\n  });\n});\n</script>\n\n`;
finalBody = linkScript + finalBody;

const fm = frontmatter ? frontmatter.trim() + '\n\n' : '';
const output = `${fm}${finalBody.trim()}\n`;

fs.writeFileSync(outPath, output, 'utf8');
console.log(`[video] embedded ${videoCount} video(s), added target=_blank to links`);
