# API Reference

The LuckyD Control API runs by default on `http://127.0.0.1:9777` (or `8000` for the harness).

---

## Endpoints

### `GET /status`
Returns system health, active AI provider, loaded agents, and open browser tabs.

### `GET /tabs`
Returns a list of currently open browser tabs with titles and URLs.

### `POST /ask`
Direct natural language Q&A against the active LLM with optional page snapshot context.
- **Request Body**: `{"question": "...", "provider": "..."}`
- **Response**: `{"ok": true, "answer": "..."}`

### `POST /act`
Executes an agent action on the active browser tab (click, type, scroll, key).

### `POST /snapshot`
Captures an accessibility snapshot and text extract of the active page.

### `POST /eval`
Evaluates arbitrary JavaScript in the active tab context.

### `GET /screenshot`
Returns a base64-encoded JPEG capture of the requested or active tab.
