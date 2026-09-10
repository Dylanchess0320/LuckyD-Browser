#!/usr/bin/env python3
"""Regenerate skills/registry.json from the bundled skills/ directory.

Run after adding or editing a bundled skill so the published sha256 hashes
stay honest. The marketplace test suite verifies the hashes match.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"


def main() -> None:
    skills = []
    for f in sorted(SKILLS.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        fm = yaml.safe_load(m.group(1)) if m else {}
        skills.append(
            {
                "name": str(fm.get("name", f.stem)),
                "description": str(fm.get("description", "")),
                "version": str(fm.get("version", "1.0")),
                "author": str(fm.get("author", "LuckyD")),
                "tags": list(fm.get("tags", []) or []),
                "url": f.name,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    registry = {
        "registry": "luckyd-bundled",
        "description": (
            "Skills bundled with LuckyD Browser. Open format — anyone can host "
            "their own registry.json like this one."
        ),
        "skills": skills,
    }
    (SKILLS / "registry.json").write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(skills)} entries to skills/registry.json")


if __name__ == "__main__":
    main()
