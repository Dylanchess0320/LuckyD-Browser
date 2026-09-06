"""Live rich TUI showing swarm activity. Degrades to plain logging if unavailable."""

from __future__ import annotations

import contextlib
from collections import defaultdict

from ..schemas import ResearchPlan
from .events import EventEmitter, RunEvent


class SwarmDisplay:
    """Subscribes to run events and renders a live status panel with rich."""

    def __init__(self, query: str, emitter: EventEmitter, enabled: bool = True) -> None:
        self.query = query
        self.enabled = enabled and _rich_ok()
        self.node: str = "init"
        self.plan: ResearchPlan | None = None
        self.task_status: dict[str, str] = defaultdict(lambda: "pending")
        self.evidence_count = 0
        self.critique_status = "—"
        self.iteration = 0
        self.events: list[RunEvent] = []
        self._live = None
        emitter.subscribe(self._on_event)

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if not self.enabled:
            return
        from rich.live import Live

        self._live = Live(self._render(), refresh_per_second=8, transient=False)
        self._live.start()

    def stop(self) -> None:
        if self._live is not None:
            self._live.update(self._render())
            self._live.stop()
            self._live = None

    # -- event handling ---------------------------------------------------
    def _on_event(self, ev: RunEvent) -> None:
        self.events.append(ev)
        self.node = ev.node
        if ev.node == "planner" and ev.level == "ok":
            pass  # plan attached separately via attach_plan
        if ev.node == "research" and ev.level == "ok":
            self.task_status[ev.message] = "done"
        if ev.node == "critique":
            self.critique_status = ev.message
        if ev.node == "synthesize":
            self.iteration += 1
        if ev.node == "researcher":
            self.evidence_count += 1
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._live.update(self._render())

    def attach_plan(self, plan: ResearchPlan) -> None:
        self.plan = plan
        for t in plan.tasks:
            self.task_status.setdefault(t.id, "pending")

    def set_evidence_count(self, n: int) -> None:
        self.evidence_count = n

    # -- rendering --------------------------------------------------------
    def _render(self):
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        title = Text("Deep Research Swarm", style="bold cyan")
        head = Table.grid(padding=(0, 1))
        head.add_column(justify="right", style="bold")
        head.add_column()
        head.add_row("Query:", self.query[:80])
        head.add_row("Node:", self.node)
        head.add_row("Evidence:", str(self.evidence_count))
        head.add_row("Critique:", self.critique_status)
        head.add_row("Iteration:", str(self.iteration))

        body = [title, head, Text("")]

        if self.plan:
            pt = Table(title="Research Plan", expand=True)
            pt.add_column("ID", style="dim")
            pt.add_column("Question")
            pt.add_column("Status", justify="right")
            for t in self.plan.tasks:
                st = self.task_status.get(t.id, "pending")
                style = "green" if st == "done" else "yellow"
                pt.add_row(t.id, t.question[:60], Text(st, style=style))
            body.append(pt)

        log = Table(title="Recent Activity", expand=True)
        log.add_column("Time", style="dim", width=8)
        log.add_column("Node", style="bold")
        log.add_column("Message")
        for ev in self.events[-8:]:
            log.add_row(f"{ev.ts:.0f}", ev.node, ev.message[:70])
        body.append(log)

        return Panel(Group(*body), border_style="cyan")

    def log_fallback(self, ev: RunEvent) -> None:
        print(f"[{ev.node}] {ev.message}")


def _rich_ok() -> bool:
    try:
        import rich  # noqa: F401

        return True
    except Exception:
        return False
