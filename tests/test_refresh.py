"""Test cookie refresh flow — semua mocked."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mimo import refresh


# ── utcnow_iso ──────────────────────────────────────────────────────────────
def test_utcnow_iso_format():
    iso = refresh.utcnow_iso()
    assert iso.endswith("Z")
    assert "T" in iso
    assert len(iso) == 20  # YYYY-MM-DDTHH:MM:SSZ


# ── load_jsonl / save_jsonl ─────────────────────────────────────────────────
def test_load_jsonl_basic(tmp_path: Path):
    f = tmp_path / "acc.jsonl"
    f.write_text('{"a":1}\n{"a":2}\n\n{"a":3}\n')
    result = refresh.load_jsonl(f)
    assert result == [{"a": 1}, {"a": 2}, {"a": 3}]


def test_load_jsonl_skip_invalid(tmp_path: Path, capsys):
    f = tmp_path / "acc.jsonl"
    f.write_text('{"a":1}\nnot json\n{"a":2}\n')
    result = refresh.load_jsonl(f)
    assert result == [{"a": 1}, {"a": 2}]
    captured = capsys.readouterr()
    assert "not valid JSON" in captured.err


def test_load_jsonl_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        refresh.load_jsonl(tmp_path / "missing.jsonl")


def test_save_jsonl(tmp_path: Path):
    f = tmp_path / "out.jsonl"
    accounts = [{"a": 1}, {"a": 2}, {"a": 3}]
    refresh.save_jsonl(accounts, f)
    content = f.read_text()
    assert json.loads(content.splitlines()[0]) == {"a": 1}
    assert content.endswith("\n")
    assert len(content.splitlines()) == 3


# ── validate_cookies (mocked) ───────────────────────────────────────────────
def test_validate_cookies_success(monkeypatch):
    """Valid cookies → MiMo returns 200 + code=0."""
    fake_session = MagicMock()
    fake_session.cookies.set = MagicMock()  # allow .set() call
    monkeypatch.setattr("mimo.refresh.cffi_requests.Session", lambda **kw: fake_session)

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"code": 0, "data": {"userId": "u1", "email": "x@y.com"}}
    fake_session.get.return_value = resp

    valid, profile = refresh.validate_cookies({"passToken": "pt", "serviceToken": "st"})
    assert valid is True
    assert profile["userId"] == "u1"


def test_validate_cookies_invalid_response(monkeypatch):
    fake_session = MagicMock()
    fake_session.cookies.set = MagicMock()
    monkeypatch.setattr("mimo.refresh.cffi_requests.Session", lambda **kw: fake_session)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"code": 401, "message": "unauthorized"}
    fake_session.get.return_value = resp

    valid, profile = refresh.validate_cookies({"passToken": "expired"})
    assert valid is False
    assert "code=401" in profile["error"]


def test_validate_cookies_http_error(monkeypatch):
    fake_session = MagicMock()
    fake_session.cookies.set = MagicMock()
    monkeypatch.setattr("mimo.refresh.cffi_requests.Session", lambda **kw: fake_session)
    resp = MagicMock()
    resp.status_code = 503
    resp.text = "service unavailable"
    fake_session.get.return_value = resp

    valid, profile = refresh.validate_cookies({"passToken": "x"})
    assert valid is False
    assert "http 503" in profile["error"]


def test_validate_cookies_empty():
    valid, profile = refresh.validate_cookies({})
    assert valid is False
    assert profile == {}


def test_validate_cookies_network_exception(monkeypatch):
    fake_session = MagicMock()
    fake_session.headers = {}
    monkeypatch.setattr("mimo.refresh.cffi_requests.Session", lambda **kw: fake_session)
    fake_session.get.side_effect = Exception("connection reset")

    valid, profile = refresh.validate_cookies({"passToken": "x"})
    assert valid is False
    assert "connection reset" in profile["error"]


# ── refresh_account (mocked) ────────────────────────────────────────────────
def test_refresh_account_success(monkeypatch):
    monkeypatch.setenv("CAPSOLVER_API_KEY", "test")
    monkeypatch.setattr("mimo.refresh.login_xiaomi", lambda *a, **kw: {
        "email": "u@e.com", "cookies": {"passToken": "new_pt", "serviceToken": "new_st"},
        "passToken": "new_pt", "serviceToken": "new_st",
        "userId": "u1", "cUserId": "c1", "location": "/",
    })
    monkeypatch.setattr("mimo.refresh.validate_cookies",
                       lambda cookies: (True, {"userId": "u1"}))

    account = {"email": "u@e.com", "password": "pw", "status": "success",
               "cookies": {"passToken": "old_pt"}}
    result = refresh.refresh_account(account, validate=True)
    assert result["status"] == "success"
    assert result["cookies"]["passToken"] == "new_pt"
    assert result["validated"] is True
    assert "refreshed_at" in result


def test_refresh_account_login_fails(monkeypatch):
    monkeypatch.setenv("CAPSOLVER_API_KEY", "test")
    monkeypatch.setattr("mimo.refresh.login_xiaomi", lambda *a, **kw: None)

    account = {"email": "u@e.com", "password": "wrong", "status": "success"}
    result = refresh.refresh_account(account, validate=True)
    assert result["status"] == "failed"
    assert "login failed" in result["error"]
    assert "refresh_attempted_at" in result


def test_refresh_account_missing_credentials():
    result = refresh.refresh_account({"email": "u@e.com", "status": "success"})
    assert result["status"] == "failed"
    assert "missing email/password" in result["error"]


def test_refresh_account_dry_run():
    account = {"email": "u@e.com", "password": "pw", "status": "success"}
    result = refresh.refresh_account(account, dry_run=True)
    assert result["status"] == "dry_run"
    # Original account data preserved + timestamp
    assert result["email"] == "u@e.com"
    assert "refresh_attempted_at" in result


def test_refresh_account_unvalidated(monkeypatch):
    """Login OK tapi validate MiMo gagal → status=unvalidated (cookies masih OK)."""
    monkeypatch.setenv("CAPSOLVER_API_KEY", "test")
    monkeypatch.setattr("mimo.refresh.login_xiaomi", lambda *a, **kw: {
        "email": "u@e.com", "cookies": {"passToken": "new"},
        "passToken": "new", "serviceToken": "new",
        "userId": "u1", "cUserId": "c1", "location": "/",
    })
    monkeypatch.setattr("mimo.refresh.validate_cookies",
                       lambda cookies: (False, {"error": "code=87001"}))

    account = {"email": "u@e.com", "password": "pw", "status": "success"}
    result = refresh.refresh_account(account, validate=True)
    assert result["status"] == "unvalidated"
    assert result["validated"] is False
    assert result["validation_error"] == "code=87001"


def test_refresh_account_skip_validation(monkeypatch):
    """--no-validate mode: tidak call validate_cookies."""
    monkeypatch.setenv("CAPSOLVER_API_KEY", "test")
    monkeypatch.setattr("mimo.refresh.login_xiaomi", lambda *a, **kw: {
        "email": "u@e.com", "cookies": {"passToken": "new"},
        "passToken": "new", "serviceToken": "new",
        "userId": "u1", "cUserId": "c1", "location": "/",
    })
    call_count = [0]
    def fake_validate(c):
        call_count[0] += 1
        return True, {}
    monkeypatch.setattr("mimo.refresh.validate_cookies", fake_validate)

    account = {"email": "u@e.com", "password": "pw", "status": "success"}
    result = refresh.refresh_account(account, validate=False)
    assert result["status"] == "success"
    assert call_count[0] == 0  # not called
    assert "validated" not in result