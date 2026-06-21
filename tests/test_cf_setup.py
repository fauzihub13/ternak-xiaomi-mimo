"""Test Cloudflare API setup — semua mocked."""

from unittest.mock import MagicMock, patch

import pytest

from mimo import cf_setup


# ── _headers ────────────────────────────────────────────────────────────────
def test_headers_bearer():
    h = cf_setup._headers("my_token")
    assert h["Authorization"] == "Bearer my_token"
    assert h["Content-Type"] == "application/json"


# ── _request (mocked) ───────────────────────────────────────────────────────
def test_request_get_success(monkeypatch):
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"success": True, "result": [{"id": "1"}]}
    monkeypatch.setattr("mimo.cf_setup.requests.request",
                       lambda *a, **kw: fake_resp)
    data = cf_setup._request("GET", "https://x", "tok")
    assert data["success"] is True


def test_request_non_json(monkeypatch):
    fake_resp = MagicMock()
    fake_resp.json.side_effect = ValueError("no json")
    fake_resp.status_code = 500
    monkeypatch.setattr("mimo.cf_setup.requests.request",
                       lambda *a, **kw: fake_resp)
    data = cf_setup._request("GET", "https://x", "tok")
    assert data["success"] is False
    assert "non-json" in data["errors"][0]["message"]


# ── list_destinations ───────────────────────────────────────────────────────
def test_list_destinations_success(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup._request",
                       lambda *a, **kw: {"success": True,
                                          "result": [{"id": "d1", "email": "a@x.com"}]})
    dests = cf_setup.list_destinations("acc", "tok")
    assert len(dests) == 1
    assert dests[0]["email"] == "a@x.com"


def test_list_destinations_api_error(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup._request",
                       lambda *a, **kw: {"success": False, "errors": ["x"]})
    with pytest.raises(RuntimeError):
        cf_setup.list_destinations("acc", "tok")


# ── create_destination ──────────────────────────────────────────────────────
def test_create_destination(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup._request",
                       lambda *a, **kw: {"success": True,
                                          "result": {"id": "d2", "email": "b@x.com"}})
    dest = cf_setup.create_destination("acc", "tok", "b@x.com")
    assert dest["email"] == "b@x.com"


def test_create_destination_fail(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup._request",
                       lambda *a, **kw: {"success": False, "errors": ["invalid"]})
    with pytest.raises(RuntimeError):
        cf_setup.create_destination("acc", "tok", "x@y.com")


# ── ensure_destination ──────────────────────────────────────────────────────
def test_ensure_destination_existing(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup.list_destinations",
                       lambda *a, **kw: [{"id": "d1", "email": "a@x.com"}])
    dest = cf_setup.ensure_destination("acc", "tok", "a@x.com")
    assert dest["id"] == "d1"


def test_ensure_destination_case_insensitive(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup.list_destinations",
                       lambda *a, **kw: [{"id": "d1", "email": "A@X.COM"}])
    dest = cf_setup.ensure_destination("acc", "tok", "a@x.com")
    assert dest["id"] == "d1"


def test_ensure_destination_creates_new(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup.list_destinations",
                       lambda *a, **kw: [{"id": "d1", "email": "other@x.com"}])
    monkeypatch.setattr("mimo.cf_setup.create_destination",
                       lambda *a, **kw: {"id": "d2", "email": "new@x.com"})
    dest = cf_setup.ensure_destination("acc", "tok", "new@x.com")
    assert dest["id"] == "d2"


# ── list_rules ──────────────────────────────────────────────────────────────
def test_list_rules(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup._request",
                       lambda *a, **kw: {"success": True,
                                          "result": [
                                              {"id": "r1", "matchers": [{"type": "all"}]},
                                              {"id": "r2", "matchers": [{"type": "literal", "field": "to", "value": "x"}]}
                                          ]})
    rules = cf_setup.list_rules("zone", "tok")
    assert len(rules) == 2


# ── create_rule ─────────────────────────────────────────────────────────────
def test_create_rule(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup._request",
                       lambda *a, **kw: {"success": True,
                                          "result": {"id": "r_new",
                                                     "matchers": [{"type": "all"}],
                                                     "actions": [{"type": "forward"}]}})
    rule = cf_setup.create_rule(
        "zone", "tok",
        name="Catch-all",
        matchers=[{"type": "all"}],
        actions=[{"type": "forward", "value": ["a@x.com"]}],
    )
    assert rule["id"] == "r_new"


# ── ensure_catch_all ────────────────────────────────────────────────────────
def test_ensure_catch_all_existing(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup.list_rules",
                       lambda *a, **kw: [{"id": "r1", "matchers": [{"type": "all"}]}])
    rule = cf_setup.ensure_catch_all("zone", "tok", "a@x.com")
    assert rule["id"] == "r1"  # not created


