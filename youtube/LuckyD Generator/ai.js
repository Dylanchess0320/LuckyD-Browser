// ai.js
// ---------------------------------------------------------------
// Free LLM access + deck generator for LuckyD videos.
// Gemini primary, Zen fallback, OpenRouter :free last resort.
// Reads .env next to this file. No dependencies.
//
// CLI:
//     node ai.js "Write a 3-line hook about black holes"
//     node ai.js deck --topic "Why my AI bill is $0" --slides 8
//     node ai.js deck --file raw-notes.md --style hype
//
// As a module from other node scripts:
//     const { ask, makeDeck } = require("./ai.js");
// ---------------------------------------------------------------

const fs = require("fs");
const path = require("path");

function loadEnv() {
    const env = {};
    const envPath = path.join(__dirname, ".env");
    if (fs.existsSync(envPath)) {
        for (const line of fs.readFileSync(envPath, "utf8").split(/\r?\n/)) {
            const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
            if (m && !line.trim().startsWith("#")) env[m[1]] = m[2].trim();
        }
    }
    return env;
}

async function askGemini(prompt, { model } = {}) {
    const env = loadEnv();
    const key = env.GOOGLE_API_KEY;
    if (!key) throw new Error("GOOGLE_API_KEY not set in .env");
    const chosen = model || env.GOOGLE_MODEL || "gemini-3-flash-preview";
    const res = await fetch(
        `https://generativelanguage.googleapis.com/v1beta/models/${chosen}:generateContent?key=${key}`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                contents: [{ parts: [{ text: prompt }] }],
                generationConfig: { temperature: 0.9, maxOutputTokens: 32768 },
            }),
        }
    );
    if (!res.ok) throw new Error(`Gemini HTTP ${res.status}`);
    const data = await res.json();
    const text = data.candidates?.[0]?.content?.parts?.map((p) => p.text || "").join("");
    if (!text) throw new Error("Gemini returned no text");
    return text;
}

