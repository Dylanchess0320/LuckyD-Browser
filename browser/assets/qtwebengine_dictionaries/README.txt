Drop hunspell .bdic dictionaries here to enable spell checking
(e.g. en-US.bdic). Qt WebEngine picks them up via QTWEBENGINE_DICTIONARIES_PATH,
which LuckyD Browser sets automatically when at least one .bdic exists.

Compiled .bdic files ship with Chromium-based apps (VS Code, Slack, etc.) —
copy one in, restart the browser, and right-click a misspelled word for
suggestions.
