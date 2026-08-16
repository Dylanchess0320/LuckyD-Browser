// Auto mode — two synthetic "models" the picker can select:
//   auto/free  → always resolves to a $0 model, picked for the task at hand
//   auto/paid  → resolves to the strongest paid model for the task
// Real resolution happens per-message in App.jsx, using whatever's actually
// in the current catalog (so it stays correct as OpenRouter's list changes).

export const AUTO_FREE_ID = 'auto/free'
export const AUTO_PAID_ID = 'auto/paid'

export const isAutoModel = (id) => id === AUTO_FREE_ID || id === AUTO_PAID_ID

// ---- task detection from the raw message text ----
const IMAGE_RE =
  /\b(draw|sketch|paint|illustrate)\b|\b(generate|create|make|design)\b[^.?!]{0,25}\b(image|picture|photo|pic|art|illustration|drawing|logo|icon|wallpaper|avatar|poster|meme|graphic)\b|\b(image|picture|photo|pic)\s+of\b/i
const CODE_RE =
  /```|\b(function|debug|refactor|stack ?trace|traceback|compile|syntax error|regex|algorithm|write (a |some )?(script|code|function|class)|fix (this|my) (code|bug)|css|html|sql query|api endpoint)\b/i
const REASONING_RE =
  /\b(prove|proof|solve|step by step|derive|calculate|equation|theorem|logic puzzle|riddle|reasoning)\b/i

export function detectTaskType(text) {
  const t = (text || '').trim()
  if (!t) return 'chat'
  if (IMAGE_RE.test(t)) return 'image'
  if (CODE_RE.test(t)) return 'code'
  if (REASONING_RE.test(t)) return 'reasoning'
  return 'chat'
}

// ---- does this message need live web data the model can't know on its own? ----
// Covers explicit news/current-events asks, freshness words tied to a topic,
// live data (prices/scores/weather), "who/when is the current X" lookups, and
// direct asks to search/browse. Kept broad-but-cheap (single regex pass) —
// false positives just mean web search turns on when it wasn't strictly
// needed, which is harmless; false negatives are the worse failure mode here.
const WEB_NEED_RE =
  /\b(news|headlines?|breaking)\b|\b(latest|recent|newest)\b[^.?!]{0,20}\b(version|release|update|model|news|price|score|episode|album|movie|paper|research)\b|\b(today|tonight|this (week|month|morning|weekend)|right now|these days|as of (today|now))\b|\b(stock|share) price\b|\bexchange rate\b|\bweather\b|\bforecast\b|\b(score|standings|schedule)\b.{0,15}\b(game|match|season|league|tournament)\b|\bwho (won|wins|is winning)\b|\belection results?\b|\brelease date\b|\bwhen (is|does|will|did)\b|\bis\s.{1,30}\sstill\b|\b(current|latest) (ceo|president|prime minister|price|status|version|champion)\b|\bwhat('| i)?s (happening|going on|new)\b|\b(search|look up|check|find|google)\b (the web|online|it up)/i

export function needsWebSearch(text) {
  const t = (text || '').trim()
  if (!t) return false
  return WEB_NEED_RE.test(t)
}

// ---- how much "thinking" does this message actually need? ----
// A flagship reasoning model answering "hi" is wasted latency and wasted
// tokens. Trivial small-talk gets routed toward fast/light models instead of
// whatever scores highest on general capability — speed matters more than
// intelligence headroom neither of you will use.
const TRIVIAL_RE =
  /^\s*(hi+|hey+|hello+|yo+|sup|howdy|hiya|good (morning|afternoon|evening|night)|thanks?( you| so much)?|thx|ty|ok(ay)?|k|cool|nice|great|awesome|lol|haha+|bye|goodbye|see ya|ttyl|yes|yep|yeah|nah|no|sure|got it|sounds good|how('s| is| are) it going\??|what'?s up\??|how are you\??)[.!?]*\s*$/i

export function estimateComplexity(text) {
  const t = (text || '').trim()
  if (!t) return 'trivial'
  if (TRIVIAL_RE.test(t)) return 'trivial'
  const words = t.split(/\s+/).length
  if (words <= 4 && !/[?]/.test(t)) return 'trivial'
  return 'normal'
}

// Naming patterns that reliably mean "small/fast variant" across labs —
// used to bias Auto toward speed on trivial messages and realtime voice
// turns instead of picking whatever model has the single highest general
// score in the pool.
//
// Split into two tiers because "lightweight" isn't one bucket: a plain
// `flash`/`turbo` model is still noticeably heavier than its own `-lite`/
// `mini`/`nano` sibling. Picking the highest-scoring match out of a pool that
// mixes both tiers means the "light" pick often isn't the fast one at all —
// e.g. a full `gemini-3.7-flash` can outscore `gemini-3.1-flash-lite` and
// win, even though the whole point of routing here was speed. Tier 0 is
// tried first; Tier 1 is the fallback if nothing in Tier 0 is available.
const SPEED_TIER_0_RE = /flash-lite|\bmini\b|\bnano\b|\bhaiku\b|\blite\b|\b([1-4])b\b/i
const SPEED_TIER_1_RE = /\bflash\b|\bsmall\b|\binstant\b|\bturbo\b|\b([5-9]|1[0-3])b\b/i

// Narrow a pool to the fastest tier that actually has candidates in it.
// Returns null (meaning "use the pool as-is") if neither tier matches
// anything, so callers can fall back to the full lightweight set or the
// unfiltered pool rather than returning nothing.
function speedPool(pool) {
  const tier0 = pool.filter((m) => SPEED_TIER_0_RE.test(`${m.id || ''} ${m.name || ''}`))
  if (tier0.length) return tier0
  const tier1 = pool.filter((m) => SPEED_TIER_1_RE.test(`${m.id || ''} ${m.name || ''}`))
  if (tier1.length) return tier1
  return null
}

// ---- curated benchmark table ----
// Snapshot taken 2026-08-15 from public leaderboards: Artificial Analysis
// Intelligence Index (general), SWE-bench Verified/Pro + Terminal-Bench
// (code), AIME 2026 / GPQA Diamond (reasoning), LMArena Elo. Scores are
// normalized to a 0-100 scale per category, matched against the model id+name
// by regex (first match wins, ordered most-specific first).
//
// Entries marked `estimated: true` have no directly published number for
// that exact variant — they're positioned relative to sibling models in the
// same family/tier rather than pulled from a benchmark run. Treat those as
// informed placement, not measured results.
//
// This is a manually-maintained snapshot, not a live feed — there's no free,
// no-auth API that serves per-model benchmark scores for arbitrary catalog
// entries, so this needs periodic manual refreshing as new models ship.
const FAMILY_BENCHMARKS = [
  { re: /claude[-\s]?opus[-\s]?5/i, general: 100, code: 96, reasoning: 95 }, // AA Intelligence Index 63 (#1 overall)
  { re: /claude[-\s]?(fable|mythos)[-\s]?5/i, general: 99, code: 93, reasoning: 97 }, // AA Index 62.1; GPQA Diamond 94.6%
  { re: /claude[-\s]?sonnet[-\s]?5/i, general: 88, code: 90, reasoning: 86, estimated: true },
  { re: /claude[-\s]?opus[-\s]?4\.[6-9]/i, general: 84, code: 90, reasoning: 84 }, // leads SWE-bench-adjacent tasks
  { re: /claude[-\s]?(sonnet|haiku)[-\s]?4/i, general: 72, code: 76, reasoning: 68, estimated: true },
  { re: /grok[-\s]?4\.6/i, general: 97, code: 85, reasoning: 90 }, // AA Index 60.9
  { re: /grok[-\s]?4(\.[0-9])?/i, general: 82, code: 78, reasoning: 83, estimated: true }, // 2M ctx, long-doc reasoning
  { re: /gpt-5\.[4-6]/i, general: 92, code: 82, reasoning: 96 }, // AIME 2026 100%, Arena Elo 1561
  { re: /gpt-5(\.[0-3])?\b/i, general: 87, code: 78, reasoning: 88, estimated: true },
  { re: /gpt-4o-mini|gpt-4\.1-mini/i, general: 55, code: 50, reasoning: 46, estimated: true },
  { re: /gpt-4o|gpt-4\.1\b/i, general: 68, code: 62, reasoning: 58, estimated: true },
  { re: /gemini[-\s]?3\.1[-\s]?pro/i, general: 90, code: 88, reasoning: 87 }, // AA Index 57; SWE-bench Verified 80.6%
  { re: /gemini[-\s]?3\.[5-7][-\s]?flash/i, general: 68, code: 57, reasoning: 56, estimated: true },
  { re: /gemini[-\s]?3(\.1)?[-\s]?flash|gemini[-\s]?2\.5[-\s]?flash/i, general: 62, code: 52, reasoning: 52, estimated: true },
  { re: /deepseek[-\s]?v4[-\s]?pro/i, general: 82, code: 90, reasoning: 79 }, // SWE-bench Verified 80.6%
  { re: /deepseek[-\s]?v4[-\s]?flash/i, general: 72, code: 79, reasoning: 66, estimated: true }, // same sparse-attn arch, smaller/faster
  { re: /deepseek[-\s]?v3(\.[0-9])?/i, general: 73, code: 71, reasoning: 73 }, // within ~10pts of GPT-5 on most reasoning tests
  { re: /deepseek[-\s]?r1/i, general: 65, code: 60, reasoning: 76 }, // strong pure-math/reasoning at low cost
  { re: /qwen3[-.]?[-\s]?coder/i, general: 66, code: 78, reasoning: 58 }, // SWE-bench 70.6%
  { re: /qwen3\.?6/i, general: 69, code: 77, reasoning: 61 }, // 27B posts 77.2% SWE-bench Verified
  { re: /qwen3\b/i, general: 46, code: 43, reasoning: 41, estimated: true },
  { re: /glm-5\.2/i, general: 81, code: 85, reasoning: 92 }, // AA Index 51; AIME 99.2%, GPQA 91.2%, Terminal-Bench 81.0
  { re: /kimi[-\s]?k3/i, general: 90, code: 80, reasoning: 82 }, // AA Index 57, top open-weight
  { re: /minimax[-\s]?m3/i, general: 76, code: 88, reasoning: 70 }, // SWE-bench Verified 80.5%
  { re: /minimax[-\s]?m2\.5/i, general: 60, code: 62, reasoning: 56, estimated: true },
  { re: /llama[-\s]?4[-\s]?scout/i, general: 60, code: 55, reasoning: 50, estimated: true }, // speed-optimized, not quality-leading
  { re: /llama[-\s]?3\.[1-3]/i, general: 50, code: 45, reasoning: 42, estimated: true },
  { re: /mistral[-\s]?large/i, general: 61, code: 59, reasoning: 55, estimated: true },
]

function familyBenchmark(m) {
  const combo = `${m.id || ''} ${m.name || ''}`
  for (const f of FAMILY_BENCHMARKS) {
    if (f.re.test(combo)) return f
  }
  return null
}

// ---- fallback tier for models with no benchmark-table entry ----
// This is NOT a benchmark — it's a coarse "how reputable is this lab in
// general" prior, keyed off the OpenRouter provider slug (id.split('/')[0]).
// It exists so an obscure/new model doesn't collapse to the same flat score
// as every other unlisted model; it still can't outrank anything that has a
// real FAMILY_BENCHMARKS entry (max tier below is capped under the lowest
// benchmarked score in the table). Treat this as "reasonable default
// ordering", not "measured quality".
const PROVIDER_TIER = {
  anthropic: 78,
  openai: 76,
  google: 70,
  'x-ai': 70,
  deepseek: 66,
  moonshotai: 64,
  qwen: 60,
  alibaba: 60,
  thudm: 58,
  zhipuai: 58,
  mistralai: 58,
  minimax: 56,
  cohere: 54,
  perplexity: 54,
  'meta-llama': 52,
  microsoft: 50,
  nvidia: 48,
  amazon: 48,
  ai21: 46,
  nousresearch: 44,
  liquid: 42,
  '01-ai': 42,
  allenai: 40,
  inflection: 54,
}

function scoreModel(m, task) {
  const fam = familyBenchmark(m)
  let general, code, reasoning
  if (fam) {
    ;({ general, code, reasoning } = fam)
  } else {
    const provider = (m.provider || (m.id || '').split('/')[0] || '').toLowerCase()
    let base = PROVIDER_TIER[provider] ?? 35
    base += (Math.min(m.context || 0, 262144) / 262144) * 8
    general = code = reasoning = base
  }
  if (task === 'code') return code * 0.7 + general * 0.3
  if (task === 'reasoning') return reasoning * 0.7 + general * 0.3
  return general
}

/**
 * Pick the best real model id for a task out of the current catalog.
 * tier 'free'   → only $0 models.
 * tier 'paid'   → only priced models; falls back to free if none are configured.
 * needsVision   → hard-filters to vision-capable models when the pool has any
 * (e.g. the user attached an image to ask about, not to generate one).
 * needsWeb      → steers away from the direct-Gemini pool when the catalog
 * has a non-Gemini alternative, since only the OpenRouter path in this app
 * can actually run a web search (Gemini here has no grounding tool wired
 * up). Also narrows to benchmark-verified models when any are available:
 * an obscure/unbenchmarked free model (no FAMILY_BENCHMARKS entry) is the
 * one most likely to ignore injected search results and just claim it has
 * no access to current info, which defeats the point of turning search on.
 * complexity    → 'trivial' (short small-talk) steers toward the fastest
 * tier of named models (see SPEED_TIER_0/1_RE) when the pool has any,
 * instead of the single highest-scoring (often heaviest, slowest) model —
 * speed over headroom for "hi"-tier asks.
 * realtime      → same speed bias as 'trivial', but applied regardless of
 * message length/complexity. Meant for voice/live conversations, where
 * every turn pays for latency out loud — a normal-length spoken question
 * still needs a fast answer, not just literal one-word greetings.
 * Both complexity and realtime speed-biasing are skipped whenever needsWeb
 * is true (a news/current-info ask is never "fast-track this" territory —
 * grounding a real answer needs a model that'll actually use the search
 * context) and whenever task is 'code' or 'reasoning' (those need the
 * strongest available model regardless of channel).
 * Returns null if nothing at all is available.
 */
export function resolveAutoModel({ task, tier, models, needsVision, needsWeb, complexity, realtime }) {
  let pool = (models || []).filter((m) => (tier === 'free' ? m.free : !m.free))
  if (!pool.length) pool = tier === 'paid' ? (models || []).filter((m) => m.free) : []
  if (!pool.length) return null
  if (needsVision) {
    const visionPool = pool.filter((m) => m.vision)
    if (visionPool.length) pool = visionPool
  }
  if (needsWeb) {
    const webPool = pool.filter((m) => m.provider !== 'Google (direct)')
    if (webPool.length) pool = webPool
    const verifiedPool = pool.filter((m) => familyBenchmark(m))
    if (verifiedPool.length) pool = verifiedPool
  }
  const wantsSpeed = !needsWeb && task !== 'code' && task !== 'reasoning' && (complexity === 'trivial' || realtime)
  if (wantsSpeed) {
    const sp = speedPool(pool)
    if (sp) pool = sp
  }
  let best = pool[0]
  let bestScore = scoreModel(best, task)
  for (const m of pool.slice(1)) {
    const s = scoreModel(m, task)
    if (s > bestScore) {
      best = m
      bestScore = s
    }
  }
  return best.id
}

export const AUTO_LABEL = { [AUTO_FREE_ID]: 'Auto (Free)', [AUTO_PAID_ID]: 'Auto (Paid)' }

// ---- image style detection → best underlying image backend ----
// Mirrors resolveAutoModel's job, but for image generation: instead of
// picking a chat model for a task, this picks which image backend suits the
// *style* the prompt is actually asking for. Checked most-specific first —
// in-image text takes priority over style, since a wrong-style-but-readable
// logo beats a nice-looking one with garbled text.
const IMAGE_TEXT_RE =
  /\b(logo|poster|meme|infographic|banner|flyer|flier|business card|album cover|book cover|title card|thumbnail|label|sign(age)?)\b|\btext (that says|reading)\b|\bwords?\s+["'\u201c]/i
const IMAGE_PORTRAIT_RE =
  /\b(portrait|headshot|selfie)\b|\bphoto(realistic)?\s+(of\s+)?(a |an |my )?(person|man|woman|face|people|couple)\b|\brealistic photo\b/i
const IMAGE_ART_RE =
  /\b(anime|manga|cartoon|illustration|illustrated|watercolor|oil painting|concept art|comic( book)?|pixel art|vector art|stylized|line art|sketch style)\b/i
const IMAGE_DRAFT_RE = /\b(quick|fast|rough|draft|simple)\b/i

/**
 * Pick the best Pollinations image backend for a prompt.
 * Returns { id, reason } — reason is null for the default pick (flux), so
 * callers can choose to only surface a toast/label when the choice actually
 * deviates from the baseline.
 */
export function resolveImageModel(text) {
  const t = (text || '').trim()
  if (IMAGE_TEXT_RE.test(t)) return { id: 'gptimage', reason: 'renders in-image text more reliably' }
  if (IMAGE_PORTRAIT_RE.test(t)) return { id: 'nanobanana', reason: 'stronger photorealistic portraits' }
  if (IMAGE_ART_RE.test(t)) return { id: 'seedream', reason: 'better stylized/illustration output' }
  if (IMAGE_DRAFT_RE.test(t)) return { id: 'turbo', reason: 'fastest for a quick draft' }
  return { id: 'flux', reason: null }
}
