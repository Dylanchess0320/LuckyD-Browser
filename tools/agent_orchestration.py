"""
Multi-agent orchestration tools: AgentHandoff, TeamCreate, SendMessage, ReceiveMessage, ListAgents.
Enables swarm/team collaboration patterns.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from .base import ToolBase, ToolOutput
from .registry import register_tool

# ── Global agent/team registry in memory ──

_agents: dict[str, dict] = {}
_teams: dict[str, dict] = {}
_message_inboxes: dict[str, list[dict]] = {}


def _register_agent(name: str, role: str = "") -> str:
    agent_id = name or str(uuid.uuid4())[:8]
    _agents[agent_id] = {
        "name": agent_id,
        "role": role,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if agent_id not in _message_inboxes:
        _message_inboxes[agent_id] = []
    return agent_id


def _send_message(to: str, from_agent: str, message: str, message_type: str = "text") -> bool:
    if to == "*":
        for agent_id in _message_inboxes:
            if agent_id != from_agent:
                _message_inboxes.setdefault(agent_id, []).append(
                    {
                        "from": from_agent,
                        "message": message,
                        "type": message_type,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
        return True
    if to in _message_inboxes:
        _message_inboxes[to].append(
            {
                "from": from_agent,
                "message": message,
                "type": message_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        return True
    _message_inboxes[to] = [
        {
            "from": from_agent,
            "message": message,
            "type": message_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    ]
    return True


class AgentHandoffTool(ToolBase):
    """Hand off a subtask to a specialist role: researcher, coder, reviewer, tester."""

    name = "AgentHandoff"
    description = "Hand off a subtask to a specialist agent. Use: researcher (web search, docs), coder (implement), reviewer (audit), tester (test). Chain: researcher → coder → reviewer. Optionally pin the specialist to a specific model (e.g. a different local Ollama model) via 'model'."
    aliases = ["Handoff", "DelegateTo"]
    timeout_sec = 600.0
    parameters = {
        "role": {
            "type": "string",
            "enum": ["researcher", "coder", "reviewer", "tester"],
            "description": "The specialist role to hand off to",
        },
        "task": {"type": "string", "description": "The specific task for the specialist agent"},
        "model": {
            "type": "string",
            "description": "Optional model override for this specialist (e.g. an Ollama model name like 'mistral' or 'phi3'). Defaults to the main agent's configured model.",
        },
        "base_url": {
            "type": "string",
            "description": "Optional API base URL override for this specialist (e.g. a different Ollama host). Defaults to the main agent's configured base_url.",
        },
    }

    async def execute(self, role: str, task: str, model: str = "", base_url: str = "") -> ToolOutput:
        agent_name = f"{role}-{uuid.uuid4().hex[:6]}"
        _register_agent(agent_name, role)

        from agent import CodingAgent
        from config import get_config

        cfg = get_config()
        sub = CodingAgent(
            api_key=cfg["api_key"],
            base_url=base_url.strip() or cfg["base_url"],
            model=model.strip() or cfg["model"],
            temperature=cfg["temperature"],
            max_tokens=cfg["max_tokens"],
        )

        # Specialist system prompt prefix
        role_prompts = {
            "researcher": "You are a RESEARCHER. Research thoroughly using WebSearch and WebFetch before answering. Never write code — just research facts, APIs, docs, and best practices.",
            "coder": "You are a CODER. Implement the requested feature precisely. Read existing files before editing. Match existing patterns. Minimal diffs.",
            "reviewer": "You are a CODE REVIEWER. Audit code for bugs, security issues, style violations. Be thorough. Report every issue found.",
            "tester": "You are a TESTER. Write and run tests for the specified module. Verify edge cases and error handling.",
        }

        prompt = role_prompts.get(role, "") + f"\n\nTask: {task}"

        try:
            result = await sub.run(prompt, max_turns=min(sub.max_turns, 15))
            return ToolOutput(
                text=result,
                title=f"{role.title()} Result",
                metadata={"role": role, "agent": agent_name, "turns": sub.turn_count},
            )
        except Exception as e:
            return ToolOutput(text=f"{role.title()} error: {e}", error=True)


class TeamCreateTool(ToolBase):
    """Create a team of agents that work in parallel."""

    name = "TeamCreate"
    description = "Create a multi-agent swarm team that works in parallel. Each agent gets a name, role, and task. They run simultaneously."
    aliases = ["Swarm", "ParallelAgents"]
    timeout_sec = 900.0
    parameters = {
        "team_name": {
            "type": "string",
            "description": "Short name for this team (e.g. 'feature-x-team')",
        },
        "description": {"type": "string", "description": "What this team is building/doing"},
        "agents": {
            "type": "array",
            "description": "List of agents with: name, role, task, model (optional), max_turns (optional)",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Agent handle (e.g. 'backend-dev')"},
                    "role": {
                        "type": "string",
                        "description": "Agent specialty (e.g. 'Python backend developer')",
                    },
                    "task": {
                        "type": "string",
                        "description": "Detailed task. Be specific — include files, functions, acceptance criteria.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model override for this agent (e.g. a different local Ollama model per team member, like 'phi3' vs 'mistral'). Defaults to the main agent's configured model.",
                    },
                    "base_url": {
                        "type": "string",
                        "description": "Optional API base URL override for this agent (e.g. a different Ollama host). Defaults to the main agent's configured base_url.",
                    },
                    "max_turns": {
                        "type": "integer",
                        "description": "Optional max turns for this agent (default 10, capped at 20).",
                    },
                },
                "required": ["name", "role", "task"],
            },
        },
    }

    async def execute(
        self, team_name: str, description: str = "", agents: list | None = None
    ) -> ToolOutput:
        if not agents:
            return ToolOutput(text="Must provide at least one agent", error=True)

        team_id = f"team-{uuid.uuid4().hex[:8]}"
        _teams[team_id] = {
            "name": team_name,
            "description": description,
            "agents": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        from agent import CodingAgent
        from config import get_config

        cfg = get_config()

        async def _run_agent(agent_def: dict) -> str:
            name = agent_def.get("name", "agent")
            role = agent_def.get("role", "")
            task = agent_def.get("task", "")
            max_turns = min(agent_def.get("max_turns", 10), 20)
            model = str(agent_def.get("model", "") or "").strip()
            base_url = str(agent_def.get("base_url", "") or "").strip()
            _register_agent(name, role)

            sub = CodingAgent(
                api_key=cfg["api_key"],
                base_url=base_url or cfg["base_url"],
                model=model or cfg["model"],
                temperature=cfg["temperature"],
                max_tokens=cfg["max_tokens"],
            )
            try:
                result = await sub.run(f"Role: {role}\n\nTask: {task}", max_turns=max_turns)
                return f"## {name} ({role})\n\n{result}"
            except Exception as e:
                return f"## {name} ({role})\n\nError: {e}"

        tasks = [_run_agent(a) for a in agents]
        results = await asyncio.gather(*tasks)

        combined = "\n\n---\n\n".join(results)
        return ToolOutput(
            text=combined,
            title=f"Team: {team_name} ({len(agents)} agents)",
            metadata={"team_id": team_id, "agent_count": len(agents)},
        )


class SendMessageTool(ToolBase):
    """Send a message to another agent."""

    name = "SendMessage"
    description = "Send a message to another agent or broadcast to all agents in your team."
    aliases = ["AgentMessage", "NotifyAgent"]
    parameters = {
        "to": {"type": "string", "description": "Recipient agent name, or '*' for broadcast"},
        "message": {"type": "string", "description": "The message content"},
        "message_type": {
            "type": "string",
            "description": "Type: text, shutdown_request, shutdown_response, status_update",
        },
    }

    async def execute(self, to: str, message: str, message_type: str = "text") -> ToolOutput:
        sent = _send_message(to, "main_agent", message, message_type)
        return ToolOutput(
            text=f"Message sent to {to}" if sent else f"Failed to send to {to}",
            title=f"Message → {to}",
            metadata={"to": to, "type": message_type},
        )


class ReceiveMessageTool(ToolBase):
    """Check for incoming messages."""

    name = "ReceiveMessage"
    description = "Read pending messages from your agent inbox."
    aliases = ["CheckMessages", "Inbox"]
    parameters = {
        "agent_name": {"type": "string", "description": "Your agent name (default: main_agent)"},
    }

    async def execute(self, agent_name: str = "main_agent") -> ToolOutput:
        msgs = _message_inboxes.pop(agent_name, [])
        if not msgs:
            return ToolOutput(text="No pending messages.", title="Inbox (0)")

        lines = []
        for i, m in enumerate(msgs):
            lines.append(f"  [{i}] From: {m['from']} | Type: {m['type']}")
            lines.append(f"      {m['message']}")
            lines.append("")

        return ToolOutput(
            text="\n".join(lines),
            title=f"Inbox ({len(msgs)} messages)",
            metadata={"count": len(msgs)},
        )


class ListAgentsTool(ToolBase):
    """List all known agents."""

    name = "ListAgents"
    description = "List all known agents and teams."
    aliases = ["Agents", "Teams"]
    parameters = {}

    async def execute(self) -> ToolOutput:
        lines = ["## Agents:"]
        for aid, info in _agents.items():
            unread = len(_message_inboxes.get(aid, []))
            lines.append(f"  - {aid} ({info.get('role', 'unknown')}) | {unread} pending msgs")

        lines.append("\n## Teams:")
        for tid, team in _teams.items():
            lines.append(f"  - {tid}: {team['name']} ({len(team.get('agents', []))} agents)")

        return ToolOutput(
            text="\n".join(lines),
            title="Known Agents & Teams",
            metadata={"agents": len(_agents), "teams": len(_teams)},
        )


# Auto-register
register_tool(AgentHandoffTool())
register_tool(TeamCreateTool())
register_tool(SendMessageTool())
register_tool(ReceiveMessageTool())
register_tool(ListAgentsTool())
