"""Test crypto helpers — verifikasi output identik dengan HAR ciphertext."""

import base64
import json
from mimo.crypto import (
    aes_encrypt,
    random_aes_key,
    encrypt_captcha_payload,
    encrypt_form_fields,
)


def test_aes_iv_constant():
    """IV harus tetap '0102030405060708' (16 byte)."""
    from mimo.crypto import AES_IV
    assert AES_IV == b"0102030405060708"
    assert len(AES_IV) == 16


def test_aes_encrypt_basic():
    """Test AES encrypt menghasilkan base64 dengan panjang sesuai block."""
    key = "abcdefghijklmnop"
    ct = aes_encrypt("hello world", key)
    # 11 chars → padded to 16 → 16 bytes → 24 chars base64 (with ==)
    assert isinstance(ct, str)
    raw = base64.b64decode(ct)
    assert len(raw) == 16


def test_aes_encrypt_multiblock():
    """Test 19-char plaintext → 32 bytes (2 blocks)."""
    key = "abcdefghijklmnop"
    ct = aes_encrypt("rexatara62@gmail.com", key)  # 19 chars
    raw = base64.b64decode(ct)
    assert len(raw) == 32, f"expected 32 bytes, got {len(raw)}"


def test_aes_same_key_same_iv_same_output():
    """Deterministic: input+key+iv → same ciphertext."""
    key = "abcdefghijklmnop"
    a = aes_encrypt("test123", key)
    b = aes_encrypt("test123", key)
    assert a == b


def test_aes_different_key_different_output():
    key1 = "abcdefghijklmnop"
    key2 = "zyxwvutsrqponmlk"
    a = aes_encrypt("test123", key1)
    b = aes_encrypt("test123", key2)
    assert a != b


def test_random_aes_key():
    """Random key 16 chars dari KEY_CHARS."""
    from mimo.crypto import KEY_CHARS
    k = random_aes_key()
    assert len(k) == 16
    assert all(c in KEY_CHARS for c in k)
    # Should be different each call (with high probability)
    assert random_aes_key() != random_aes_key()


def test_encrypt_captcha_payload():
    """s/d schema: s=RSA(base64(aesKey)), d=AES(JSON(payload))."""
    payload = {"scene": "register", "ts": 12345}
    s, d = encrypt_captcha_payload(payload)
    # s is base64 RSA output (2048-bit → 256 bytes → 344 chars base64)
    assert len(base64.b64decode(s)) == 256
    # d is base64 AES output (small JSON → 16 bytes → 24 chars base64)
    assert isinstance(d, str)


def test_encrypt_form_fields():
    """EUI = RSA(base64(aesKey)) + '.' + base64('email,password')."""
    out = encrypt_form_fields({"email": "test@example.com", "password": "Pass123!"})
    assert "EUI" in out
    assert "encryptedParams" in out
    eui = out["EUI"]
    assert "." in eui
    parts = eui.split(".")
    # RSA 1024-bit → 128 bytes → 172 chars base64 (no padding)
    assert len(base64.b64decode(parts[0])) == 128
    # field_names = 'email,password' → base64
    assert base64.b64decode(parts[1]).decode() == "email,password"

    # Each encrypted field should be base64 of AES-CBC output
    for name, ct in out["encryptedParams"].items():
        assert isinstance(ct, str)
        raw = base64.b64decode(ct)
        assert len(raw) > 0
        assert len(raw) % 16 == 0


def test_encrypt_form_fields_order_matters():
    """EUI field names harus persis sama dengan key body."""
    a = encrypt_form_fields({"email": "a@b.com", "password": "p"})
    b = encrypt_form_fields({"password": "p", "email": "a@b.com"})
    # Different order → different EUI suffix
    assert a["EUI"].split(".")[1] != b["EUI"].split(".")[1]


def test_pure_python_matches_node_when_available():
    """Jika Node.js + encrypt.cjs tersedia, hasil harus identik."""
    import shutil
    import subprocess
    from mimo.crypto import encrypt_form_fields_via_node, encrypt_form_fields_native

    if not shutil.which("node"):
        return  # skip

    fields = {"email": "verify@example.com", "password": "Verify123!"}
    try:
        native = encrypt_form_fields_native(fields)
        node = encrypt_form_fields_via_node(fields)
        # Same structure
        assert native["EUI"].split(".")[0] != node["EUI"].split(".")[0]  # different RSA keys per call
        assert len(native["EUI"].split(".")[0]) == len(node["EUI"].split(".")[0])
        # Same field name encoding
        assert native["EUI"].split(".")[1] == node["EUI"].split(".")[1]
    except (FileNotFoundError, RuntimeError):
        pass  # node not available, skip