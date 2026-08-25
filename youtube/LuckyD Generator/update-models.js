// update-models.js
// ---------------------------------------------------------------
// Keeps every LuckyD tool pinned to the BEST WORKING free model.
// Probes Google Gemini, OpenCode Zen, and OpenRouter live, ranks
// candidates, test-generates with each winner, then rewrites the
// model lines in all tool configs. Never touches API keys.
//
// Run manually:
//     node update-models.js
// (also runs weekly via Task Scheduler)
// ---------------------------------------------------------------

const fs = require("fs");
const path = require("path");

const HERE = __dirname;
const ENV_MARP = path.join(HERE, ".env");
const ENV_AGENT = path.join(process.env.USERPROFILE || "", "OneDrive", "Desktop", "coding-agent", ".env");
const ENV_BROWSER = path.join(process.env.LOCALAPPDATA || "", "Programs", "LuckyDBrowser", ".env");
const SETTINGS_BROWSER = path.join(process.env.LOCALAPPDATA || "", "LuckyDBrowser", "settings.json");

function loadEnv(file) {
    const env = {};
    if (!fs.existsSync(file)) return env;
    for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
        const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
        if (m && !line.trim().startsWith("#")) env[m[1]] = m[2].trim();
    }
    return env;
}

async function getJson(url, headers, timeoutMs) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs || 45000);
    try {
        const res = await fetch(url, { headers: headers || {}, signal: ctrl.signal });
        if (!res.ok) throw new Error("HTTP " + res.status);
        return await res.json();
    } finally {
        clearTimeout(t);
    }
}

async function postJson(url, body, headers, timeoutMs) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs || 60000);
    try {
        const res = await fetch(url, {
            method: "POST",
            headers: Object.assign({ "Content-Type": "application/json" }, headers || {}),
            body: JSON.stringify(body),
            signal: ctrl.signal,
        });
        if (!res.ok) throw new Error("HTTP " + res.status);
        return await res.json();
    } finally {
        clearTimeout(t);
    }
}

// ── Google Gemini ────────────────────────────────────────────────

