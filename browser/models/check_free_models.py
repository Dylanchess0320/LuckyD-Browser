#!/usr/bin/env python3
"""
LuckyD Browser - Free Models Status Checker

Reads browser/models/providers_config.json (synced from opencode's open
model registry, models.dev) and prints every free model per provider.
"""

import json
from pathlib import Path

CONFIG = Path(__file__).parent / "providers_config.json"


def main():
    print("LuckyD Browser - Free Models Status Checker")
    print("=" * 50)

    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read {CONFIG.name}: {exc}")
        return

    providers = config.get("ai_providers", {})
    total = 0
    for pid, p in sorted(providers.items()):
        models = p.get("free_models", [])
        total += len(models)
        print(f"\n{p.get('name', pid)} ({pid}):")
        for model in models:
            marker = " *" if model == p.get("recommended") else ""
            print(f"  - {model}{marker}")
        if p.get("note"):
            print(f"    note: {p['note']}")

    print("\n" + "=" * 50)
    print(f"{len(providers)} providers, {total} free models")
    print(f"default provider: {config.get('default_provider')}")
    print("\nTo switch models:")
    print("  python main.py model <model-name>")
    print("  Example: python main.py model nemotron-3-ultra-free")
    print("")
    print("Current settings in browser/data/settings.json")
    print("See models/FREE_MODELS_CATALOG.md for details")


if __name__ == "__main__":
    main()
