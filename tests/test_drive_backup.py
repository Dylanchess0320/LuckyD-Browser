"""Tests for browser/browser_core/drive_backup.py — Google client libs faked, no network."""

from __future__ import annotations

import sys
import types

import pytest

from browser.browser_core import drive_backup
from browser.browser_core.drive_backup import DriveBackupError, backup, ensure_folder, upload_file

# ── fakes ────────────────────────────────────────────────────────────────


class _FakeFilesResource:
    def __init__(self):
        self.created = []
        self._list_result = {"files": []}

    def list(self, **kw):
        outer = self

        class _Req:
            def execute(self):
                return outer._list_result

        return _Req()

    def create(self, *, body=None, media_body=None, fields=None):
        outer = self
        outer.created.append(body)

        class _Req:
            def execute(self):
                if body.get("mimeType") == "application/vnd.google-apps.folder":
                    return {"id": "folder123"}
                return {"id": "file123"}

        return _Req()


class _FakeService:
    def __init__(self):
        self._files = _FakeFilesResource()

    def files(self):
        return self._files


def _install_fakes(monkeypatch):
    """Fake googleapiclient + google_auth_oauthlib + google.auth/oauth2 modules."""
    fake_http = types.ModuleType("googleapiclient.http")

    class _FakeMediaUpload:
        def __init__(self, path, mimetype=None, resumable=False):
            self.path = path

    fake_http.MediaFileUpload = _FakeMediaUpload
    fake_client_mod = types.ModuleType("googleapiclient")
    fake_client_mod.http = fake_http

    fake_oauth_flow = types.ModuleType("google_auth_oauthlib.flow")
    fake_oauthlib = types.ModuleType("google_auth_oauthlib")
    fake_oauthlib.flow = fake_oauth_flow

    for name, mod in {
        "googleapiclient": fake_client_mod,
        "googleapiclient.http": fake_http,
        "google_auth_oauthlib": fake_oauthlib,
        "google_auth_oauthlib.flow": fake_oauth_flow,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)


# ── tests ────────────────────────────────────────────────────────────────


def test_ensure_folder_reuses_existing(monkeypatch):
    _install_fakes(monkeypatch)
    svc = _FakeService()
    svc.files()._list_result = {"files": [{"id": "existing9", "name": "LuckyD-Backup"}]}
    assert ensure_folder(svc, "LuckyD-Backup") == "existing9"
    assert svc.files().created == []


def test_ensure_folder_creates_when_missing(monkeypatch):
    _install_fakes(monkeypatch)
    svc = _FakeService()
    assert ensure_folder(svc, "LuckyD-Backup") == "folder123"
    assert svc.files().created[0]["mimeType"] == "application/vnd.google-apps.folder"


def test_upload_file_rejects_non_file(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    with pytest.raises(DriveBackupError, match="Not a file"):
        upload_file(_FakeService(), "folder123", tmp_path / "nope.txt")


def test_backup_uploads_files_and_dirs(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    (tmp_path / "a.txt").write_text("hello")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("world")
    done = backup([tmp_path / "a.txt", sub], service=_FakeService())
    assert "a.txt" in done
    assert "b.txt" in done  # relative path inside the directory


def test_backup_missing_path_errors(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    with pytest.raises(DriveBackupError, match="Nothing to back up"):
        backup([tmp_path / "ghost"], service=_FakeService())


def test_get_drive_service_without_secrets_errors(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    with pytest.raises(DriveBackupError, match="client secrets"):
        drive_backup.get_drive_service(client_secrets=tmp_path / "nope.json")
