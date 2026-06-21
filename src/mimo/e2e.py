"""End-to-end: register akun Xiaomi → SSO MiMo → load profile.

TANPA bind_referral. Single entry point: `python -m mimo.e2e`.

Flow:
  Step 1: Register akun Xiaomi (atau pakai existing cookies)
  Step 2: Login pakai passToken existing (skip captcha)
  Step 3: SSO ke platform.xiaomimimo.com (full redirect chain)
  Step 4: GET /api/v1/userProfile (verify session)
  Step 5: Print user info + status

Output: xiaomi_account.json + status printout
"""

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path

import imaplib
from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

# Load .env SEBELUM baca env vars (module-level)
load_dotenv()


# ═══════════════════════════════════════════════════════════════════════════════
# Konfigurasi
# ═══════════════════════════════════════════════════════════════════════════════

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)
IMPERSONATE = "chrome124"

CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")
CAPSOLVER_CREATE  = "https://api.capsolver.com/createTask"
CAPSOLVER_RESULT  = "https://api.capsolver.com/getTaskResult"

CAPTCHA_SITEKEY  = "6LeBM0ocAAAAAEwYcFUjtxpVbs-0rnbSVXBBXmh4"
CAPTCHA_PARAM_K   = "8027422fb0eb42fbac1b521ec4a7961f"
CAPTCHA_DATA_TPL    = "https://verify.sec.xiaomi.com/captcha/v2/data?k=8027422fb0eb42fbac1b521ec4a7961f&locale=en_US&_t={ts}"
CAPTCHA_VERIFY_TPL  = "https://verify.sec.xiaomi.com/captcha/v2/recaptcha/verify?k=8027422fb0eb42fbac1b521ec4a7961f&locale=en_US&_t={ts}"

REGISTER_URL   = "https://global.account.xiaomi.com/fe/service/register?_locale=en_US&_uRegion=ID"
LOGIN_URL      = "https://account.xiaomi.com/pass/serviceLoginAuth2"
SSO_LOGIN_URL  = "https://account.xiaomi.com/pass/serviceLogin"
LOGIN_PAGE     = "https://account.xiaomi.com/fe/service/login/password"
MIMO_BASE      = "https://platform.xiaomimimo.com"
MIMO_PROFILE   = "https://platform.xiaomimimo.com/api/v1/userProfile"

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASS = os.getenv("IMAP_PASS", "")

USE_PROXY = os.getenv("USE_PROXY", "1") == "1"
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:40000")

AES_IV    = b"0102030405060708"
KEY_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*"

CAPTCHA_RSA_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArxfNLkuAQ/BYHzkzVwtu
g+0abmYRBVCEScSzGxJIOsfxVzcuqaKO87H2o2wBcacD3bRHhMjTkhSEqxPjQ/FE
XuJ1cdbmr3+b3EQR6wf/cYcMx2468/QyVoQ7BADLSPecQhtgGOllkC+cLYN6Md34
Uii6U+VJf0p0q/saxUTZvhR2ka9fqJ4+6C6cOghIecjMYQNHIaNW+eSKunfFsXVU
+QfMD0q2EM9wo20aLnos24yDzRjh9HJc6xfr37jRlv1/boG/EABMG9FnTm35xWrV
R0nw3cpYF7GZg13QicS/ZwEsSd4HyboAruMxJBPvK3Jdr4ZS23bpN0cavWOJsBqZ
VwIDAQAB
-----END PUBLIC KEY-----"""

EUI_RSA_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCYEVrK/4Mahiv0pUJgTybx4J9P
5dUT/Y0PuwMbk+gMU+jrZnBiXGv6/hCH1avIhoBcE535F8nJQQN3UavZdFkYidso
XuEnat3+eVTp3FslyhRwIBDF09v4vDhRtxFOT+R7uH7h/mzmyA2/+lfIMWGIrffX
prYizbV76+YQKhoqFQIDAQAB
-----END PUBLIC KEY-----"""


# ═══════════════════════════════════════════════════════════════════════════════
# Crypto helpers (inlined — same as mimo/crypto.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _random_aes_key(length: int = 16) -> str:
    return "".join(random.choices(KEY_CHARS, k=length))


def _aes_encrypt(plaintext: str, key: str) -> str:
    """AES-128-CBC + PKCS7. Returns base64."""
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    import base64
    cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, AES_IV)
    ct = cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))
    return base64.b64encode(ct).decode("utf-8")


