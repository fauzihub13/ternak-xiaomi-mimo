"""Test batch orchestrator dengan mocked register()."""

import json
from pathlib import Path

import pytest

from mimo import batch


# ── _gen_password ────────────────────────────────────────────────────────────
def test_gen_password_default_length():
    pw = batch._gen_password(18)
    assert len(pw) == 18
    assert any(c.isupper() for c in pw)
    assert any(c.islower() for c in pw)
    assert any(c.isdigit() for c in pw)
    assert any(c in "!@#$%^&*" for c in pw)


def test_gen_password_min_length():
    """Min length 4 (1 upper + 1 lower + 1 digit + 1 special)."""
    for _ in range(50):
        pw = batch._gen_password(4)
        assert len(pw) == 4
        assert any(c.isupper() for c in pw)
        assert any(c.islower() for c in pw)
        assert any(c.isdigit() for c in pw)


def test_gen_password_unique():
    pws = {batch._gen_password(20) for _ in range(100)}
    assert len(pws) == 100  # all unique


# ── already_registered ──────────────────────────────────────────────────────
def test_already_registered_empty(tmp_path: Path):
    out = tmp_path / "nope.jsonl"
    assert batch.already_registered(out) == set()


def test_already_registered_parses_jsonl(tmp_path: Path):
    out = tmp_path / "acc.jsonl"
    out.write_text(
        json.dumps({"email": "a@x.com", "status": "success", "cookies": {}}) + "\n"
        + json.dumps({"email": "b@x.com", "status": "failed", "error": "x"}) + "\n"
        + json.dumps({"email": "c@x.com", "status": "success", "cookies": {}}) + "\n"
    )
    done = batch.already_registered(out)
    assert done == {"a@x.com", "c@x.com"}


def test_already_registered_handles_corrupt(tmp_path: Path):
    out = tmp_path / "acc.jsonl"
    out.write_text(
        json.dumps({"email": "a@x.com", "status": "success"}) + "\n"
        + "this is not json\n"
        + json.dumps({"email": "c@x.com", "status": "success"}) + "\n"
    )
    done = batch.already_registered(out)
    assert done == {"a@x.com", "c@x.com"}


# ── build_email_list ────────────────────────────────────────────────────────
def test_build_email_list_explicit(monkeypatch):
    args = type("Args", (), {
        "emails": ["x@y.com", "z@y.com"],
        "count": 5,
        "email_strategy": "catch_all",
        "email_domain": "d.com",
        "email_base": None,
        "email_prefix": None,
        "email_file": None,
    })()
    emails = batch.build_email_list(args, {})
    assert emails == ["x@y.com", "z@y.com"]


def test_build_email_list_catch_all():
    args = type("Args", (), {
        "emails": None,
        "count": 3,
        "email_strategy": "catch_all",
        "email_domain": "d.com",
        "email_base": None,
        "email_prefix": "acc",
        "email_file": None,
    })()
    emails = batch.build_email_list(args, {})
    assert len(emails) == 3
    assert all(e.endswith("@d.com") for e in emails)


def test_build_email_list_no_strategy_raises():
    args = type("Args", (), {
        "emails": None,
        "count": 1,
        "email_strategy": "catch_all",
        "email_domain": None,
        "email_base": None,
        "email_prefix": None,
        "email_file": None,
    })()
    with pytest.raises(ValueError):
        batch.build_email_list(args, {})


# ── register_account (mocked) ───────────────────────────────────────────────
def test_register_account_success(monkeypatch):
    monkeypatch.setenv("CAPSOLVER_API_KEY", "test")
    monkeypatch.setenv("IMAP_USER", "x")
    monkeypatch.setenv("IMAP_PASS", "y")

    def fake_register(email=None, password=None):
        return {"email": email or "x@y.com", "password": password or "p",
                "cookies": {"a": "b"}, "created_at": "2026-01-01T00:00:00Z"}

    monkeypatch.setattr("mimo.batch.register", fake_register)
    result = batch.register_account("x@y.com", "mypw123", {})
    assert result["status"] == "success"
    assert result["email"] == "x@y.com"
    assert result["attempt"] == 1


def test_register_account_failure(monkeypatch):
    from mimo.register import RegisterError
    monkeypatch.setenv("CAPSOLVER_API_KEY", "test")
    monkeypatch.setenv("IMAP_USER", "x")
    monkeypatch.setenv("IMAP_PASS", "y")

    def fake_register(email=None, password=None):
        raise RegisterError("captcha failed")

    monkeypatch.setattr("mimo.batch.register", fake_register)
    result = batch.register_account("x@y.com", "wrongpass", {})
    assert result["status"] == "failed"
    assert "captcha failed" in result["error"]
    assert result["attempt"] == 1


def test_register_account_restores_env(monkeypatch):
    """Pastikan EMAIL/XIAOMI_PASSWORD di-restore setelah call."""
    monkeypatch.setenv("CAPSOLVER_API_KEY", "test")
    monkeypatch.setenv("IMAP_USER", "x")
    monkeypatch.setenv("IMAP_PASS", "y")
    monkeypatch.setenv("EMAIL", "ORIGINAL@x.com")
    monkeypatch.setenv("XIAOMI_PASSWORD", "ORIG_PASS")

    def fake_register(email=None, password=None):
        # Verify args were passed
        assert email == "NEW@y.com"
        assert password == "NEW_PASS"
        return {"email": email, "password": password,
                "cookies": {}, "created_at": ""}

    monkeypatch.setattr("mimo.batch.register", fake_register)
    batch.register_account("NEW@y.com", "NEW_PASS", {})
    # After call, original env values must be intact
    import os
    assert os.environ["EMAIL"] == "ORIGINAL@x.com"
    assert os.environ["XIAOMI_PASSWORD"] == "ORIG_PASS"