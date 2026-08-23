// genimage.js
// ---------------------------------------------------------------
// Free AI image generation for decks, via Pollinations.ai.
// No API key needed — same free backends Nexus uses (flux, turbo,
// gptimage, nanobanana, seedream).
//
// Usage:
//     node genimage.js <input.md> <output.md> <imagesDir>
//
// In your deck markdown, write placeholders like:
//     ![gen: giant glowing brain connectome, neon green, dark background]
//
// Optional extras after the prompt, separated by "|":
//     ![gen: retro robot mascot | seedream | 1024x1024]
//
// The placeholder is replaced with a real downloaded image. Files are
// cached by prompt hash, so re-running never regenerates the same image.
// Your original .md file is never modified.
//
// Tuning via env vars (all optional):
//     GENIMAGE_CONCURRENCY=2      how many requests in flight at once
//     GENIMAGE_MAX_ATTEMPTS=5     retries per image before giving up
// ---------------------------------------------------------------

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const POLL_BASE = "https://image.pollinations.ai";
const MODELS = ["flux", "turbo", "gptimage", "nanobanana", "seedream"];
const DEFAULT_MODEL = "flux";
const DEFAULT_SIZE = { width: 1280, height: 720 };

// How many images to generate at once instead of one-by-one. Pollinations
// is a free/shared service and was observed hitting HTTP 429 with 3
// in-flight requests, so the default is now 2. Override with
// GENIMAGE_CONCURRENCY if you want it faster and aren't seeing failures,
// or drop it to 1 if you still see FAILED lines from rate limiting.
const CONCURRENCY = Number(process.env.GENIMAGE_CONCURRENCY) || 2;

// Retries per image before giving up on the first pass. Failures are
// common on a free/shared backend (timeouts, momentary 429/5xx) and don't
// mean the prompt is bad.
const MAX_ATTEMPTS = Number(process.env.GENIMAGE_MAX_ATTEMPTS) || 5;

// Backoff tuning. 429s get a longer base wait than other errors, since
// rate-limit windows are usually per-minute, not per-second.
const RETRY_BASE_MS = 4000;
const RETRY_BASE_MS_429 = 9000;
const RETRY_MAX_MS = 30000;

// Small delay between launching each concurrent worker slot, so a batch
// of N images doesn't all hit the API in the same instant (a "thundering
// herd" is a common cause of 429s even when steady-state concurrency
// would have been fine).
const STAGGER_MS = 500;

// If any images are still failed after the concurrent pass, wait this
// long to let any rate-limit window clear, then retry them one at a time.
const COOLDOWN_BEFORE_FINAL_RETRY_MS = 10000;

function parseArgs() {
    const [input, output, imagesDir] = process.argv.slice(2);
    if (!input || !output || !imagesDir) {
        console.error("Usage: node genimage.js <input.md> <output.md> <imagesDir>");
        process.exit(1);
    }
    return { input, output, imagesDir };
}

function parseTag(raw) {
    const parts = raw.split("|").map((p) => p.trim());
    const prompt = parts.shift();
    let model = DEFAULT_MODEL;
    let size = { ...DEFAULT_SIZE };
    for (const part of parts) {
        const dim = part.match(/^(\d+)x(\d+)$/);
        if (dim) {
            size = { width: Number(dim[1]), height: Number(dim[2]) };
        } else if (MODELS.includes(part.toLowerCase())) {
            model = part.toLowerCase();
        }
    }
    if (!prompt) return null;
    return { prompt, model, size };
}

function sleep(ms) {
    return new Promise((res) => setTimeout(res, ms));
}

async function fetchImage(url, timeoutMs = 90000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const res = await fetch(url, { signal: controller.signal });
        if (!res.ok) {
            const err = new Error(`HTTP ${res.status}`);
            err.status = res.status;
            const retryAfter = res.headers.get("retry-after");
            if (retryAfter && !Number.isNaN(Number(retryAfter))) {
                err.retryAfterMs = Number(retryAfter) * 1000;
            }
            throw err;
        }
        const buf = Buffer.from(await res.arrayBuffer());
        if (buf.length < 1000) throw new Error("suspiciously small response");
        return buf;
    } catch (err) {
        if (err.name === "AbortError") {
            throw new Error(`timed out after ${Math.round(timeoutMs / 1000)}s`);
        }
        throw err;
    } finally {
        clearTimeout(timer);
    }
}

