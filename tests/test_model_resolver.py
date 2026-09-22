import pathlib

import model_resolver


def test_invalidate_cache_handles_oserror(monkeypatch):
    """Verify that invalidate_cache ignores OSErrors during unlink."""

    def mock_unlink(*args, **kwargs):
        raise OSError("Mocked OSError")

    # Force CACHE_FILE.exists() to return True, then mock unlink to throw
    monkeypatch.setattr(pathlib.Path, "exists", lambda x: True)
    monkeypatch.setattr(pathlib.Path, "unlink", mock_unlink)

    # Calling invalidate_cache should not raise an exception
    model_resolver.invalidate_cache()


def test_invalidate_cache_normal_operation(monkeypatch, tmp_path):
    """Verify invalidate_cache normally unlinks the file."""
    fake_cache = tmp_path / ".model_cache.json"
    fake_cache.write_text("{}")
    monkeypatch.setattr(model_resolver, "CACHE_FILE", fake_cache)
    model_resolver.invalidate_cache()
    assert not fake_cache.exists()
