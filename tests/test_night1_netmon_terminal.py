"""Night-1 browser-core audit: netmon.py (reducer/HAR) and terminal_server.py
(shell command construction, client options, token auth)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# terminal_server.py / netmon.py import no Qt at top level (winpty and
# websockets are lazy) — but mock PySide6 anyway for uniformity.
for _mod in (
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core.netmon import MAX_ROWS, NetMonitor, to_har
from browser_core.terminal_server import (
    MESH_SHELLS,
    MESH_WSL_SHELLS,
    SHELLS,
    TerminalServer,
    _cli_command,
    _client_options,
    _client_token,
    _mesh_shell_command,
    _shell_command,
    mesh_shells_available,
)

# ── netmon reducer ───────────────────────────────────────────────────


def _req(rid="1", url="https://a.com/x", ts=100.0):
    return {
        "method": "Network.requestWillBeSent",
        "params": {
            "requestId": rid,
            "request": {"method": "GET", "url": url},
            "type": "Document",
            "timestamp": ts,
        },
    }


def test_netmon_full_lifecycle() -> None:
    m = NetMonitor()
    m._handle(_req())
    m._handle(
        {
            "method": "Network.responseReceived",
            "params": {"requestId": "1", "response": {"status": 200}, "type": "Document"},
        }
    )
    m._handle(
        {
            "method": "Network.loadingFinished",
            "params": {"requestId": "1", "encodedDataLength": 1234, "timestamp": 100.5},
        }
    )
    rows = m.rows()["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert (row["status"], row["size"], row["ms"]) == (200, 1234, 500)
    assert row["url"] == "https://a.com/x"


def test_netmon_failed_without_status_is_minus_one() -> None:
    m = NetMonitor()
    m._handle(_req())
    m._handle({"method": "Network.loadingFailed", "params": {"requestId": "1"}})
    assert m.rows()["rows"][0]["status"] == -1


def test_netmon_failed_keeps_error_status() -> None:
    m = NetMonitor()
    m._handle(_req())
    m._handle(
        {
            "method": "Network.responseReceived",
            "params": {"requestId": "1", "response": {"status": 500}},
        }
    )
    m._handle({"method": "Network.loadingFailed", "params": {"requestId": "1"}})
    assert m.rows()["rows"][0]["status"] == 500


def test_netmon_ignores_unknown_methods_and_ids() -> None:
    m = NetMonitor()
    m._handle({"method": "Network.somethingElse", "params": {}})
    m._handle(
        {
            "method": "Network.responseReceived",
            "params": {"requestId": "ghost", "response": {"status": 200}},
        }
    )
    assert m.rows()["rows"] == []


def test_netmon_max_rows_trims_and_keeps_map_honest() -> None:
    m = NetMonitor()
    for i in range(MAX_ROWS + 25):
        m._handle(_req(rid=str(i)))
    rows = m.rows()["rows"]
    assert len(rows) == MAX_ROWS
    assert rows[0]["id"] == "25"  # oldest 25 dropped
    assert len(m._by_id) == MAX_ROWS
    assert "0" not in m._by_id  # stale ids purged from the map too


def test_netmon_rows_since_and_clear() -> None:
    m = NetMonitor()
    m._handle(_req(rid="1"))
    m._handle(_req(rid="2"))
    seq = m.rows()["seq"]
    assert m.rows(since=seq)["rows"] == []
    assert len(m.rows(since=seq - 1)["rows"]) == 1
    m.clear()
    assert m.rows()["rows"] == []


def test_to_har_shape() -> None:
    m = NetMonitor()
    m._handle(_req(url="https://a.com/x", ts=100.0))
    m._handle({"method": "Network.loadingFailed", "params": {"requestId": "1"}})
    har = to_har(m.rows()["rows"], page_url="https://a.com/")
    assert har["log"]["version"] == "1.2"
    assert har["log"]["pages"] == [{"title": "https://a.com/", "id": "page_1"}]
    entry = har["log"]["entries"][0]
    assert entry["request"]["url"] == "https://a.com/x"
    assert entry["response"]["status"] == 0  # -1 clamped: HAR has no negative status
    assert entry["startedDateTime"].endswith("Z")


# ── terminal shell command construction ──────────────────────────────


def test_shell_command_system_shells() -> None:
    assert _shell_command("powershell") == ["powershell.exe", "-NoLogo", "-NoExit"]
    assert _shell_command("cmd") == ["cmd.exe"]
    assert _shell_command("  POWERSHELL  ") == ["powershell.exe", "-NoLogo", "-NoExit"]


def test_shell_command_unknown_falls_back_to_agent_cli() -> None:
    """Unknown names never become raw shell input — they resolve to the agent CLI."""
    assert _shell_command("rm -rf ~") == _shell_command("agent") == _cli_command("")
    cmd = _shell_command("agent")
    assert isinstance(cmd, list) and cmd and isinstance(cmd[0], str)


def test_mesh_shell_command_rejects_unknown() -> None:
    with pytest.raises(KeyError):
        _mesh_shell_command("mesh-nope")


def test_mesh_shells_available_shape() -> None:
    avail = mesh_shells_available()
    assert set(avail) == set(MESH_SHELLS) | set(MESH_WSL_SHELLS)
    assert all(isinstance(v, bool) for v in avail.values())


def test_shells_registry_consistency() -> None:
    for name in list(MESH_SHELLS) + list(MESH_WSL_SHELLS):
        assert name in SHELLS, f"{name} resolvable but not advertised in SHELLS"


# ── client options / token ───────────────────────────────────────────


def _ws(path: str = "", cookie: str = ""):
    return SimpleNamespace(request=SimpleNamespace(path=path, headers={"Cookie": cookie}))


def test_client_options_defaults() -> None:
    cols, rows, shell = _client_options(_ws())
    assert (cols, rows, shell) == (120, 30, "agent")


def test_client_options_parses_and_clamps() -> None:
    cols, rows, shell = _client_options(_ws("/?cols=5&rows=9999&shell=powershell"))
    assert (cols, rows, shell) == (20, 200, "powershell")  # clamped to [20,500]x[5,200]


def test_client_options_unknown_shell_falls_back() -> None:
    cols, rows, shell = _client_options(_ws("/?cols=120&rows=40&shell=evil;rm"))
    assert (cols, rows, shell) == (120, 40, "agent")


def test_client_options_bad_numbers_keep_defaults() -> None:
    cols, rows, shell = _client_options(_ws("/?cols=abc&rows="))
    assert (cols, rows, shell) == (120, 30, "agent")


def test_client_token_cookie() -> None:
    ws = _ws(cookie="other=1; luckyd_term=tok123; x=2")
    assert _client_token(ws) == "tok123"
    assert _client_token(_ws()) == ""
    # quoted cookie values are unwrapped
    assert _client_token(_ws(cookie='luckyd_term="tok456"')) == "tok456"


def test_terminal_server_authorized_fail_closed() -> None:
    srv = TerminalServer(token="")
    assert not srv._authorized(_ws(cookie="luckyd_term=anything"))  # empty token: deny
    srv2 = TerminalServer(token="secret")
    assert srv2._authorized(_ws(cookie="luckyd_term=secret"))
    assert not srv2._authorized(_ws(cookie="luckyd_term=wrong"))
    assert not srv2._authorized(_ws())


def test_terminal_server_running_flag() -> None:
    assert not TerminalServer(port=0).running