// Exponential backoff with jitter. 429s (rate limit) get a longer base
// wait than transient network/5xx errors. If the server sends a
// Retry-After header, that wins over everything else.
function backoffMs(attempt, err) {
    if (err && err.retryAfterMs) return err.retryAfterMs;
    const isRateLimit = err && err.status === 429;
    const base = isRateLimit ? RETRY_BASE_MS_429 : RETRY_BASE_MS;
    const exp = base * Math.pow(2, attempt - 1);
    const jitter = Math.random() * 1000;
    return Math.min(exp + jitter, RETRY_MAX_MS);
}

// Pollinations is a free, shared, best-effort service - a single failed
// request (timeout, momentary 429/5xx, a hiccup) is common and does NOT
// mean the prompt is bad, so give it several tries with growing pauses
// between them before actually giving up on that image.
async function fetchImageWithRetry(url, onRetry, maxAttempts = MAX_ATTEMPTS) {
    let lastErr;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        try {
            return await fetchImage(url);
        } catch (err) {
            lastErr = err;
            if (attempt < maxAttempts) {
                const waitMs = backoffMs(attempt, err);
                onRetry?.(attempt, maxAttempts, err, waitMs);
                await sleep(waitMs);
            }
        }
    }
    throw lastErr;
}

// Runs `worker` over `items` with at most `limit` in flight at once.
// Each worker slot's first request is staggered slightly so a batch of
// N images doesn't all hit the API in the same instant. Results come
// back in the SAME ORDER as `items`, regardless of which finished first,
// so downstream code doesn't have to worry about interleaving.
async function withConcurrency(items, limit, worker, staggerMs = STAGGER_MS) {
    const results = new Array(items.length);
    let nextIndex = 0;
    async function runNext(slot) {
        await sleep(slot * staggerMs);
        while (nextIndex < items.length) {
            const i = nextIndex++;
            results[i] = await worker(items[i], i);
        }
    }
    const pool = Array.from({ length: Math.min(limit, items.length) }, (_, slot) => runNext(slot));
    await Promise.all(pool);
    return results;
}

function cacheInfoFor(spec, imagesDir) {
    const key = crypto
        .createHash("sha1")
        .update(`${spec.prompt}|${spec.model}|${spec.size.width}x${spec.size.height}`)
        .digest("hex")
        .slice(0, 12);
    const fileName = `gen_${key}.png`;
    return { fileName, filePath: path.join(imagesDir, fileName) };
}

// Cache filenames claimed during THIS run. Two slides must never share an
// image, so if two tags hash to the same file (identical prompts), later
// ones get a variant suffix that changes both the cache key AND the
// generated picture (the salt flows into the Pollinations URL too).
const claimedFiles = new Set();
let variantCounter = 0;

function uniqueCacheInfoFor(spec, imagesDir) {
    let info = cacheInfoFor(spec, imagesDir);
    while (claimedFiles.has(info.fileName)) {
        spec = {
            ...spec,
            prompt: `${spec.prompt}, variation ${++variantCounter}`,
        };
        info = cacheInfoFor(spec, imagesDir);
    }
    claimedFiles.add(info.fileName);
    return { spec, ...info };
}