def _rsa_encrypt(b64_data: str, pem: str) -> str:
    """RSA-PKCS1v15. Returns base64."""
    from Crypto.PublicKey import RSA
    from Crypto.Cipher import PKCS1_v1_5
    import base64
    key = RSA.import_key(pem)
    cipher = PKCS1_v1_5.new(key)
    ct = cipher.encrypt(b64_data.encode("utf-8"))
    return base64.b64encode(ct).decode("utf-8")


def _encrypt_captcha_payload(payload: dict) -> tuple[str, str]:
    """Returns (s, d) untuk captcha/v2/data."""
    aes_key = _random_aes_key()
    d = _aes_encrypt(json.dumps(payload, separators=(",", ":")), aes_key)
    s = _rsa_encrypt(__import__("base64").b64encode(aes_key.encode()).decode(), CAPTCHA_RSA_PEM)
    return s, d


def _encrypt_form_fields(fields: dict) -> dict:
    """Returns {EUI, encryptedParams: {field: base64}}."""
    import base64
    aes_key = _random_aes_key()
    encrypted = {k: _aes_encrypt(v, aes_key) for k, v in fields.items()}
    key_b64 = base64.b64encode(aes_key.encode()).decode()
    rsa_ct = _rsa_encrypt(key_b64, EUI_RSA_PEM)
    field_names = ",".join(fields.keys())
    field_b64 = base64.b64encode(field_names.encode()).decode()
    eui = f"{rsa_ct}.{field_b64}"
    return {"EUI": eui, "encryptedParams": encrypted}


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP / Session
# ═══════════════════════════════════════════════════════════════════════════════

def _make_session() -> cffi_requests.Session:
    kwargs = {"impersonate": IMPERSONATE}
    if USE_PROXY and PROXY_URL:
        kwargs["proxies"] = {"https": PROXY_URL, "http": PROXY_URL}
    s = cffi_requests.Session(**kwargs)
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def _parse_xiaomi(text: str) -> dict:
    """Strip &&&START&&& prefix dan parse JSON."""
    clean = text.strip()
    if clean.startswith("&&&START&&&"):
        clean = clean[len("&&&START&&&"):].strip()
    return json.loads(clean)


def _md5(text: str) -> str:
    import hashlib
    return hashlib.md5(text.encode()).hexdigest().upper()


# ═══════════════════════════════════════════════════════════════════════════════
# CapSolver (reCAPTCHA Enterprise)
# ═══════════════════════════════════════════════════════════════════════════════

def _solve_captcha(e_token: str, action: str = "register",
                   website_url: str = REGISTER_URL, timeout: int = 300) -> str | None:
    """Submit reCAPTCHA ke CapSolver, return gRecaptchaResponse token."""
    if not CAPSOLVER_API_KEY:
        raise RuntimeError("CAPSOLVER_API_KEY not set in .env")

    proxies = {"https": PROXY_URL, "http": PROXY_URL} if USE_PROXY else None

    # createTask
    create_body = {
        "clientKey": CAPSOLVER_API_KEY,
        "task": {
            "type": "ReCaptchaV2EnterpriseTaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": CAPTCHA_SITEKEY,
            "enterprisePayload": {"s": e_token},
            "pageAction": action,
        },
    }
    r = cffi_requests.post(CAPSOLVER_CREATE, json=create_body, timeout=30,
                           impersonate=IMPERSONATE, proxies=proxies)
    res = r.json()
    if res.get("errorId", 0) != 0:
        raise RuntimeError(f"CapSolver createTask error: {res}")
    task_id = res["taskId"]

    # Poll getTaskResult
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        poll = cffi_requests.post(CAPSOLVER_RESULT,
                                  json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id},
                                  timeout=30, impersonate=IMPERSONATE, proxies=proxies)
        res = poll.json()
        if res.get("status") == "ready":
            return res["solution"]["gRecaptchaResponse"]
        if res.get("errorId", 0) != 0:
            raise RuntimeError(f"CapSolver error: {res}")
    raise TimeoutError("CapSolver timed out")


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1: Register akun Xiaomi (8 langkah)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_fingerprint_payload(scene: str = "register", referer: str = REGISTER_URL) -> dict:
    """Build browser fingerprint untuk captcha/v2/data."""
    now_ms = int(time.time() * 1000)
    return {
        "type": 0, "version": "2.0", "scene": scene,
        "force": True, "talkBack": False,
        "env": {
            "p1": "0.1", "p2": "pc-Chrome148",
            "p3": "Macintosh; Intel Mac OS X 10_15_7",
            "p4": "Gecko", "p5": "en-US", "p6": "Netscape", "p7": "Mozilla",
            "p8": True, "p9": UA, "p10": 0, "p11": now_ms,
            "p12": 1280, "p13": 800, "p14": 1280, "p15": 800,
            "p16": 1280, "p17": 800, "p18": referer, "p19": 5,
            "p20": __import__("hashlib").sha1(os.urandom(20)).hexdigest(),
            "p21": "P" + __import__("hashlib").sha1(os.urandom(20)).hexdigest(),
            "p22": 0,
            "p23": "da39a3ee5e6b4b0d3255bfef95601890afd80709",
            "p24": "", "p25": "", "p26": "", "p28": "",
            "p29": 107, "p30": 10, "p31": 10, "p32": "0.73",
            "p33": [], "p34": referer,
        },
        "action": {"a1": [1280, 800], "a2": [], "a3": [], "a4": [], "a5": [],
                   "a6": [], "a7": [], "a8": [], "a9": [], "a10": [],
                   "a11": [], "a12": [], "a13": [], "a14": []},
        "nonce": {"t": int(now_ms / 1000),
                  "r": random.randint(1_000_000_000, 9_999_999_999)},
    }


