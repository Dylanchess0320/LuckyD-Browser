"""
AskUserQuestion - structured multi-choice prompts to gather preferences.
"""

from __future__ import annotations

from .base import ToolBase, ToolOutput
from .registry import register_tool


class AskUserQuestionTool(ToolBase):
    name = "AskUserQuestion"
    description = "Ask the user structured multiple-choice questions to gather preferences, clarify ambiguous instructions, or get decisions on implementation choices."
    aliases = ["AskUser", "PromptUser"]
    parameters = {
        "questions": {
            "type": "array",
            "description": "1-4 questions to ask the user",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question text"},
                    "header": {"type": "string", "description": "Very short label (max 12 chars)"},
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {
                                    "type": "string",
                                    "description": "Short display text (1-5 words)",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Explanation of what this option means",
                                },
                            },
                            "required": ["label", "description"],
                        },
                    },
                    "multi_select": {
                        "type": "boolean",
                        "description": "Allow multiple selections",
                        "default": False,
                    },
                },
                "required": ["question", "header", "options"],
            },
        },
    }

    async def execute(self, questions: list) -> ToolOutput:
        lines = []
        for i, q in enumerate(questions):
            header = q.get("header", f"Q{i + 1}")
            multi = " [multi-select]" if q.get("multi_select") else ""
            lines.append(f"\n{'=' * 50}")
            lines.append(f"  {header}{multi}: {q['question']}")
            lines.append(f"{'=' * 50}")
            for j, opt in enumerate(q.get("options", [])):
                lines.append(f"  [{j + 1}] {opt['label']} — {opt['description']}")
            lines.append("  [0] Custom answer")

        lines.append("\nReply with your choices (e.g. '1,3' or 'all' or custom text).")

        return ToolOutput(
            text="\n".join(lines),
            title=f"Questions ({len(questions)})",
            metadata={"questions": questions},
        )


register_tool(AskUserQuestionTool())