def test_ensure_catch_all_creates_new(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup.list_rules",
                       lambda *a, **kw: [{"id": "r1",
                                          "matchers": [{"type": "literal", "field": "to", "value": "x"}]}])
    monkeypatch.setattr("mimo.cf_setup.create_rule",
                       lambda *a, **kw: {"id": "r_new",
                                          "name": "Catch-all"})
    rule = cf_setup.ensure_catch_all("zone", "tok", "a@x.com")
    assert rule["id"] == "r_new"


# ── delete_rule ─────────────────────────────────────────────────────────────
def test_delete_rule(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup._request",
                       lambda *a, **kw: {"success": True})
    result = cf_setup.delete_rule("zone", "tok", "r1")
    assert result["success"] is True


# ── list_zones ──────────────────────────────────────────────────────────────
def test_list_zones_all(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup._request",
                       lambda *a, **kw: {"success": True,
                                          "result": [
                                              {"id": "z1", "name": "a.com"},
                                              {"id": "z2", "name": "b.com"},
                                          ]})
    zones = cf_setup.list_zones("tok")
    assert len(zones) == 2


def test_list_zones_filter(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup._request",
                       lambda *a, **kw: {"success": True,
                                          "result": [
                                              {"id": "z1", "name": "mimo.kamu.com"},
                                              {"id": "z2", "name": "lain.com"},
                                          ]})
    zones = cf_setup.list_zones("tok", name="mimo")
    assert len(zones) == 1
    assert zones[0]["id"] == "z1"


# ── get_account_id ──────────────────────────────────────────────────────────
def test_get_account_id(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup._request",
                       lambda *a, **kw: {"success": True,
                                          "result": {"accounts": [{"id": "acc_123"}]}})
    acc_id = cf_setup.get_account_id("tok")
    assert acc_id == "acc_123"


def test_get_account_id_empty(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup._request",
                       lambda *a, **kw: {"success": True,
                                          "result": {"accounts": []}})
    with pytest.raises(RuntimeError):
        cf_setup.get_account_id("tok")


# ── setup_catch_all (full flow) ─────────────────────────────────────────────
def test_setup_catch_all_already_exists(monkeypatch, capsys):
    monkeypatch.setattr("mimo.cf_setup.list_destinations",
                       lambda *a, **kw: [{"id": "d1", "email": "a@x.com", "verified": True}])
    monkeypatch.setattr("mimo.cf_setup.list_rules",
                       lambda *a, **kw: [{"id": "r1",
                                          "name": "Existing catch-all",
                                          "matchers": [{"type": "all"}]}])
    result = cf_setup.setup_catch_all("tok", "zone", "acc", "a@x.com")
    captured = capsys.readouterr()
    assert "already exists" in captured.out
    assert result["already_existed"] is True
    assert result["rule"]["id"] == "r1"


def test_setup_catch_all_new(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup.list_destinations",
                       lambda *a, **kw: [{"id": "d1", "email": "a@x.com", "verified": True}])
    monkeypatch.setattr("mimo.cf_setup.list_rules", lambda *a, **kw: [])
    monkeypatch.setattr("mimo.cf_setup.create_rule",
                       lambda *a, **kw: {"id": "r_new", "name": "Catch-all"})
    result = cf_setup.setup_catch_all("tok", "zone", "acc", "a@x.com")
    assert result["rule"]["id"] == "r_new"


def test_setup_catch_all_unverified_destination(monkeypatch, capsys):
    """Kalau destination belum verified, skip catch-all creation."""
    monkeypatch.setattr("mimo.cf_setup.list_destinations",
                       lambda *a, **kw: [{"id": "d1", "email": "a@x.com", "verified": False}])
    monkeypatch.setattr("mimo.cf_setup.create_destination",
                       lambda *a, **kw: {"id": "d1", "email": "a@x.com", "verified": False})
    monkeypatch.setattr("mimo.cf_setup.list_rules", lambda *a, **kw: [])
    result = cf_setup.setup_catch_all("tok", "zone", "acc", "a@x.com")
    captured = capsys.readouterr()
    assert "skip" in captured.out
    assert result["rule"] is None  # not created


def test_setup_catch_all_dry_run(monkeypatch):
    monkeypatch.setattr("mimo.cf_setup.list_destinations", lambda *a, **kw: [])
    monkeypatch.setattr("mimo.cf_setup.create_destination",
                       lambda *a, **kw: {"id": "d1", "email": "a@x.com", "verified": True})
    monkeypatch.setattr("mimo.cf_setup.list_rules", lambda *a, **kw: [])
    monkeypatch.setattr("mimo.cf_setup.create_rule",
                       lambda *a, **kw: {"id": "r_new"})
    result = cf_setup.setup_catch_all("tok", "zone", "acc", "a@x.com",
                                       dry_run=True)
    assert result["destination"]["dry_run"] is True