async function pickGemini(key) {
    const data = await getJson(
        "https://generativelanguage.googleapis.com/v1beta/models?key=" + key + "&pageSize=100"
    );
    const usable = (data.models || [])
        .filter((m) => (m.supportedGenerationMethods || []).includes("generateContent"))
        .map((m) => m.name.replace(/^models\//, ""))
        .filter((n) => /gemini/.test(n))
        .filter((n) => !/(tts|image|embedding|aqa|live)/i.test(n));
    const score = (n) => ({
        major: Number((n.match(/^gemini-(\d+)/) || [])[1] || 0),
        pro: /pro/.test(n) ? 1 : 0,
        lite: /lite/.test(n) ? 1 : 0,
    });
    usable.sort((a, b) => {
        const A = score(a), B = score(b);
        if (A.major !== B.major) return B.major - A.major;
        if (A.pro !== B.pro) return A.pro - B.pro;
        if (A.lite !== B.lite) return A.lite - B.lite;
        return b.length - a.length;
    });
    for (const model of usable.slice(0, 5)) {
        try {
            await postJson(
                "https://generativelanguage.googleapis.com/v1beta/models/" +
                    model + ":generateContent?key=" + key,
                { contents: [{ parts: [{ text: "ping" }] }] }
            );
            return model;
        } catch (err) {
            console.log("    [skip] " + model + ": " + err.message);
        }
    }
    throw new Error("no working Gemini model found");
}

// ── OpenCode Zen ────────────────────────────────────────────────

const ZEN_PREFERENCE = [
    "deepseek-v4-flash-free",
    "big-pickle",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "mimo-v2.5-free",
];

async function pickZen(key, base) {
    const data = await getJson(base + "/models", { Authorization: "Bearer " + key });
    const freeIds = (data.data || []).map((m) => m.id).filter((id) => id.includes("free"));
    const ranked = ZEN_PREFERENCE.filter((p) => freeIds.includes(p))
        .concat(freeIds.filter((id) => !ZEN_PREFERENCE.includes(id)));
    for (const model of ranked.slice(0, 5)) {
        try {
            const r = await postJson(
                base + "/chat/completions",
                { model: model, messages: [{ role: "user", content: "ping" }] },
                { Authorization: "Bearer " + key }
            );
            if (r.choices && r.choices[0] && r.choices[0].message && r.choices[0].message.content) {
                return model;
            }
        } catch (err) {
            console.log("    [skip] " + model + ": " + err.message);
        }
    }
    throw new Error("no working Zen free model");
}

// ── OpenRouter ──────────────────────────────────────────────────

const OR_PREFERENCE = ["nemotron-3-ultra", "glm-", "deepseek-", "gemma-", "qwen"];

async function pickOpenRouter(key) {
    const data = await getJson("https://openrouter.ai/api/v1/models", {
        Authorization: "Bearer " + key,
    });
    const free = (data.data || []).map((m) => m.id).filter((id) => id.endsWith(":free"));
    const rank = (id) => {
        const i = OR_PREFERENCE.findIndex((p) => id.includes(p));
        return i === -1 ? OR_PREFERENCE.length : i;
    };
    free.sort((a, b) => rank(a) - rank(b));
    for (const model of free.slice(0, 4)) {
        try {
            const r = await postJson(
                "https://openrouter.ai/api/v1/chat/completions",
                { model: model, messages: [{ role: "user", content: "ping" }] },
                { Authorization: "Bearer " + key }
            );
            if (r.choices && r.choices[0] && r.choices[0].message && r.choices[0].message.content) {
                return model;
            }
        } catch (err) {
            console.log("    [skip] " + model + ": " + err.message);
        }
    }
    throw new Error("no working OpenRouter :free model");
}

// ── Config rewriting ────────────────────────────────────────────

function setEnvValue(file, key, value) {
    if (!fs.existsSync(file)) return false;
    let text = fs.readFileSync(file, "utf8");
    const lines = text.split(/\r?\n/);
    let found = false;
    for (let i = 0; i < lines.length; i++) {
        if (new RegExp("^" + key + "=").test(lines[i])) {
            lines[i] = key + "=" + value;
            found = true;
            break;
        }
    }
    if (!found) return false;
    fs.writeFileSync(file, lines.join("\n"), "utf8");
    return true;
}

function setJsonValue(file, keyPath, value) {
    try {
        const data = JSON.parse(fs.readFileSync(file, "utf8"));
        const parts = keyPath.split(".");
        let obj = data;
        for (let i = 0; i < parts.length - 1; i++) obj = obj[parts[i]];
        if (obj && typeof obj === "object") {
            obj[parts[parts.length - 1]] = value;
            fs.writeFileSync(file, JSON.stringify(data, null, 2), "utf8");
            return true;
        }
    } catch (err) {
        console.log("    [warn] could not update " + file + ": " + err.message);
    }
    return false;
}

// ── Main ────────────────────────────────────────────────────────

async function main() {
    const marp = loadEnv(ENV_MARP);
    const results = {};

    console.log("==> Probing Google Gemini...");
    try {
        results.google = await pickGemini(marp.GOOGLE_API_KEY);
        console.log("    best working: " + results.google);
    } catch (err) {
        console.log("    FAILED: " + err.message + " (keeping current)");
    }

    console.log("==> Probing OpenCode Zen...");
    try {
        results.zen = await pickZen(marp.ZEN_API_KEY, marp.ZEN_BASE_URL || "https://opencode.ai/zen/v1");
        console.log("    best working: " + results.zen);
    } catch (err) {
        console.log("    FAILED: " + err.message + " (keeping current)");
    }

    console.log("==> Probing OpenRouter...");
    try {
        results.openrouter = await pickOpenRouter(marp.OPENROUTER_API_KEY);
        console.log("    best working: " + results.openrouter);
    } catch (err) {
        console.log("    FAILED: " + err.message + " (keeping current)");
    }

    const updates = [];
    for (const [envFile, label] of [
        [ENV_MARP, "marp"],
        [ENV_AGENT, "agent"],
        [ENV_BROWSER, "browser"],
    ]) {
        if (!fs.existsSync(envFile)) continue;
        if (results.google && setEnvValue(envFile, "GOOGLE_MODEL", results.google))
            updates.push(label + ":GOOGLE_MODEL=" + results.google);
        if (results.zen && setEnvValue(envFile, "OPENAI_MODEL", results.zen))
            updates.push(label + ":OPENAI_MODEL=" + results.zen);
        if (results.openrouter && setEnvValue(envFile, "OPENROUTER_MODEL", results.openrouter))
            updates.push(label + ":OPENROUTER_MODEL=" + results.openrouter);
    }

    if (results.zen) {
        if (setEnvValue(ENV_MARP, "ZEN_MODEL", results.zen))
            updates.push("marp:ZEN_MODEL=" + results.zen);
        if (setEnvValue(ENV_MARP, "OPENROUTER_FREE_MODEL", results.openrouter))
            updates.push("marp:OPENROUTER_FREE_MODEL=" + results.openrouter);
        if (
            results.google &&
            setJsonValue(SETTINGS_BROWSER, "ai_model_overrides.google", results.google)
        )
            updates.push("browser-settings:google=" + results.google);
    }

    console.log("");
    console.log(updates.length ? "==> Updated:" : "==> Nothing changed.");
    for (const u of updates) console.log("    " + u);
    const stamp = new Date().toISOString() + "\n";
    fs.appendFileSync(path.join(HERE, "update-models.log"), stamp + updates.join("\n") + "\n\n");
}

main().catch((err) => {
    console.error("[!] update-models crashed: " + err.message);
    process.exit(1);
});
