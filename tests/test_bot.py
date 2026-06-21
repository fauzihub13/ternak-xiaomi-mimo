"""Test mimo.bot (login + SSO + bind referral + UltraSpeed) — semua mocked."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mimo import bot


# ── md5_hash ────────────────────────────────────────────────────────────────
def test_md5_hash_uppercase():
    """Xiaomi pakai MD5 hash UPPERCASE hex untuk password."""
    assert bot.md5_hash("password") == "5F4DCC3B5AA765D61D8327DEB882CF99"
    assert bot.md5_hash("test") == "098F6BCD4621D373CADE4E832627B4F6"


# ── parse_xiaomi ────────────────────────────────────────────────────────────
def test_parse_xiaomi_strips_prefix():
    text = '&&&START&&&{"code":0,"data":{}}'
    assert bot.parse_xiaomi(text) == {"code": 0, "data": {}}


def test_parse_xiaomi_no_prefix():
    text = '{"code":0}'
    assert bot.parse_xiaomi(text) == {"code": 0}


def test_parse_xiaomi_whitespace():
    text = '   &&&START&&&  {"code":70022}  '
    assert bot.parse_xiaomi(text) == {"code": 70022}


# ── load_account ────────────────────────────────────────────────────────────
def test_load_account_first_success(tmp_path: Path):
    f = tmp_path / "acc.jsonl"
    f.write_text(
        json.dumps({"email": "a@x.com", "status": "failed", "error": "x"}) + "\n"
        + json.dumps({"email": "b@x.com", "status": "success",
                      "password": "p1", "cookies": {}}) + "\n"
        + json.dumps({"email": "c@x.com", "status": "success",
                      "password": "p2", "cookies": {}}) + "\n"
    )
    acc = bot.load_account(str(f), row=0)
    assert acc["email"] == "b@x.com"
    assert acc["password"] == "p1"


def test_load_account_specific_row(tmp_path: Path):
    f = tmp_path / "acc.jsonl"
    f.write_text(
        json.dumps({"email": "a@x.com", "status": "success", "password": "p1"}) + "\n"
        + json.dumps({"email": "b@x.com", "status": "success", "password": "p2"}) + "\n"
    )
    assert bot.load_account(str(f), 1)["email"] == "b@x.com"


def test_load_account_missing_file():
    with pytest.raises(FileNotFoundError):
        bot.load_account("/nonexistent/path.jsonl")


def test_load_account_no_success(tmp_path: Path):
    f = tmp_path / "acc.jsonl"
    f.write_text(json.dumps({"email": "a@x.com", "status": "failed"}) + "\n")
    with pytest.raises(ValueError):
        bot.load_account(str(f))


def test_load_account_row_out_of_range(tmp_path: Path):
    f = tmp_path / "acc.jsonl"
    f.write_text(json.dumps({"email": "a@x.com", "status": "success",
                              "password": "p"}) + "\n")
    with pytest.raises(IndexError):
        bot.load_account(str(f), row=5)


# ── login_xiaomi (mocked) ───────────────────────────────────────────────────
def test_login_xiaomi_success(monkeypatch):
    """Login tanpa captcha → success."""
    monkeypatch.setenv("CAPSOLVER_API_KEY", "test")

    fake_session = MagicMock()
    fake_session.cookies = []  # empty iter
    fake_session.headers = {}
    monkeypatch.setattr("mimo.bot.make_session", lambda: fake_session)
    monkeypatch.setattr("mimo.bot.encrypt_form_fields",
                       lambda f: {"EUI": "x", "encryptedParams": {"email": "enc"}})

    # First do_login call returns success
    resp = MagicMock()
    resp.text = '&&&START&&&{"code":0,"location":"/fe/service/account"}'
    fake_session.post.return_value = resp
    fake_session.get.return_value = MagicMock()  # warm-up

    result = bot.login_xiaomi("user@example.com", "password123")
    assert result is not None
    assert result["email"] == "user@example.com"
    assert result["location"] == "/fe/service/account"


def test_login_xiaomi_wrong_password(monkeypatch):
    """Login dengan password salah → code 70002 → return None."""
    monkeypatch.setenv("CAPSOLVER_API_KEY", "test")
    fake_session = MagicMock()
    fake_session.headers = {}
    monkeypatch.setattr("mimo.bot.make_session", lambda: fake_session)
    monkeypatch.setattr("mimo.bot.encrypt_form_fields",
                       lambda f: {"EUI": "x", "encryptedParams": {"email": "enc"}})
    resp = MagicMock()
    resp.text = '&&&START&&&{"code":70002,"description":"invalid"}'
    fake_session.post.return_value = resp
    fake_session.get.return_value = MagicMock()

    result = bot.login_xiaomi("user@example.com", "wrongpass")
    assert result is None


def test_login_xiaomi_dry_run():
    result = bot.login_xiaomi("u@e.com", "pw", dry_run=True)
    assert result["dry_run"] is True
    assert result["email"] == "u@e.com"


# ── run() orchestration ─────────────────────────────────────────────────────
def test_run_dry_run():
    result = bot.run("u@e.com", "pw", dry_run=True)
    assert result["status"] == "dry_run"
    assert all([result["login"], result["sso"], result["referral"], result["ultraspeed"]])


def test_run_login_failure(monkeypatch):
    monkeypatch.setenv("CAPSOLVER_API_KEY", "test")
    monkeypatch.setattr("mimo.bot.login_xiaomi", lambda *a, **kw: None)
    result = bot.run("u@e.com", "pw")
    assert result["status"] == "failed"
    assert result["error"] == "login failed"
    assert result["login"] is False
    assert result["sso"] is False


def test_run_full_flow(monkeypatch):
    monkeypatch.setenv("CAPSOLVER_API_KEY", "test")

    fake_login = {
        "email": "u@e.com", "cookies": {"passToken": "pt", "serviceToken": "st"},
        "passToken": "pt", "serviceToken": "st", "userId": "u1", "cUserId": "c1",
        "location": "/fe", "session": MagicMock(),
    }
    fake_sso = {"session": MagicMock(), "cookies": {"api-platform_ph": "ph"}}

    monkeypatch.setattr("mimo.bot.login_xiaomi", lambda *a, **kw: fake_login)
    monkeypatch.setattr("mimo.bot.sso_to_mimo", lambda *a, **kw: fake_sso)
    monkeypatch.setattr("mimo.bot.bind_referral", lambda *a, **kw: True)
    monkeypatch.setattr("mimo.bot.apply_ultraspeed", lambda *a, **kw: True)

    result = bot.run("u@e.com", "pw", referral_code="ABC123",
                     name="Test", phone="08123")
    assert result["status"] == "success"
    assert all([result["login"], result["sso"], result["referral"], result["ultraspeed"]])


def test_run_partial_when_ultraspeed_fails(monkeypatch):
    monkeypatch.setenv("CAPSOLVER_API_KEY", "test")
    fake_login = {"email": "u@e.com", "cookies": {}, "passToken": "", "serviceToken": "",
                  "userId": "", "cUserId": "", "location": "", "session": MagicMock()}
    fake_sso = {"session": MagicMock(), "cookies": {}}
    monkeypatch.setattr("mimo.bot.login_xiaomi", lambda *a, **kw: fake_login)
    monkeypatch.setattr("mimo.bot.sso_to_mimo", lambda *a, **kw: fake_sso)
    monkeypatch.setattr("mimo.bot.bind_referral", lambda *a, **kw: True)
    monkeypatch.setattr("mimo.bot.apply_ultraspeed", lambda *a, **kw: False)

    result = bot.run("u@e.com", "pw", referral_code="X")
    assert result["status"] == "partial"
    assert result["login"] and result["sso"] and result["referral"]
    assert not result["ultraspeed"]