// Generates a guaranteed-unique image for one [gen: ...] tag match.
// maxAttempts is overridable so the final cooldown retry pass can use a
// smaller attempt count (it's already had its full share of retries).
async function generateOne(match, imagesDir, maxAttempts = MAX_ATTEMPTS) {
    const parsed = parseTag(match[1]);
    if (!parsed) return { match, ok: false, skip: true };

    const { spec, fileName, filePath } = uniqueCacheInfoFor(parsed, imagesDir);

    try {
        if (!fs.existsSync(filePath)) {
            const encoded = encodeURIComponent(spec.prompt);
            const url =
                `${POLL_BASE}/prompt/${encoded}` +
                `?width=${spec.size.width}&height=${spec.size.height}` +
                `&nologo=true&model=${spec.model}&seed=${Math.floor(Math.random() * 1e6)}`;
            console.log(`    [${spec.model}] starting: ${spec.prompt.slice(0, 60)}...`);
            const buf = await fetchImageWithRetry(
                url,
                (attempt, max, err, waitMs) => {
                    console.log(`    [${spec.model}] attempt ${attempt}/${max} failed (${err.message}), retrying in ${Math.round(waitMs / 1000)}s: ${spec.prompt.slice(0, 60)}...`);
                },
                maxAttempts
            );
            fs.writeFileSync(filePath, buf);
            console.log(`    [${spec.model}] done: ${spec.prompt.slice(0, 60)}...`);
        } else {
            console.log(`    [cached] ${fileName}`);
        }
        return { match, fileName, ok: true };
    } catch (err) {
        console.log(`    [${spec.model}] FAILED (${err.message}): ${spec.prompt.slice(0, 60)}...`);
        return { match, spec, ok: false };
    }
}

async function main() {
    const { input, output, imagesDir } = parseArgs();
    if (!fs.existsSync(input)) {
        console.error(`[!] Input not found: ${input}`);
        process.exit(1);
    }
    fs.mkdirSync(imagesDir, { recursive: true });

    const src = fs.readFileSync(input, "utf8");
    const tagRe = /!\[gen:\s*([^\]]+)\]/g;

    const found = [...src.matchAll(tagRe)];
    if (found.length === 0) {
        console.log("No [gen:] image tags found — nothing to do.");
        return;
    }

    console.log(`==> Found ${found.length} image tag(s), generating (up to ${CONCURRENCY} at a time)...`);

    let results = await withConcurrency(found, CONCURRENCY, (match) => generateOne(match, imagesDir));

    // Second chance: anything still failed after the concurrent pass gets
    // retried one at a time, after a cooldown, instead of being given up
    // on immediately. This is what actually fixes the "2 out of 6 failed"
    // scenario — those 2 get a clean shot with zero concurrent competition.
    const failedIdx = results
        .map((r, i) => (r && !r.ok && !r.skip ? i : -1))
        .filter((i) => i !== -1);

    if (failedIdx.length > 0) {
        console.log(`==> ${failedIdx.length} image(s) still failed after the first pass — cooling down ${Math.round(COOLDOWN_BEFORE_FINAL_RETRY_MS / 1000)}s, then retrying one at a time...`);
        await sleep(COOLDOWN_BEFORE_FINAL_RETRY_MS);
        for (const i of failedIdx) {
            results[i] = await generateOne(found[i], imagesDir, 3);
        }
    }

    // Rebuild the output POSITIONALLY from the match offsets. String.replace
    // only swaps the first occurrence, so two identical tags used to collapse
    // onto the first slide's image — duplicates are exactly what we never
    // want. Positional splicing keeps every tag independent.
    let out = "";
    let last = 0;
    let ok = 0;
    const stillFailed = [];
    results.forEach((r, i) => {
        const m = found[i];
        out += src.slice(last, m.index);
        last = m.index + m[0].length;
        if (!r || r.skip) {
            out += m[0];
            return;
        }
        if (r.ok) {
            out += `![](images/${r.fileName})`;
            ok++;
        } else {
            out += `> ⚠️ image generation failed: ${r.spec.prompt}`;
            stillFailed.push(r.spec.prompt);
        }
    });
    out += src.slice(last);

    fs.writeFileSync(output, out, "utf8");
    console.log(`==> Done: ${ok}/${found.length} images ready in ${imagesDir}`);
    if (stillFailed.length > 0) {
        console.log(`==> ${stillFailed.length} image(s) never succeeded — re-run this command to retry just those (already-generated images are cached and will be skipped):`);
        for (const p of stillFailed) {
            console.log(`      - ${p.slice(0, 80)}${p.length > 80 ? "..." : ""}`);
        }
    }
}

main().catch((err) => {
    console.error(`[!] genimage crashed: ${err.message}`);
    process.exit(1);
});