def _register(email: str, password: str) -> dict:
    """Daftar 1 akun Xiaomi. Return dict dengan cookies + credentials."""
    if not password:
        raise RuntimeError("password kosong — set via .env atau --password")
    if not IMAP_USER or not IMAP_PASS:
        raise RuntimeError("IMAP_USER/IMAP_PASS kosong di .env")
    if not CAPSOLVER_API_KEY:
        raise RuntimeError("CAPSOLVER_API_KEY kosong di .env")

    print("\n[REGISTER] mulai register akun baru")
    print(f"  Email    : {email}")
    print(f"  Password : {'*' * len(password)}")

    session = _make_session()

    # Step 1: Warmup
    print("  [1/8] warmup GET register page…", end=" ")
    r = session.get(REGISTER_URL)
    print(f"✓ ({r.status_code})")

    # Step 2-4: Captcha loop (max 4 attempts karena ~30-50% failure rate)
    vtoken = None
    for attempt in range(4):
        try:
            print(f"  [2/8] captcha data…", end=" ")
            payload = _build_fingerprint_payload(scene="register", referer=REGISTER_URL)
            s, d = _encrypt_captcha_payload(payload)
            ts = int(time.time() * 1000)
            url = f"https://verify.sec.xiaomi.com/captcha/v2/data?k={CAPTCHA_PARAM_K}&locale=en_US&_t={ts}"
            r = session.post(url, data=f"s={urllib.parse.quote(s)}&d={urllib.parse.quote(d)}&a=register",
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
            data = r.json()
            e_token = parse_qs(urlparse(data["data"]["url"]).query)["e"][0]
            print(f"✓ e_token={e_token[:20]}…")

            print(f"  [3/8] solve reCAPTCHA (CapSolver)…", end=" ")
            g_recaptcha = _solve_captcha(e_token, action="register", website_url=REGISTER_URL)
            print(f"✓")

            print(f"  [4/8] verify captcha → vToken…", end=" ")
            ts = int(time.time() * 1000)
            url = f"https://verify.sec.xiaomi.com/captcha/v2/recaptcha/verify?k={CAPTCHA_PARAM_K}&locale=en_US&_t={ts}"
            r = session.post(url,
                              data=f"e={urllib.parse.quote(e_token)}&g={urllib.parse.quote(g_recaptcha)}&type=4",
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
            data = r.json()
            if data.get("code") != 0 or not data.get("data", {}).get("result"):
                raise RuntimeError(f"verify failed: {data}")
            vtoken = data["data"]["token"]
            print(f"✓ vToken={vtoken[:20]}…")
            break
        except Exception as e:
            print(f"✗ {e}")
            if attempt < 3:
                print(f"    retry ({attempt + 1}/4)…")
                time.sleep(2)
            else:
                raise

    # Step 5: Encrypt email+password
    print("  [5/8] encrypt email+password (EUI)…", end=" ")
    enc = _encrypt_form_fields({"email": email, "password": password})
    eui = enc["EUI"]
    enc_email = enc["encryptedParams"]["email"]
    enc_password = enc["encryptedParams"]["password"]
    print(f"✓ EUI={eui[:30]}…")

    # Step 6: sendEmailRegTicket
    print("  [6/8] sendEmailRegTicket (vToken via COOKIE)…", end=" ")
    device_id = f"wb_{uuid.uuid4()}"
    session.cookies.set("vToken",   vtoken,   domain="global.account.xiaomi.com")
    session.cookies.set("vAction",  "register", domain="global.account.xiaomi.com")
    session.cookies.set("deviceId", device_id, domain="global.account.xiaomi.com")
    body = (f"email={urllib.parse.quote(enc_email)}"
            f"&password={urllib.parse.quote(enc_password)}"
            f"&region=ID&sid=&icode=")
    r = session.post("https://global.account.xiaomi.com/pass/sendEmailRegTicket",
                     data=body,
                     headers={
                         "eui": eui,
                         "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                         "X-Requested-With": "XMLHttpRequest",
                         "Referer": REGISTER_URL,
                         "Origin": "https://global.account.xiaomi.com",
                     })
    text = r.text
    if text.startswith("&&&START&&&"):
        text = text[len("&&&START&&&"):]
    data = json.loads(text)
    if data.get("code") != 0:
        raise RuntimeError(f"sendEmailRegTicket failed: {data}")
    print(f"✓ ({data.get('data', {}).get('vCodeLen')} digit code)")

    # Step 7: Read OTP dari IMAP
    print("  [7/8] read OTP from IMAP…", end=" ")
    code = _read_otp_from_imap(email, timeout=120)
    print(f"✓ ({code})")

    # Step 8: verifyEmailRegTicket
    print("  [8/8] verifyEmailRegTicket (create account)…", end=" ")
    enc = _encrypt_form_fields({"email": email, "password": password})
    eui = enc["EUI"]
    enc_email = enc["encryptedParams"]["email"]
    enc_password = enc["encryptedParams"]["password"]
    device_fp = "".join(random.choices("0123456789abcdef", k=32))
    body = (f"ticket={code}"
            f"&region=ID"
            f"&email={urllib.parse.quote(enc_email)}"
            f"&env=web"
            f"&qs=%253Fsid%253Dpassport"
            f"&isAcceptLicense=true"
            f"&sid="
            f"&password={urllib.parse.quote(enc_password)}"
            f"&policyName=globalmiaccount"
            f"&callback="
            f"&deviceFingerprint={device_fp}")
    r = session.post("https://global.account.xiaomi.com/pass/verifyEmailRegTicket",
                     data=body,
                     headers={
                         "accept": "application/json, text/plain, */*",
                         "accept-language": "en-US,en;q=0.9",
                         "cache-control": "no-cache", "pragma": "no-cache",
                         "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                         "eui": eui,
                         "origin": "https://global.account.xiaomi.com",
                         "referer": (
                             "https://global.account.xiaomi.com/fe/service/register/email/verify"
                             "?_locale=en&_uRegion=ID"
                             f"&_user={urllib.parse.quote(email)}"
                             "&_agreementChecked=true"
                         ),
                         "user-agent": UA,
                         "x-requested-with": "XMLHttpRequest",
                     })
    text = r.text
    if text.startswith("&&&START&&&"):
        text = text[len("&&&START&&&"):]
    data = json.loads(text)
    if data.get("code") != 0:
        raise RuntimeError(f"verifyEmailRegTicket failed: {data}")
    print(f"✓")

    # Extract cookies
    cookies = {}
    for name in ("passToken", "serviceToken", "cUserId", "userId"):
        val = session.cookies.get(name, domain="account.xiaomi.com") or session.cookies.get(name)
        if val:
            cookies[name] = val

    result = {
        "email": email,
        "password": password,
        "cookies": cookies,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    print(f"  [REGISTER] ✓ akun dibuat: {email}")
    return result


def _read_otp_from_imap(email: str, timeout: int = 120) -> str:
    """Poll IMAP, cari email OTP 6-digit dari noreply@notice.xiaomi.com untuk `email`."""
    import email as email_lib
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            imap.login(IMAP_USER, IMAP_PASS)
            imap.select("INBOX")
            _, data = imap.search(None, '(UNSEEN FROM "noreply@notice.xiaomi.com")')
            msg_ids = data[0].split()
            for msg_id in reversed(msg_ids[-20:]):
                _, raw = imap.fetch(msg_id, "(RFC822)")
                msg = email_lib.message_from_bytes(raw[0][1])
                to_addr = (msg.get("To", "") or "").lower()
                if email.lower() not in to_addr:
                    continue
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() in ("text/plain", "text/html"):
                            payload = part.get_payload(decode=True)
                            if payload:
                                body = payload.decode("utf-8", errors="replace")
                                break
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body = payload.decode("utf-8", errors="replace")
                body = body.replace("=\r\n", "")
                m = re.search(r"verification code is[:\s]*(\d{6})", body, re.IGNORECASE)
                if m:
                    imap.logout()
                    return m.group(1)
            imap.logout()
        except Exception as e:
            print(f"\n    IMAP error: {e}", end="")
        time.sleep(5)
    raise TimeoutError("OTP tidak diterima dalam 120s")


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2: Login pakai existing cookies (skip captcha)
# ═══════════════════════════════════════════════════════════════════════════════

def _login_with_cookies(account: dict) -> dict:
    """Inject existing passToken, return login_data dict."""
    cookies = account.get("cookies", {})
    if not cookies.get("passToken"):
        raise RuntimeError("no passToken di cookies")

    session = _make_session()
    for name, value in cookies.items():
        if value:
            session.cookies.set(name, value, domain=".xiaomi.com")

    return {
        "email":   account["email"],
        "cookies": cookies,
        "session": session,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3: SSO ke platform.xiaomimimo.com (full redirect chain)
# ═══════════════════════════════════════════════════════════════════════════════

def _sso_to_mimo(login_data: dict) -> dict:
    """Follow OAuth chain: Xiaomi → MiMo STS → /auth/login. Return session + cookies."""
    session = login_data["session"]
    email = login_data["email"]

    # Step A: Probe MiMo API → dapat loginUrl dari 401
    print("  [A] probe /api/v1/auth/login → 401 + loginUrl…", end=" ")
    r = session.post(f"{MIMO_BASE}/api/v1/auth/login",
                     json={"account": email}, impersonate=IMPERSONATE)
    data = r.json()
    login_url = data.get("loginUrl", "")
    if not login_url:
        raise RuntimeError(f"no loginUrl in 401 response: {r.text[:200]}")
    print(f"✓ ({len(login_url)} chars)")

    # Step B: GET loginUrl → 302 → MiMo STS → 307 → /auth/login?userId=...
    print("  [B] follow redirect chain…")
    r = session.get(login_url, impersonate=IMPERSONATE, allow_redirects=False)
    print(f"      1. {r.status_code} → {r.headers.get('Location', '?')[:60]}…")
    redirects = 0
    while r.status_code in (301, 302, 303, 307, 308) and redirects < 5:
        loc = r.headers.get("Location")
        if not loc:
            break
        if loc.startswith("/"):
            parsed = urllib.parse.urlparse(r.url)
            loc = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, loc, "", "", ""))
        r = session.get(loc, impersonate=IMPERSONATE, allow_redirects=False)
        redirects += 1
        print(f"      {redirects + 1}. {r.status_code} → {r.headers.get('Location', '?')[:60]}…")
    print(f"      ✓ SSO chain selesai ({redirects} redirects)")

    # Step C: POST /auth/login untuk aktivasi MiMo session token
    m = re.search(r"userId=(\d+)", r.url)
    if m:
        user_id = m.group(1)
        print(f"  [C] POST /auth/login userId={user_id}…", end=" ")
        login_resp = session.post(r.url, json={"userId": user_id}, impersonate=IMPERSONATE)
        if login_resp.status_code == 200:
            print("✓ MiMo session activated")
        else:
            print(f"⚠ {login_resp.status_code}")
    else:
        print("  [C] (no userId in URL, skip)")

    return {"session": session, "email": email}


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4: Load /api/v1/userProfile (verify session)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_profile(session) -> dict:
    """GET /api/v1/userProfile untuk verify session + lihat data user."""
    print("  [D] GET /api/v1/userProfile…", end=" ")
    r = session.get(f"{MIMO_BASE}/api/v1/userProfile", impersonate=IMPERSONATE)
    if r.status_code != 200:
        raise RuntimeError(f"profile failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"profile error: {data}")
    print(f"✓ (userId={data['data']['userId']})")
    return data["data"]


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN: orchestrate semuanya
# ═══════════════════════════════════════════════════════════════════════════════

def run(email: str = None, password: str = None, account_file: str = None,
        account_row: int = 0) -> dict:
    """End-to-end: register atau pakai existing → SSO → profile.

    Args:
        email: kalau diisi, register akun baru dengan email ini
        password: password untuk register
        account_file: kalau diisi, pakai akun dari JSONL file (skip register)
        account_row: row index di JSONL

    Returns dict {email, cookies, profile, status, ...}
    """
    load_dotenv()

    account = None
    if account_file:
        path = Path(account_file)
        if not path.exists():
            raise FileNotFoundError(f"file not found: {account_file}")
        rows = []
        text = path.read_text().strip()
        # Detect JSON vs JSONL
        if text.startswith("{") and not text.startswith("{" * 2):
            # Could be single JSON object OR JSONL — try JSON first
            try:
                obj = json.loads(text)
                if isinstance(obj, list):
                    rows = [o for o in obj if o.get("status") == "success"]
                elif isinstance(obj, dict):
                    if obj.get("status") == "success":
                        rows = [obj]
                    else:
                        # Maybe one record without status
                        rows = [obj]
            except json.JSONDecodeError:
                # Fall back to JSONL
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("status") == "success":
                            rows.append(obj)
                    except json.JSONDecodeError:
                        pass
        else:
            # JSONL
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("status") == "success":
                        rows.append(obj)
                except json.JSONDecodeError:
                    pass
        if not rows:
            raise ValueError(f"no success records in {account_file}")
        account = rows[account_row]
        email = account["email"]
        password = account.get("password")
        print(f"[E2E] using existing account: {email} (from {account_file})")
    elif email:
        email = email
        password = password or os.getenv("XIAOMI_PASSWORD")
    else:
        raise RuntimeError("provide --email + --password, OR --account <file>")

    print("=" * 60)
    print(f"E2E: register → SSO → MiMo profile (no bind_referral)")
    print(f"Email: {email}")
    print("=" * 60)

    # ── Step 1: Register kalau belum ada account ────────────────────────
    if account is None:
        print("\n[STEP 1] Register akun baru")
        account = _register(email, password)
        # Save
        Path("xiaomi_account.json").write_text(json.dumps(account, indent=2))
        print(f"  [saved] xiaomi_account.json")
    else:
        print("\n[STEP 1] Skip register — pakai existing cookies")
        if not account.get("cookies", {}).get("passToken"):
            # Cookies expired → refresh via register ulang
            print("  cookies kosong, register ulang…")
            password = password or account.get("password")
            if not password:
                raise RuntimeError("no password saved in account record")
            account = _register(email, password)
            Path("xiaomi_account.json").write_text(json.dumps(account, indent=2))

    # ── Step 2: Login pakai cookies ──────────────────────────────────────
    print("\n[STEP 2] Login pakai existing cookies (skip captcha)")
    login_data = _login_with_cookies(account)
    print(f"  ✓ session ready (email={login_data['email']})")

    # ── Step 3: SSO ke MiMo ─────────────────────────────────────────────
    print("\n[STEP 3] SSO ke platform.xiaomimimo.com")
    sso = _sso_to_mimo(login_data)
    mimo_session = sso["session"]

    # ── Step 4: Load profile ────────────────────────────────────────────
    print("\n[STEP 4] Load MiMo profile")
    profile = _load_profile(mimo_session)

    # ── Summary ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("✓✓✓ BERHASIL MASUK MiMo PLATFORM ✓✓✓")
    print("=" * 60)
    print(f"  Email        : {profile.get('email')}")
    print(f"  User ID      : {profile.get('userId')}")
    print(f"  Phone        : {profile.get('phone') or '(belum di-bind)'}")
    print(f"  Agreement    : {profile.get('agreement')}")
    print(f"  IDC          : {profile.get('idc')}")
    print(f"  Region       : {os.getenv('REGION', 'ID')}")
    print("=" * 60)
    print("  NOTE: bind_referral OFF — tidak apply UltraSpeed")
    print("=" * 60)

    return {
        "email": email,
        "cookies": account["cookies"],
        "profile": profile,
        "status": "success",
    }


def main():
    ap = argparse.ArgumentParser(
        description="End-to-end: register → SSO → MiMo profile (tanpa bind_referral)",
    )
    ap.add_argument("--email", help="email untuk register baru")
    ap.add_argument("--password", help="password untuk register baru")
    ap.add_argument("--account", help="JSONL file (output batch/register) untuk pakai existing")
    ap.add_argument("--row", type=int, default=0, help="row index di JSONL")
    args = ap.parse_args()

    if not args.account and not args.email:
        ap.error("provide --email + --password, OR --account <file>")

    try:
        run(email=args.email, password=args.password,
            account_file=args.account, account_row=args.row)
    except Exception as e:
        print(f"\n[FAIL] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()