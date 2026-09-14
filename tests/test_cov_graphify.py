"""Coverage tests for tools/graphify_tool.py.

Covers _run_graphify (success, stderr merge, no output, timeout, missing
binary, generic error — via a faked asyncio.create_subprocess_exec) and every
GraphifyTool subcommand (info / god-nodes / path / explain / query /
affected / tree / unknown) with a hand-written fake runner. No real
subprocesses are spawned.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import tools.graphify_tool as g
from tools.graphify_tool import GraphifyTool


class _FakeRunner:
    """Stand-in for _run_graphify recording its calls."""

    def __init__(self, out: str = "(fake)", code: int = 0):
        self.out = out
        self.code = code
        self.calls: list[tuple] = []

    async def __call__(self, *args):
        self.calls.append(args)
        return self.out, self.code


@pytest.fixture()
def fake_runner(monkeypatch):
    runner = _FakeRunner()
    monkeypatch.setattr(g, "_run_graphify", runner)
    return runner


@pytest.fixture()
def fake_graph(tmp_path, monkeypatch):
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    graph_file = graph_dir / "graph.json"
    monkeypatch.setattr(g, "DEFAULT_GRAPH", graph_file)
    monkeypatch.setattr(g, "PROJECT_ROOT", tmp_path)
    return graph_file, graph_dir


# ── _run_graphify (real function, faked subprocess layer) ────────────────


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes, code: int):
        self._stdout = stdout
        self._stderr = stderr
        self._code = code

    async def communicate(self):
        return self._stdout, self._stderr

    @property
    def returncode(self):
        return self._code


def _patch_exec(monkeypatch, proc=None, exc=None):
    async def fake_exec(*args, **kwargs):
        if exc is not None:
            raise exc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


async def test_run_graphify_success_with_stderr(monkeypatch):
    _patch_exec(monkeypatch, _FakeProc(b"out", b"err", 3))
    out, code = await g._run_graphify("god-nodes")
    assert out == "out\nerr"
    assert code == 3


async def test_run_graphify_stderr_only(monkeypatch):
    _patch_exec(monkeypatch, _FakeProc(b"", b"only-err", 0))
    out, code = await g._run_graphify("info")
    assert out == "only-err"
    assert code == 0


async def test_run_graphify_no_output(monkeypatch):
    _patch_exec(monkeypatch, _FakeProc(b"", b"", 0))
    out, code = await g._run_graphify("info")
    assert out == "(no output)"
    assert code == 0


async def test_run_graphify_timeout(monkeypatch):
    class _TimeoutProc(_FakeProc):
        async def communicate(self):
            raise asyncio.TimeoutError()

    _patch_exec(monkeypatch, _TimeoutProc(b"", b"", 0))
    out, code = await g._run_graphify("info")
    assert out == "graphify timed out"
    assert code == -1


async def test_run_graphify_missing_binary(monkeypatch):
    _patch_exec(monkeypatch, exc=FileNotFoundError())
    out, code = await g._run_graphify("info")
    assert "not installed" in out
    assert code == 1


async def test_run_graphify_generic_error(monkeypatch):
    _patch_exec(monkeypatch, exc=RuntimeError("weird"))
    out, code = await g._run_graphify("info")
    assert out == "Error: weird"
    assert code == 1


# ── info ──────────────────────────────────────────────────────────────────


async def test_info_no_graph(fake_graph):
    graph_file, _ = fake_graph
    assert not graph_file.exists()
    out = await GraphifyTool().execute(subcommand="info")
    assert out.error
    assert "No graph found" in out.text


async def test_info_malformed_graph(fake_graph):
    graph_file, _ = fake_graph
    graph_file.write_text("{not json", encoding="utf-8")
    out = await GraphifyTool().execute(subcommand="info")
    assert out.error
    assert "Error reading graph" in out.text


async def test_info_success(fake_graph):
    graph_file, _ = fake_graph
    graph_file.write_text(
        json.dumps(
            {
                "nodes": [
                    {"community": 0},
                    {"community": 1},
                    {"community": -1},
                    {},
                ],
                "links": [{"a": 1}, {"a": 2}],
            }
        ),
        encoding="utf-8",
    )
    out = await GraphifyTool().execute(subcommand="info")
    assert not out.error
    assert out.title == "Graphify Info"
    assert "4 nodes, 2 edges, 2 communities" in out.text
    assert out.metadata == {"nodes": 4, "edges": 2, "communities": 2}


# ── god-nodes ─────────────────────────────────────────────────────────────


async def test_god_nodes_explicit_arg1(fake_runner, fake_graph):
    graph_file, _ = fake_graph
    runner = fake_runner
    runner.out, runner.code = "node-a\nnode-b", 0
    out = await GraphifyTool().execute(subcommand="god-nodes", arg1="5")
    assert not out.error
    assert out.title == "God Nodes (top 5)"
    assert out.text == "node-a\nnode-b"
    assert runner.calls[-1] == ("god-nodes", "--graph", str(graph_file), "--top", "5")


async def test_god_nodes_defaults_to_top_n(fake_runner, fake_graph):
    runner = fake_runner
    out = await GraphifyTool().execute(subcommand="god-nodes", top_n=3)
    assert not out.error
    assert out.title == "God Nodes (top 3)"
    assert runner.calls[-1][-1] == "3"


async def test_god_nodes_nonnumeric_arg1_uses_top_n(fake_runner):
    runner = fake_runner
    await GraphifyTool().execute(subcommand="god-nodes", arg1="many", top_n=7)
    assert runner.calls[-1][-1] == "7"


async def test_god_nodes_error_code(fake_runner):
    runner = fake_runner
    runner.out, runner.code = "failed", 1
    out = await GraphifyTool().execute(subcommand="god-nodes")
    assert out.error
    assert out.text == "failed"


# ── path / explain ────────────────────────────────────────────────────────


async def test_path_missing_args():
    out = await GraphifyTool().execute(subcommand="path", arg1="a")
    assert out.error
    assert "Usage: path" in out.text


async def test_path_success(fake_runner, fake_graph):
    graph_file, _ = fake_graph
    runner = fake_runner
    runner.out = "a -> b"
    out = await GraphifyTool().execute(subcommand="path", arg1="a", arg2="b")
    assert not out.error
    assert out.title == "Path: a → b"
    assert runner.calls[-1] == ("path", "a", "b", "--graph", str(graph_file))


async def test_explain_missing_arg():
    out = await GraphifyTool().execute(subcommand="explain")
    assert out.error
    assert "Usage: explain" in out.text


async def test_explain_success(fake_runner):
    runner = fake_runner
    runner.out, runner.code = "node info", 0
    out = await GraphifyTool().execute(subcommand="explain", arg1="mymod")
    assert not out.error
    assert out.title == "Explain: mymod"
    assert runner.calls[-1][1] == "mymod"


# ── query ─────────────────────────────────────────────────────────────────


async def test_query_missing_arg():
    out = await GraphifyTool().execute(subcommand="query")
    assert out.error
    assert "Usage: query" in out.text


async def test_query_with_dfs_flag(fake_runner):
    runner = fake_runner
    runner.out = "dfs result"
    out = await GraphifyTool().execute(subcommand="query", arg1="q", arg2="--dfs")
    assert not out.error
    assert out.title == "Query: q..."
    assert "--dfs" in runner.calls[-1]


async def test_query_without_dfs_and_error_code(fake_runner):
    runner = fake_runner
    runner.out, runner.code = "bfs failed", 2
    out = await GraphifyTool().execute(subcommand="query", arg1="q")
    assert out.error
    assert "--dfs" not in runner.calls[-1]


async def test_query_title_truncates_long_question(fake_runner):
    long_q = "x" * 100
    out = await GraphifyTool().execute(subcommand="query", arg1=long_q)
    assert out.title == f"Query: {'x' * 60}..."


# ── affected ──────────────────────────────────────────────────────────────


async def test_affected_missing_arg():
    out = await GraphifyTool().execute(subcommand="affected")
    assert out.error
    assert "Usage: affected" in out.text


async def test_affected_success_with_depth(fake_runner):
    runner = fake_runner
    runner.out = "impact"
    out = await GraphifyTool().execute(subcommand="affected", arg1="mymod", depth=4)
    assert not out.error
    assert out.title == "Affected by: mymod"
    assert out.text == "impact"
    args = runner.calls[-1]
    assert args[0] == "affected"
    assert "--depth" in args
    assert args[args.index("--depth") + 1] == "4"


# ── tree ──────────────────────────────────────────────────────────────────


async def test_tree_appends_html_path_when_present(fake_runner, fake_graph):
    _, graph_dir = fake_graph
    runner = fake_runner
    runner.out = "tree-out"
    html = graph_dir / "GRAPH_TREE.html"
    html.write_text("<html></html>", encoding="utf-8")
    out = await GraphifyTool().execute(subcommand="tree")
    assert not out.error
    assert out.title == "Graph Tree"
    assert f"Open in browser: {html}" in out.text
    assert runner.calls[-1][0] == "tree"


async def test_tree_without_html_file(fake_runner, fake_graph):
    runner = fake_runner
    runner.out, runner.code = "tree-out", 0
    out = await GraphifyTool().execute(subcommand="tree")
    assert not out.error
    assert "Open in browser" not in out.text


async def test_tree_error_code(fake_runner):
    runner = fake_runner
    runner.out, runner.code = "nope", 1
    out = await GraphifyTool().execute(subcommand="tree")
    assert out.error


# ── unknown ───────────────────────────────────────────────────────────────


async def test_unknown_subcommand():
    out = await GraphifyTool().execute(subcommand="frobnicate")
    assert out.error
    assert "Unknown subcommand: frobnicate" in out.text
