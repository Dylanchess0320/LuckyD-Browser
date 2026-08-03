"""
Skill management tool — discover, list, and run skills from the skills/ directory.
Skills are markdown files with YAML frontmatter that define reusable agent behaviors.
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    import yaml
except ImportError:  # optional - frozen harness excludes PyYAML
    yaml = None

from .base import ToolBase, ToolOutput
from .registry import register_tool

SKILLS_DIR = Path(__file__).parent.parent / "skills"


def _parse_skill(filepath: Path) -> dict | None:
    """Parse a markdown skill file with YAML frontmatter."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return None

    # Extract YAML frontmatter
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not match:
        return None

    if yaml is None:
        return None  # PyYAML unavailable (frozen harness)
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None

    return {
        "name": frontmatter.get("name", filepath.stem),
        "description": frontmatter.get("description", ""),
        "version": frontmatter.get("version", "1.0"),
        "prompt": match.group(2).strip(),
        "file": str(filepath),
    }


class SkillListTool(ToolBase):
    name = "SkillList"
    description = "List all available skills from the skills/ directory."
    aliases = ["ListSkills", "Skills"]
    parameters = {}

    async def execute(self) -> ToolOutput:
        if not SKILLS_DIR.exists():
            return ToolOutput(text="No skills directory found.", title="Skills")

        skills = []
        for f in sorted(SKILLS_DIR.glob("*.md")):
            parsed = _parse_skill(f)
            if parsed:
                skills.append(
                    f"  - **{parsed['name']}** v{parsed['version']}: {parsed['description']}"
                )

        if not skills:
            return ToolOutput(text="No skills found in skills/ directory.", title="Skills")

        return ToolOutput(
            text="Available skills:\n\n" + "\n".join(skills),
            title=f"{len(skills)} Skills",
            metadata={"count": len(skills)},
        )


class SkillRunTool(ToolBase):
    name = "SkillRun"
    description = "Run a skill by name. This loads the skill's markdown prompt as context for the current agent turn."
    aliases = ["RunSkill", "UseSkill"]
    parameters = {
        "skill_name": {"type": "string", "description": "Name of the skill to run"},
        "context": {"type": "string", "description": "Additional context or input for the skill"},
    }

    async def execute(self, skill_name: str, context: str = "") -> ToolOutput:
        if not SKILLS_DIR.exists():
            return ToolOutput(text="No skills directory found.", error=True)

        for f in sorted(SKILLS_DIR.glob("*.md")):
            parsed = _parse_skill(f)
            if parsed and parsed["name"].lower() == skill_name.lower():
                prompt = parsed["prompt"]
                if context:
                    prompt += f"\n\n## Additional Context\n{context}"
                return ToolOutput(
                    text=prompt,
                    title=f"Skill: {parsed['name']}",
                    metadata={"skill": parsed["name"], "version": parsed["version"]},
                )

        return ToolOutput(
            text=f"Skill '{skill_name}' not found. Use SkillList to see available skills.",
            error=True,
        )


register_tool(SkillListTool())
register_tool(SkillRunTool())
