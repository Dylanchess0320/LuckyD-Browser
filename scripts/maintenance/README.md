# Maintenance scripts

One-off fix/patch utility scripts that were cluttering the project root. They
are not imported or referenced by anything in this repo — most were quick
string-replacement helpers (some for the external `movie-recommender` Android
project). Kept for reference; safe to delete.

| Script | Purpose (best guess from content) |
|--------|-----------------------------------|
| `fix_feed2.py`, `fix_feed3.py`, `fix_main.py`, `fix2.py` | One-off Kotlin/Android source patches |
| `fix_encoding.py` | File-encoding fix helper |
| `replace.py` | Generic string-replace helper |
| `check_xml.py` | XML inspection helper |
| `find_section.py` | File section locator helper |
| `write_palette.py` | Palette writer snippet |
| `_fix_browser.py` | One-off browser-module patch |
| `.fix_all_perms.py` | Permissions fix helper |

Moved here during the folder organization on 2026-07-28.
