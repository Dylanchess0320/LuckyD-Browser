"""
CodingAgent — backward-compatibility shim.

The canonical implementation now lives in core/agent_loop.py (modular architecture
using core/llm_client.py, core/message_builder.py, core/context_manager.py,
core/hooks.py, and core/checkpoint.py). This module re-exports it so that
existing imports (from agent import CodingAgent) continue to work.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tools.agent_orchestration
import tools.ask_question_tool
import tools.bash_tool
import tools.brief_tool
import tools.browser_tools
import tools.config_tool
import tools.data_tools
import tools.datetime_tools

# Auto-register built-in tools (populates the tool registry)
import tools.file_tools
import tools.git_tools
import tools.graphify_tool
import tools.harness_tool
import tools.lsp_tools
import tools.memory_tools
import tools.plan_tools
import tools.session_tools
import tools.skill_tools
import tools.subagent_tool
import tools.task_tools
import tools.utility_tools
import tools.web_tools  # noqa: F401
from config import PROJECT_DIR
from project import ProjectDetector

_project_info = None
try:
    _detector = ProjectDetector()
    _project_info = _detector.detect(PROJECT_DIR)
except Exception:
    pass

from core.agent_loop import CodingAgent  # noqa: F401

SYSTEM_PROMPT = """You are LuckyD Code, an AI coding assistant in a terminal.

Answer concisely. For code: use Bash/Read/Write/Edit/Glob/Grep tools.
For questions: answer directly.
"""