async function askOpenRouter(prompt, { model } = {}) {
    const env = loadEnv();
    const key = env.OPENROUTER_API_KEY;
    if (!key) throw new Error("OPENROUTER_API_KEY not set in .env");
    const chosen = model || env.OPENROUTER_FREE_MODEL || "nvidia/nemotron-3-ultra-550b-a55b:free";
    const res = await fetch("https://openrouter.ai/api/v1/chat/completions", {
        method: "POST",
        headers: {
            Authorization: `Bearer ${key}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            model: chosen,
            messages: [
                { role: "system", content: "You are a sharp, funny video scriptwriter." },
                { role: "user", content: prompt },
            ],
            max_tokens: 16384,
        }),
    });
    if (!res.ok) throw new Error(`OpenRouter HTTP ${res.status}`);
    const data = await res.json();
    const text = data.choices?.[0]?.message?.content;
    if (!text) throw new Error("OpenRouter returned no content");
    return text;
}

async function askZen(prompt, { model } = {}) {
    const env = loadEnv();
    const key = env.ZEN_API_KEY;
    if (!key) throw new Error("ZEN_API_KEY not set in .env");
    const base = (env.ZEN_BASE_URL || "https://opencode.ai/zen/v1").replace(/\/$/, "");
    const chosen = model || env.ZEN_MODEL || "nemotron-3-ultra-free";
    const res = await fetch(`${base}/chat/completions`, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${key}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            model: chosen,
            messages: [
                { role: "system", content: "You are a sharp, funny video scriptwriter." },
                { role: "user", content: prompt },
            ],
            max_tokens: 16384,
        }),
    });
    if (!res.ok) throw new Error(`Zen HTTP ${res.status}`);
    const data = await res.json();
    const text = data.choices?.[0]?.message?.content;
    if (!text) throw new Error("Zen returned no content");
    return text;
}

// Cascade: primary provider first, fallback on failure.
async function ask(prompt, opts = {}) {
    try {
        return await askGemini(prompt, opts);
    } catch (err) {
        console.error(`[ai] Gemini failed (${err.message}) — falling back to Zen...`);
        return await askZen(prompt, opts);
    }
}

async function askWithFullFallback(prompt, opts = {}) {
    for (const [name, fn] of [
        ["gemini", askGemini],
        ["zen", askZen],
        ["openrouter", askOpenRouter],
    ]) {
        try {
            return await fn(prompt, opts);
        } catch (err) {
            console.error(`[ai] ${name} failed (${err.message}) — trying next...`);
        }
    }
    throw new Error("All providers failed");
}

// ── Deck generation ────────────────────────────────────────────────

const STYLES = {
    fun: "Tone: playful best-friend energy. Jokes allowed and encouraged — at least one genuine laugh per section. Punchy, warm, zero pretension.",
    hype: "Tone: high-energy YouTuber. Short explosive lines, dramatic pauses written as '...', stakes feel enormous. MrBeast thumbnail energy.",
    pro: "Tone: confident explainer. Clever but restrained wit. Feels like a smart friend whiteboarding, not a lecture.",
    story: "Tone: narrative storyteller. Each section advances a story with tension and payoff. Cliffhanger transitions between sections.",
};

const BANNED =
    "Never use these words/phrases: delve, leverage, unlock, unleash, game-changer, revolutionary, revolutionize, in today's world, in today's fast-paced, landscape, realm, tapestry, testament, moreover, furthermore, it's important to note.";

const DECK_RULES = `
You write Marp slide decks for YouTube videos by LuckyD — a teenage dev who builds
his own AI tools for free and turns everything into content.

FORMAT RULES (mandatory):
- Output ONLY the raw .md deck. No code fences, no commentary before or after.
- Start with exactly this front matter:
---
marp: true
theme: black-green
paginate: true
---
- Separate slides with a line containing only ---.

SLIDE STRUCTURE — the black-green theme has dedicated layouts for each of
these moments. Use the matching HTML comment class marker right after the
--- on every slide listed below (plain content slides get no marker):

1. TITLE slide:
   <!-- _class: lead -->
   # Big bold title
   ## A subtitle that teases the payoff

2. HOOK slide — the single most surprising fact/stake, ONE punchy line, no bullets:
   <!-- _class: big -->
   # The hook line itself, stated as a title

3. CONTENT slides (3-8 of them, plain, no class marker):
   ## Section heading
   - **Bold key word** in a short bullet, 12 words max
   - 2-4 bullets total, one idea per slide

4. Optional MID-DECK PUNCH slide (use once if the material has a killer
   quote, callback, or standout one-liner — skip if nothing fits naturally):
   <!-- _class: quote -->
   > The line, as a markdown blockquote
   OR, for a single striking number/stat instead of a quote:
   <!-- _class: stat -->
   # 47%
   ### three or four words of context

5. CALLBACK slide (plain, no class marker) — ties back to the hook, pays it off.

6. CTA slide, always last:
   <!-- _class: cta -->
   # Like & Subscribe
   ## @LuckyDYoutube — youtube.com/@LuckyDYoutube

OTHER MANDATORY RULES:
- Bullets: 12 words max each, 2-4 bullets. Bold the key word in a bullet.
- Every CONTENT and CALLBACK slide gets speaker notes at its end: <!-- notes: what Lucky says out loud here, conversational, with delivery cues -->.
- IMAGE SLIDES — roughly every 3rd slide, turn that CONTENT slide into a
  side-by-side image slide instead of a plain one. Put the image class marker
  right after the --- (ALTERNATE image-right and image-left each time you use
  one, for visual rhythm), keep the text SHORT because it only gets half the
  slide's width now (heading + 1-2 short bullets, or a single short line — never
  the full 2-4 bullet budget), then the image directive as the LAST line of the
  slide:
   <!-- _class: image-right -->
   ## Section heading
   - **Bold key word** in one short bullet, 8 words max

   ![gen: a hilarious, vivid concrete visual scene matching this slide | flux | 1280x720]
  Make the scene genuinely FUNNY, not just illustrative - an absurd sight gag, a
  ridiculous exaggeration, a deadpan literal take on the idea pushed too far, or an
  unexpected comedic juxtaposition. The scene itself should be the joke - never just
  "a person laughing" or "a funny face," that's not a joke, that's a description of one.
  Keep it specific and concrete (real objects, a real setting, a real punchline), never
  abstract or vague. Never put an image directive on a lead/big/quote/stat/cta slide, and
  never combine an image with the full 2-4 bullet budget — that overflows the slide.

WRITING RULES:
- One idea per slide. If two ideas fit on a slide, split them.
- Specific beats abstract: numbers, names, mini-examples. No vague filler.
- Vary sentence rhythm. Short. Then a longer one that lands the point.
${BANNED}
`;

function buildDeckPrompt({ topic, sourceText, slides, style }) {
    const styleLine = STYLES[style] || STYLES.fun;
    const countLine = slides
        ? `Target about ${slides} slides total (including title/hook/callback/CTA).`
        : "Use as many slides as the content genuinely needs — but never pad.";
    if (sourceText) {
        return `${DECK_RULES}\nSTYLE: ${styleLine}\n${countLine}\n\nBelow is RAW SOURCE MATERIAL (messy notes/transcript/chat log).\nTurn it into a deck: keep every genuinely interesting fact, cut all clutter,\nand make it FUN to read. Reorder for drama. Invent nothing factual.\n\n--- SOURCE MATERIAL START ---\n${sourceText}\n--- SOURCE MATERIAL END ---`;
    }
    return `${DECK_RULES}\nSTYLE: ${styleLine}\n${countLine}\n\nTOPIC: ${topic}`;
}

function cleanDeck(text) {
    let t = String(text).trim();
    // Strip a single wrapping markdown fence if the model added one
    const fence = t.match(/^```(?:markdown|md)?\s*\n([\s\S]*?)\n```$/i);
    if (fence) t = fence[1].trim();
    // Drop any preamble before front matter
    const fmStart = t.indexOf("---\n");
    if (fmStart === -1 || fmStart > 0) {
        const firstFm = t.match(/^---\s*\n[\s\S]*?\n---\s*\n/m);
        if (firstFm && firstFm.index > 0) t = t.slice(firstFm.index);
        else if (!/^---/.test(t)) {
            t = "---\nmarp: true\ntheme: black-green\npaginate: true\n---\n\n" + t;
        }
    }
    return t.replace(/\r\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim() + "\n";
}

function extractTitle(deck) {
    const m = deck.match(/^#\s+(.+)$/m);
    return m ? m[1].trim() : "untitled-deck";
}

function slugify(title) {
    return (
        title
            .toLowerCase()
            .replace(/[^a-z0-9\s-]/g, "")
            .trim()
            .replace(/\s+/g, "-")
            .slice(0, 60) || "deck"
    );
}

async function makeDeck(opts = {}) {
    const { topic, file, slides, style = "fun", out } = opts;
    let sourceText = null;
    if (file) {
        sourceText = fs.readFileSync(file, "utf8");
        if (sourceText.length > 90000) sourceText = sourceText.slice(0, 90000) + "\n...[truncated]";
    }
    if (!topic && !sourceText) throw new Error("makeDeck needs --topic or --file");

    process.stderr.write("[ai] writing deck...\n");
    const raw = await askWithFullFallback(buildDeckPrompt({ topic, sourceText, slides, style }));
    const deck = cleanDeck(raw);

    const outPath =
        out ||
        path.join(path.dirname(file || __dirname), "decks", `${slugify(extractTitle(deck))}.md`);
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, deck, "utf8");
    process.stderr.write(`[ai] saved -> ${outPath}\n`);
    console.log(outPath);
    return outPath;
}

module.exports = { ask, askGemini, askZen, askOpenRouter, askWithFullFallback, makeDeck };

if (require.main === module) {
    const argv = process.argv.slice(2);
    const mode = argv[0] === "deck" ? "deck" : "prompt";
    const args = argv.slice(mode === "deck" ? 1 : 0);

    if (mode === "prompt") {
        let provider = null;
        const parts = [];
        for (let i = 0; i < args.length; i++) {
            if (args[i] === "--provider") provider = args[++i];
            else parts.push(args[i]);
        }
        const prompt = parts.join(" ").trim();
        if (!prompt) {
            console.error('Usage: node ai.js "your prompt" [--provider gemini|zen|openrouter]');
            console.error("       node ai.js deck --topic \"...\" | --file notes.md [--slides N] [--style fun|hype|pro|story]");
            process.exit(1);
        }
        const fn =
            provider === "openrouter" ? askOpenRouter :
            provider === "zen" ? askZen :
            provider === "gemini" ? askGemini : ask;
        fn(prompt)
            .then((text) => console.log(text))
            .catch((err) => {
                console.error(`[!] ${err.message}`);
                process.exit(1);
            });
    } else {
        const opts = {};
        const rest = [];
        for (let i = 0; i < args.length; i++) {
            const a = args[i];
            if (a === "--topic") opts.topic = args[++i];
            else if (a === "--file") opts.file = args[++i];
            else if (a === "--slides") opts.slides = parseInt(args[++i], 10) || undefined;
            else if (a === "--style") opts.style = args[++i];
            else if (a === "--out") opts.out = args[++i];
            else rest.push(a);
        }
        if (!opts.topic && rest.length) opts.topic = rest.join(" ");
        makeDeck(opts).catch((err) => {
            console.error(`[!] ${err.message}`);
            process.exit(1);
        });
    }
}
