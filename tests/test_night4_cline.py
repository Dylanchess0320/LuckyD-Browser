"""Night-4 browser-core audit: cline_session.py round 1.

No dedicated tests existed. Covers the CLI session-file discovery,
token iteration/preference order, validity window, JWT decoding,
client-id extraction, the WorkOS refresh-and-store cycle (HTTP mocked),
and fresh_token()'s fallback ladder. CLINE_DATA_DIR redirects the
session lookup into tmp dirs — never touches the real ~/.cline.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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

import browser_core.cline_session as cs


def _jwt(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{body}.sig"


@pytest.fixture()
def cline_dir(tmp_path, monkeypatch):
    d = tmp_path / "cline"
    (d / "settings").mkdir(parents=True)
    monkeypatch.setenv("CLINE_DATA_DIR", str(d))
    return d


def _write_providers(cline_dir, providers: dict) -> Path:
    p = cline_dir / "settings" / "providers.json"
    p.write_text(json.dumps({"providers": providers}), encoding="utf-8")
    return p


def _entry(access, expires_ms, refresh, updated="2026-01-01T00:00:00Z"):
    return {
        "settings": {
            "auth": {
                "accessToken": access,
                "refreshToken": refresh,
                "expiresAt": expires_ms,
            }
        },
        "updatedAt": updated,
    }


_NOW_MS = int(time.time() * 1000)
_FUTURE = _NOW_MS + 3_600_000
_PAST = _NOW_MS - 3_600_000


# ── discovery / reading ──────────────────────────────────────────────


def test_providers_path_honors_env(cline_dir) -> None:
    assert cs._providers_path() == cline_dir / "settings" / "providers.json"


def test_providers_path_default_home(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CLINE_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert cs._providers_path() == tmp_path / ".cline" / "data" / "settings" / "providers.json"


def test_read_providers_missing_file(cline_dir) -> None:
    with pytest.raises(RuntimeError, match="Cline CLI session not found"):
        cs._read_providers()


def test_read_providers_bad_json(cline_dir) -> None:
    (cline_dir / "settings" / "providers.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot parse"):
        cs._read_providers()


def test_has_session_false_when_missing(cline_dir) -> None:
    assert cs.has_session() is False


def test_has_session_true_with_expired_token(cline_dir) -> None:
    _write_providers(cline_dir, {"cline-pass": _entry("tok", _PAST, "ref")})
    assert cs.has_session() is True


def test_iter_tokens_prefers_cline_pass_first(cline_dir) -> None:
    _write_providers(
        cline_dir,
        {
            "cline": _entry("cline-tok", _FUTURE, "ref-c"),
            "cline-pass": _entry("pass-tok", _FUTURE, "ref-p"),
        },
    )
    toks = cs._iter_tokens()
    assert [t[0] for t in toks] == ["cline-pass", "cline"]
    assert toks[0][1] == "pass-tok"


def test_iter_tokens_skips_empty_auth(cline_dir) -> None:
    _write_providers(
        cline_dir,
        {
            "cline-pass": _entry("", 0, ""),
            "other": _entry("x", 0, ""),
        },
    )
    assert cs._iter_tokens() == []


def test_iter_tokens_trims_whitespace(cline_dir) -> None:
    _write_providers(cline_dir, {"cline-pass": _entry("  tok  ", _FUTURE, "  ref  ")})
    assert cs._iter_tokens()[0][1] == "tok"
    assert cs._iter_tokens()[0][3] == "ref"


def test_load_session_prefers_cline_pass(cline_dir) -> None:
    _write_providers(
        cline_dir,
        {
            "cline": _entry("cline-tok", _FUTURE, "ref-c"),
            "cline-pass": _entry("pass-tok", _FUTURE, "ref-p"),
        },
    )
    token, expires = cs.load_session()
    assert (token, expires) == ("pass-tok", _FUTURE)


def test_load_session_no_tokens_raises(cline_dir) -> None:
    _write_providers(cline_dir, {})
    with pytest.raises(RuntimeError, match="no Cline session token found"):
        cs.load_session()


# ── validity + stored_token ──────────────────────────────────────────


def test_valid_window() -> None:
    assert cs._valid(0) is True  # no expiry recorded → trusted
    assert cs._valid(int(time.time() * 1000) + 3_600_000) is True
    assert cs._valid(int(time.time() * 1000) - 1) is False
    assert cs._valid(int(time.time() * 1000) + 30_000) is False  # 60s safety margin


def test_stored_token_returns_first_valid(cline_dir) -> None:
    _write_providers(
        cline_dir,
        {
            "cline-pass": _entry("expired-tok", _PAST, "ref"),
            "cline": _entry("fresh-tok", _FUTURE, "ref"),
        },
    )
    assert cs.stored_token() == "fresh-tok"


def test_stored_token_all_expired_raises(cline_dir) -> None:
    _write_providers(cline_dir, {"cline-pass": _entry("tok", _PAST, "ref")})
    with pytest.raises(RuntimeError, match="no valid Cline session token stored"):
        cs.stored_token()


# ── JWT decoding / client id ─────────────────────────────────────────


def test_decode_jwt_claims() -> None:
    token = "workos:" + _jwt({"client_id": "client_ABC", "exp": 123})
    assert cs._decode_jwt(token) == {"client_id": "client_ABC", "exp": 123}


def test_decode_jwt_without_prefix() -> None:
    token = _jwt({"sub": "u1"})
    assert cs._decode_jwt(token)["sub"] == "u1"


def test_decode_jwt_garbage_is_empty() -> None:
    assert cs._decode_jwt("not-a-jwt") == {}
    assert cs._decode_jwt("a.b") == {}  # bad base64 payload


def test_client_id_from_claims() -> None:
    toks = [("cline-pass", "workos:" + _jwt({"client_id": "client_X"}), 0, "r")]
    assert cs._client_id(toks) == "client_X"


def test_client_id_from_iss() -> None:
    toks = [("cline", "workos:" + _jwt({"iss": "https://x/client_99ZZ"}), 0, "r")]
    assert cs._client_id(toks) == "client_99ZZ"


def test_client_id_default_fallback() -> None:
    assert cs._client_id([("cline-pass", "tok", 0, "r")]) == cs._DEFAULT_CLIENT_ID


# ── refresh and store ────────────────────────────────────────────────


def _providers_two_entries(
    expires_pass, expires_cline, refresh_pass="REF-P", refresh_cline="REF-C"
):
    return {
        "cline-pass": _entry("workos:old-access", expires_pass, refresh_pass),
        "cline": _entry("workos:old-access-2", expires_cline, refresh_cline),
    }


def test_refresh_and_store_updates_matching_entries(cline_dir, monkeypatch) -> None:
    _write_providers(cline_dir, _providers_two_entries(_PAST, _FUTURE))
    monkeypatch.setattr(
        cs,
        "_http_post",
        lambda url, payload: {
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh",
        },
    )
    # exp not decoded from the fake token → fallback +1h; just check persistence
    got = cs._refresh_and_store("REF-P")
    assert got == "workos:fresh-access"
    data = json.loads((cline_dir / "settings" / "providers.json").read_text())
    pp = data["providers"]["cline-pass"]["settings"]["auth"]
    assert pp["accessToken"] == "workos:fresh-access"
    assert pp["refreshToken"] == "fresh-refresh"
    assert pp["expiresAt"] > _NOW_MS
    # The cline entry used a DIFFERENT refresh token → untouched.
    cp = data["providers"]["cline"]["settings"]["auth"]
    assert cp["accessToken"] == "workos:old-access-2"
    assert cp["refreshToken"] == "REF-C"


def test_refresh_and_store_keeps_prefix_when_present(cline_dir, monkeypatch) -> None:
    _write_providers(cline_dir, _providers_two_entries(_PAST, _PAST))
    monkeypatch.setattr(
        cs,
        "_http_post",
        lambda url, payload: {"access_token": "workos:prefixed", "refresh_token": "r2"},
    )
    assert cs._refresh_and_store("REF-P") == "workos:prefixed"  # not double-prefixed


def test_refresh_and_store_keeps_old_refresh_when_not_rotated(cline_dir, monkeypatch) -> None:
    _write_providers(cline_dir, _providers_two_entries(_PAST, _PAST))
    monkeypatch.setattr(cs, "_http_post", lambda url, payload: {"access_token": "a"})
    cs._refresh_and_store("REF-P")
    data = json.loads((cline_dir / "settings" / "providers.json").read_text())
    assert data["providers"]["cline-pass"]["settings"]["auth"]["refreshToken"] == "REF-P"


def test_refresh_and_store_uses_exp_claim(cline_dir, monkeypatch) -> None:
    _write_providers(cline_dir, _providers_two_entries(_PAST, _PAST))
    exp = int(time.time()) + 7200
    monkeypatch.setattr(
        cs,
        "_http_post",
        lambda url, payload: {"access_token": "workos:" + _jwt({"exp": exp})},
    )
    cs._refresh_and_store("REF-P")
    data = json.loads((cline_dir / "settings" / "providers.json").read_text())
    assert data["providers"]["cline-pass"]["settings"]["auth"]["expiresAt"] == exp * 1000


def test_refresh_and_store_no_access_token_raises(cline_dir, monkeypatch) -> None:
    _write_providers(cline_dir, _providers_two_entries(_PAST, _PAST))
    monkeypatch.setattr(cs, "_http_post", lambda url, payload: {})
    with pytest.raises(RuntimeError, match="no access_token"):
        cs._refresh_and_store("REF-P")


def test_refresh_and_store_updates_both_when_shared_refresh(cline_dir, monkeypatch) -> None:
    _write_providers(
        cline_dir,
        {
            "cline-pass": _entry("workos:a", _PAST, "SHARED"),
            "cline": _entry("workos:b", _PAST, "SHARED"),
        },
    )
    monkeypatch.setattr(cs, "_http_post", lambda url, payload: {"access_token": "workos:new"})
    cs._refresh_and_store("SHARED")
    data = json.loads((cline_dir / "settings" / "providers.json").read_text())
    for name in ("cline-pass", "cline"):
        assert data["providers"][name]["settings"]["auth"]["accessToken"] == "workos:new"


def test_atomic_write_is_atomic(cline_dir) -> None:
    _write_providers(cline_dir, {"cline-pass": _entry("t", _FUTURE, "r")})
    cs._atomic_write({"hello": 1})
    p = cline_dir / "settings" / "providers.json"
    assert json.loads(p.read_text()) == {"hello": 1}
    assert not (cline_dir / "settings" / "providers.json.tmp").exists()


# ── fresh_token ladder ───────────────────────────────────────────────


def test_fresh_token_returns_valid_stored(cline_dir, monkeypatch) -> None:
    _write_providers(cline_dir, {"cline-pass": _entry("good", _FUTURE, "ref")})
    called = []
    monkeypatch.setattr(cs, "_http_post", lambda *a, **k: called.append(1) or {})
    assert cs.fresh_token() == "good"
    assert called == []  # no network when a valid token exists


def test_fresh_token_refreshes_expired(cline_dir, monkeypatch) -> None:
    _write_providers(cline_dir, {"cline-pass": _entry("old", _PAST, "ref")})
    monkeypatch.setattr(cs, "_http_post", lambda url, payload: {"access_token": "workos:newtok"})
    assert cs.fresh_token() == "workos:newtok"


def test_fresh_token_expired_no_refresh_raises(cline_dir) -> None:
    _write_providers(cline_dir, {"cline-pass": _entry("old", _PAST, "")})
    with pytest.raises(RuntimeError, match="run `cline`"):
        cs.fresh_token()


def test_fresh_token_no_session_raises(cline_dir) -> None:
    _write_providers(cline_dir, {})
    with pytest.raises(RuntimeError, match="no Cline session token found"):
        cs.fresh_token()


def test_fresh_token_refresh_failure_uses_concurrent_new_token(cline_dir, monkeypatch) -> None:
    _write_providers(cline_dir, {"cline-pass": _entry("old", _PAST, "ref")})

    def _post(url, payload):
        # Simulate: CLI rotated the session mid-flight; our read now sees
        # a fresh token the CLI wrote.
        _write_providers(cline_dir, {"cline-pass": _entry("cli-fresh", _FUTURE, "ref2")})
        raise RuntimeError("token revoked")

    monkeypatch.setattr(cs, "_http_post", _post)
    assert cs.fresh_token() == "cli-fresh"


def test_fresh_token_refresh_failure_tries_newer_refresh(cline_dir, monkeypatch) -> None:
    _write_providers(cline_dir, {"cline-pass": _entry("old", _PAST, "ref-old")})

    def _post(url, payload):
        if payload["refresh_token"] == "ref-old":
            _write_providers(cline_dir, {"cline-pass": _entry("old", _PAST, "ref-new")})
            raise RuntimeError("rotated")
        return {"access_token": "workos:via-newer"}

    monkeypatch.setattr(cs, "_http_post", _post)
    assert cs.fresh_token() == "workos:via-newer"


def test_fresh_token_total_failure_raises(cline_dir, monkeypatch) -> None:
    _write_providers(cline_dir, {"cline-pass": _entry("old", _PAST, "ref")})
    monkeypatch.setattr(
        cs, "_http_post", lambda url, payload: (_ for _ in ()).throw(RuntimeError("down"))
    )
    with pytest.raises(RuntimeError, match="auto-refresh failed"):
        cs.fresh_token()


def test_http_post_error_raises(monkeypatch) -> None:
    import httpx

    class _R:
        status_code = 401
        text = "bad"

        def json(self):
            return {}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _R())
    with pytest.raises(RuntimeError, match="WorkOS HTTP 401"):
        cs._http_post("https://x", {})
