"""Crypto helpers untuk Xiaomi account registration.

Berisi:
  - AES-128-CBC + PKCS7 encrypt (IV tetap: 0102030405060708)
  - RSA-PKCS1v15 encrypt (untuk `s` captcha dan `eui` header)
  - Wrappers untuk Node.js encrypt.cjs (EUI generation)
  - Captcha payload encryption (`s`/`d`)
"""

import base64
import json
import os
import random
import subprocess
from pathlib import Path
from typing import Optional

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad

# ── Konstanta ──────────────────────────────────────────────────────────────
AES_IV    = b"0102030405060708"
KEY_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*"

# Captcha RSA public key (2048-bit) — dari m.js bundle
CAPTCHA_RSA_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArxfNLkuAQ/BYHzkzVwtu
g+0abmYRBVCEScSzGxJIOsfxVzcuqaKO87H2o2wBcacD3bRHhMjTkhSEqxPjQ/FE
XuJ1cdbmr3+b3EQR6wf/cYcMx2468/QyVoQ7BADLSPecQhtgGOllkC+cLYN6Md34
Uii6U+VJf0p0q/saxUTZvhR2ka9fqJ4+6C6cOghIecjMYQNHIaNW+eSKunfFsXVU
+QfMD0q2EM9wo20aLnos24yDzRjh9HJc6xfr37jRlv1/boG/EABMG9FnTm35xWrV
R0nw3cpYF7GZg13QicS/ZwEsSd4HyboAruMxJBPvK3Jdr4ZS23bpN0cavWOJsBqZ
VwIDAQAB
-----END PUBLIC KEY-----"""

# EUI RSA public key (1024-bit) — dari encrypt.cjs (frontend Xiaomi)
EUI_RSA_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCYEVrK/4Mahiv0pUJgTybx4J9P
5dUT/Y0PuwMbk+gMU+jrZnBiXGv6/hCH1avIhoBcE535F8nJQQN3UavZdFkYidso
XuEnat3+eVTp3FslyhRwIBDF09v4vDhRtxFOT+R7uH7h/mzmyA2/+lfIMWGIrffX
prYizbV76+YQKhoqFQIDAQAB
-----END PUBLIC KEY-----"""

ENCRYPT_CJS = Path(__file__).parent / "crypto" / "encrypt.cjs"


# ── AES ────────────────────────────────────────────────────────────────────
def random_aes_key(length: int = 16) -> str:
    """Generate random AES key dari KEY_CHARS charset."""
    return "".join(random.choices(KEY_CHARS, k=length))


def aes_encrypt(plaintext: str, aes_key: str) -> str:
    """AES-128-CBC + PKCS7. Returns base64 string."""
    cipher = AES.new(aes_key.encode("utf-8"), AES.MODE_CBC, AES_IV)
    ct = cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))
    return base64.b64encode(ct).decode("utf-8")


# ── RSA ────────────────────────────────────────────────────────────────────
def rsa_encrypt_pkcs1(b64_data: str, pem: str) -> str:
    """RSA PKCS1v15 encrypt base64-encoded string. Returns base64."""
    key = RSA.import_key(pem)
    cipher = PKCS1_v1_5.new(key)
    ct = cipher.encrypt(b64_data.encode("utf-8"))
    return base64.b64encode(ct).decode("utf-8")


# ── Captcha payload (s, d) ──────────────────────────────────────────────────
def encrypt_captcha_payload(payload: dict) -> tuple[str, str]:
    """Encrypt browser fingerprint untuk captcha/v2/data.
    Returns (s, d):
      s = RSA(captcha_key, base64(aes_key))
      d = AES-128-CBC(JSON payload, aes_key, iv=0102030405060708)
    """
    aes_key = random_aes_key()
    payload_json = json.dumps(payload, separators=(",", ":"))
    d = aes_encrypt(payload_json, aes_key)
    s = rsa_encrypt_pkcs1(base64.b64encode(aes_key.encode()).decode(), CAPTCHA_RSA_PEM)
    return s, d


# ── EUI (email + password) ──────────────────────────────────────────────────
def encrypt_form_fields_native(fields: dict) -> dict:
    """Pure-Python implementation (TANPA Node.js) dari scripts/encrypt.cjs.

    Returns:
        {
            "EUI": "<rsa(base64(aesKey))>.<base64(field_names_csv)>",
            "encryptedParams": {"email": "...", "password": "..."},
        }
    """
    if not fields:
        raise ValueError("fields kosong")

    # 1. Generate random 16-char AES key dari KEY_CHARS
    aes_key = random_aes_key()

    # 2. Encrypt tiap field
    encrypted_params = {
        name: aes_encrypt(value, aes_key) for name, value in fields.items()
    }

    # 3. RSA-encrypt base64(aesKey) pakai EUI RSA key (1024-bit)
    aes_key_b64 = base64.b64encode(aes_key.encode()).decode()
    rsa_encrypted = rsa_encrypt_pkcs1(aes_key_b64, EUI_RSA_PEM)

    # 4. EUI = rsaEncrypted + "." + base64(field_names_csv)
    field_names_csv = ",".join(fields.keys())
    field_names_b64 = base64.b64encode(field_names_csv.encode()).decode()
    eui = f"{rsa_encrypted}.{field_names_b64}"

    return {"EUI": eui, "encryptedParams": encrypted_params}


def encrypt_form_fields_via_node(fields: dict) -> dict:
    """Fallback ke Node.js encrypt.cjs (identical output, lebih reliable)."""
    result = subprocess.run(
        ["node", str(ENCRYPT_CJS), json.dumps(fields)],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"encrypt.cjs failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def encrypt_form_fields(fields: dict, prefer_node: bool = True) -> dict:
    """Encrypt form fields. Default prefer Node.js (identical ke frontend Xiaomi)."""
    if prefer_node and ENCRYPT_CJS.exists():
        try:
            return encrypt_form_fields_via_node(fields)
        except (FileNotFoundError, RuntimeError):
            pass  # node tidak ada, fallback ke pure-Python
    return encrypt_form_fields_native(fields)