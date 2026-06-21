"""Test email generator + batch orchestrator (mocked)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mimo.email_gen import (
    catch_all,
    generate_emails,
    gmail_plus_alias,
    iter_catch_all,
    iter_from_file,
    iter_gmail_plus_aliases,
    random_tag,
)


# ── random_tag ──────────────────────────────────────────────────────────────
def test_random_tag_length():
    assert len(random_tag()) == 8
    assert len(random_tag(16)) == 16


def test_random_tag_unique():
    """High probability of uniqueness for 8 chars from 16-char alphabet."""
    tags = {random_tag() for _ in range(100)}
    assert len(tags) == 100


# ── gmail_plus_alias ────────────────────────────────────────────────────────
def test_gmail_plus_alias_basic():
    assert gmail_plus_alias("foo@gmail.com", "abc") == "foo+abc@gmail.com"
    assert gmail_plus_alias("user.name@gmail.com", "x") == "user.name+x@gmail.com"


def test_gmail_plus_alias_random_tag():
    e = gmail_plus_alias("test@gmail.com")
    assert e.startswith("test+")
    assert e.endswith("@gmail.com")
    local = e.split("@")[0]
    tag = local.split("+")[1]
    assert len(tag) == 8


def test_gmail_plus_alias_invalid():
    with pytest.raises(ValueError):
        gmail_plus_alias("not-an-email")


def test_iter_gmail_plus_aliases_unique():
    emails = list(iter_gmail_plus_aliases("base@gmail.com", 10))
    assert len(emails) == 10
    assert len(set(emails)) == 10  # all unique


# ── catch_all ───────────────────────────────────────────────────────────────
def test_catch_all_random():
    e = catch_all("mimo.example.com")
    local, domain = e.split("@")
    assert domain == "mimo.example.com"
    assert len(local) == 10
    assert local.isalnum()


def test_catch_all_with_prefix():
    e = catch_all("mimo.example.com", prefix="acc")
    local, domain = e.split("@")
    assert local.startswith("acc-")
    assert len(local.split("-")[1]) == 10


def test_iter_catch_all_unique():
    emails = list(iter_catch_all("test.com", 50))
    assert len(emails) == 50
    assert len(set(emails)) == 50


# ── from_file ───────────────────────────────────────────────────────────────
def test_iter_from_file_one_per_line(tmp_path: Path):
    f = tmp_path / "emails.txt"
    f.write_text("a@x.com\nb@x.com\nc@x.com\n")
    assert list(iter_from_file(f)) == ["a@x.com", "b@x.com", "c@x.com"]


def test_iter_from_file_csv_with_header(tmp_path: Path):
    f = tmp_path / "emails.csv"
    f.write_text("email\na@x.com\nb@x.com\n")
    assert list(iter_from_file(f)) == ["a@x.com", "b@x.com"]


def test_iter_from_file_comma_separated(tmp_path: Path):
    f = tmp_path / "emails.txt"
    f.write_text("a@x.com,b@x.com;c@x.com")
    assert list(iter_from_file(f)) == ["a@x.com", "b@x.com", "c@x.com"]


def test_iter_from_file_filters_invalid(tmp_path: Path):
    f = tmp_path / "emails.txt"
    f.write_text("valid@x.com\nnot-an-email\nfoo@bar.com\n")
    assert list(iter_from_file(f)) == ["valid@x.com", "foo@bar.com"]


def test_iter_from_file_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        list(iter_from_file(tmp_path / "missing.txt"))


# ── generate_emails (unified) ────────────────────────────────────────────────
def test_generate_emails_catch_all():
    emails = generate_emails(5, "catch_all", domain="mimo.x.com")
    assert len(emails) == 5
    assert all("@mimo.x.com" in e for e in emails)


def test_generate_emails_gmail_plus():
    emails = generate_emails(3, "gmail_plus", base="user@gmail.com")
    assert len(emails) == 3
    assert all(e.startswith("user+") for e in emails)


def test_generate_emails_from_file(tmp_path: Path):
    f = tmp_path / "emails.txt"
    f.write_text("a@x.com\nb@x.com\nc@x.com\nd@x.com\n")
    emails = generate_emails(2, "from_file", file=str(f))
    assert len(emails) == 2
    assert emails == ["a@x.com", "b@x.com"]


def test_generate_emails_missing_args():
    with pytest.raises(ValueError):
        generate_emails(5, "catch_all")  # no domain
    with pytest.raises(ValueError):
        generate_emails(5, "gmail_plus")  # no base
    with pytest.raises(ValueError):
        generate_emails(5, "from_file")  # no file
    with pytest.raises(ValueError):
        generate_emails(5, "unknown_strategy")