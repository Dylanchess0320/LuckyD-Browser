"""B6: cline_bridge /v1/models serves the live upstream catalog.

The endpoint returned only the hardcoded KNOWN_MODELS list, which rots
whenever the gateway adds/retires ids. It now proxies the upstream
/v1/models catalog and falls back to KNOWN_MODELS when unreachable —
same OpenAI list shape either way.
"""

from __future__ import annotations

from types import SimpleNamespace


def _authed_client(monkeypatch):
    monkeypatch.setenv("CLINE_BRIDGE_TOKEN", "bridge-test-secret")
    monkeypatch.delenv("CODING_AGENT_API_KEY", raising=False)
    from fastapi.testclient import TestClient

    import cline_bridge

    return TestClient(cline_bridge.app)


def _models_response(ids):
    return SimpleNamespace(
        status_code=200,
        raise_for_status=lambda: None,
        json=lambda: {"data": [{"id": i} for i in ids]},
    )


class TestLiveModels:
    def test_live_catalog_served(self, monkeypatch):
        import httpx

        import cline_bridge

        monkeypatch.setattr(cline_bridge, "_upstream_token", lambda: "test-token")
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _models_response(["live-a", "live-b"]))
        client = _authed_client(monkeypatch)
        r = client.get("/v1/models", headers={"Authorization": "Bearer bridge-test-secret"})
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["data"]]
        assert ids == ["live-a", "live-b"]

    def test_falls_back_to_known_models(self, monkeypatch):
        import httpx

        import cline_bridge

        def _boom(*args, **kwargs):
            raise httpx.ConnectError("down")

        monkeypatch.setattr(cline_bridge, "_upstream_token", lambda: "test-token")
        monkeypatch.setattr(httpx, "get", _boom)
        client = _authed_client(monkeypatch)
        r = client.get("/v1/models", headers={"Authorization": "Bearer bridge-test-secret"})
        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "list"
        assert [m["id"] for m in body["data"]] == cline_bridge.KNOWN_MODELS

    def test_blank_ids_dropped(self, monkeypatch):
        import httpx

        import cline_bridge

        monkeypatch.setattr(cline_bridge, "_upstream_token", lambda: "test-token")
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _models_response(["ok", "", None]))
        assert cline_bridge._live_model_ids() == ["ok"]
