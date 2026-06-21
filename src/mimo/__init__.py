"""mimo — Xiaomi account registration & MiMo automation."""

from .crypto import (
    AES_IV,
    CAPTCHA_RSA_PEM,
    EUI_RSA_PEM,
    KEY_CHARS,
    aes_encrypt,
    encrypt_captcha_payload,
    encrypt_form_fields,
    random_aes_key,
    rsa_encrypt_pkcs1,
)
from .email_gen import (
    catch_all,
    generate_emails,
    gmail_plus_alias,
    iter_catch_all,
    iter_from_file,
    iter_gmail_plus_aliases,
)

__all__ = [
    "AES_IV",
    "CAPTCHA_RSA_PEM",
    "EUI_RSA_PEM",
    "KEY_CHARS",
    "aes_encrypt",
    "catch_all",
    "encrypt_captcha_payload",
    "encrypt_form_fields",
    "generate_emails",
    "gmail_plus_alias",
    "iter_catch_all",
    "iter_from_file",
    "iter_gmail_plus_aliases",
    "random_aes_key",
    "rsa_encrypt_pkcs1",
]

__version__ = "0.1.0"