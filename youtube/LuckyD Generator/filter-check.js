#!/usr/bin/env node
// filter-check.js
// ---------------------------------------------------------------
// Makes a filtered COPY of a Marp deck, starring out any words or
// phrases listed in decks\banned-words.txt (one per line, # for
// comments). That filtered copy is what actually gets converted -
// your original .md file is never changed.
//
// Usage: node filter-check.js <deck.md> <banned-words.txt> <output.md>
// ---------------------------------------------------------------

const fs = require('fs');
const path = require('path');

const [, , deckPath, wordsPath, outPath] = process.argv;

if (!deckPath || !outPath) {
  console.log('[filter] usage: node filter-check.js <deck.md> <banned-words.txt> <output.md>');
  process.exit(0);
}

if (!fs.existsSync(deckPath)) {
  console.log(`[filter] deck not found: ${deckPath}`);
  process.exit(0);
}

const text = fs.readFileSync(deckPath, 'utf8');

let bannedWords = [];
if (wordsPath && fs.existsSync(wordsPath)) {
  bannedWords = fs.readFileSync(wordsPath, 'utf8')
    .split(/\r?\n/)
    .map(w => w.trim())
    .filter(w => w.length > 0 && !w.startsWith('#'));
}

// Nothing to filter - pass the deck through unchanged.
if (bannedWords.length === 0) {
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, text, 'utf8');
  console.log('[filter] no words listed in banned-words.txt - deck passed through unchanged.');
  process.exit(0);
}

// Longest phrases first so a multi-word entry isn't shadowed by a
// shorter one, then escape regex special characters and let any
// whitespace in a phrase match flexibly.
const escaped = bannedWords
  .sort((a, b) => b.length - a.length)
  .map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+'));

const pattern = new RegExp('\\b(' + escaped.join('|') + ')\\b', 'gi');

const lines = text.split(/\r?\n/);
let hits = 0;
for (let i = 0; i < lines.length; i++) {
  lines[i] = lines[i].replace(pattern, (match) => {
    hits++;
    console.log(`[filter] line ${i + 1}: replaced "${match}"`);
    // keep first character, star out the rest - e.g. "word" -> "w***"
    return match[0] + '*'.repeat(Math.max(match.length - 1, 1));
  });
}

fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, lines.join('\n'), 'utf8');

if (hits > 0) {
  console.log(`[filter] ${hits} word(s) starred out of the exported deck. Your original .md was NOT changed.`);
} else {
  console.log('[filter] no banned words found - all clear.');
}
