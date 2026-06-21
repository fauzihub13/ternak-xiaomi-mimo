"""
Xiaomi Account Registration — Browserless (CapSolver edition).

Berdasarkan reverse-engineering dari HAR + referensi guajiimi/xiaomi-register.
8-step flow: warmup → captcha fingerprint → 2Captcha/CapSolver → verify →
encrypt email+password → send verification → IMAP read code → create account.

Diff dari versi upstream:
  - 2Captcha (api.2captcha.com)  →  CapSolver (api.capsolver.com)
  - Task type: RecaptchaV2EnterpriseTaskProxyless → ReCaptchaV2EnterpriseTaskProxyLess

Penggunaan:
  cp .env.example .env
  python -m mimo.register
"""

import base64
import email as email_lib
import imaplib
import json
import os
import random
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad
from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

# ─── CONFIG ──────────────────────────────────────────────────────────────────
load_dotenv()

CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")
CAPSOLVER_CREATE  = "https://api.capsolver.com/createTask"
CAPSOLVER_RESULT  = "https://api.capsolver.com/getTaskResult"

CAPTCHA_SITE_KEY = "6LeBM0ocAAAAAEwYcFUjtxpVbs-0rnbSVXBBXmh4"
CAPTCHA_PARAM_K  = "8027422fb0eb42fbac1b521ec4a7961f"

REGISTER_URL = (
    "https://global.account.xiaomi.com/fe/service/register"
    "?_locale=en_US&_uRegion=ID"
)

# Captcha RSA key (2048-bit) — encrypts AES key for `s` payload
CAPTCHA_RSA_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArxfNLkuAQ/BYHzkzVwtu
g+0abmYRBVCEScSzGxJIOsfxVzcuqaKO87H2o2wBcacD3bRHhMjTkhSEqxPjQ/FE
XuJ1cdbmr3+b3EQR6wf/cYcMx2468/QyVoQ7BADLSPecQhtgGOllkC+cLYN6Md34
Uii6U+VJf0p0q/saxUTZvhR2ka9fqJ4+6C6cOghIecjMYQNHIaNW+eSKunfFsXVU
+QfMD0q2EM9wo20aLnos24yDzRjh9HJc6xfr37jRlv1/boG/EABMG9FnTm35xWrV
R0nw3cpYF7GZg13QicS/ZwEsSd4HyboAruMxJBPvK3Jdr4ZS23bpN0cavWOJsBqZ
VwIDAQAB
-----END PUBLIC KEY-----"""

AES_IV    = b"0102030405060708"
KEY_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*"

EMAIL    = os.getenv("EMAIL", f"miacc{int(time.time())}@example.com")
PASSWORD = os.getenv("XIAOMI_PASSWORD", "")

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASS = os.getenv("IMAP_PASS", "")

# Proxy WARP / residential (opsional). VPS IP di-block 503.
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:40000")
USE_PROXY = os.getenv("USE_PROXY", "1") == "1"

# Path ke encrypt.cjs
ENCRYPT_CJS = Path(__file__).parent / "crypto" / "encrypt.cjs"

# ─── HTTP SESSION ────────────────────────────────────────────────────────────
def make_session() -> cffi_requests.Session:
    kwargs = {"impersonate": "chrome124"}
    if USE_PROXY and PROXY_URL:
        kwargs["proxies"] = {"https": PROXY_URL, "http": PROXY_URL}
    s = cffi_requests.Session(**kwargs)
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


# ─── CRYPTO HELPERS ──────────────────────────────────────────────────────────
def random_aes_key(length: int = 16) -> str:
    return "".join(random.choices(KEY_CHARS, k=length))


def aes_encrypt(plaintext: str, aes_key: str) -> str:
    """AES-128-CBC + PKCS7 padding. Returns base64."""
    cipher = AES.new(aes_key.encode("utf-8"), AES.MODE_CBC, AES_IV)
    ct = cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))
    return base64.b64encode(ct).decode("utf-8")


def rsa_encrypt_pkcs1(b64_data: str, pem: str) -> str:
    """RSA PKCS1v15 encrypt base64-encoded string. Returns base64."""
    key = RSA.import_key(pem)
    cipher = PKCS1_v1_5.new(key)
    ct = cipher.encrypt(b64_data.encode("utf-8"))
    return base64.b64encode(ct).decode("utf-8")


def encrypt_captcha_payload(payload: dict) -> tuple[str, str]:
    """Encrypt captcha fingerprint. Returns (s, d)."""
    aes_key = random_aes_key()
    payload_json = json.dumps(payload, separators=(",", ":"))
    d = aes_encrypt(payload_json, aes_key)
    s = rsa_encrypt_pkcs1(base64.b64encode(aes_key.encode()).decode(), CAPTCHA_RSA_PEM)
    return s, d


def encrypt_form_fields(fields: dict) -> dict:
    """Build EUI header + encrypted fields via Node.js encrypt.cjs.
    Returns {"EUI": "...", "encryptedParams": {"email": "...", "password": "..."}}.
    """
    result = subprocess.run(
        ["node", str(ENCRYPT_CJS), json.dumps(fields)],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"encrypt.cjs failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


# ─── FINGERPRINT PAYLOAD (template dari upstream) ────────────────────────────
def build_fingerprint_payload(template: dict | None = None) -> dict:
    """Refresh timestamp/nonce fields dari template, return ready-to-encrypt payload."""
    if template is None:
        template_path = Path(__file__).parent / "crypto" / "payload_template.json"
        if template_path.exists():
            template = json.loads(template_path.read_text())
        else:
            template = _default_payload_template()
    now_ms = int(time.time() * 1000)
    template["startTs"] = now_ms
    template["endTs"]   = now_ms + random.randint(500, 1500)
    template["env"]["p11"] = now_ms
    template["env"]["p33"] = []  # no webdriver flag
    template["nonce"]["t"] = int(now_ms / 1000)
    template["nonce"]["r"] = random.randint(1_000_000_000, 9_999_999_999)
    return template


def _default_payload_template() -> dict:
    """Fallback jika payload_template.json tidak ada."""
    return {
        "type": 0, "version": "2.0", "scene": "register",
        "force": True, "talkBack": False,
        "env": {
            "p1": "0.1", "p2": "pc-Chrome148", "p3": "Windows NT 10.0; Win64; x64",
            "p4": "Gecko", "p5": "en-US", "p6": "Netscape", "p7": "Mozilla",
            "p8": True, "p9": "Mozilla/5.0 ... Chrome/148 ...", "p10": 0,
            "p11": 0, "p12": 1280, "p13": 800, "p14": 1280, "p15": 800,
            "p16": 1280, "p17": 800, "p18": REGISTER_URL, "p19": 5,
            "p20": "", "p21": "", "p22": 0,
            "p23": "da39a3ee5e6b4b0d3255bfef95601890afd80709",
            "p24": "", "p25": "", "p26": "", "p28": "",
            "p29": 107, "p30": 10, "p31": 10, "p32": "0.73",
            "p33": [], "p34": REGISTER_URL,
        },
        "action": {
            "a1": [1280, 800], "a2": [], "a3": [], "a4": [], "a5": [],
            "a6": [], "a7": [], "a8": [], "a9": [], "a10": [],
            "a11": [], "a12": [], "a13": [], "a14": [],
        },
        "nonce": {"t": 0, "r": 0},
    }


# ─── RESPONSE PARSING ────────────────────────────────────────────────────────
def parse_xiaomi(text: str) -> dict:
    """Strip &&&START&&& prefix (JSONP) and parse JSON."""
    clean = text.strip()
    if clean.startswith("&&&START&&&"):
        clean = clean[len("&&&START&&&"):].strip()
    return json.loads(clean)


# ─── CAPSOLVER INTEGRATION ───────────────────────────────────────────────────
def solve_captcha_capsolver(e_token: str, timeout: int = 300) -> str | None:
    """Solve reCAPTCHA Enterprise via CapSolver.
    Returns gRecaptchaResponse token atau None.
    """
    if not CAPSOLVER_API_KEY:
        raise RuntimeError("CAPSOLVER_API_KEY belum di-set di .env")

    # createTask
    create_body = {
        "clientKey": CAPSOLVER_API_KEY,
        "task": {
            "type": "ReCaptchaV2EnterpriseTaskProxyLess",
            "websiteURL": REGISTER_URL,
            "websiteKey": CAPTCHA_SITE_KEY,
            "enterprisePayload": {"s": e_token},   # WAJIB!
        },
    }
    resp = cffi_requests.post(CAPSOLVER_CREATE, json=create_body, timeout=30,
                              impersonate="chrome124",
                              proxies={"https": PROXY_URL, "http": PROXY_URL} if USE_PROXY else None)
    result = resp.json()
    print(f"  [capsolver] createTask: errorId={result.get('errorId')}, taskId={result.get('taskId')}")

    if result.get("errorId", 0) != 0:
        raise RuntimeError(f"CapSolver createTask error: {result}")

    task_id = result["taskId"]
    deadline = time.time() + timeout

    while time.time() < deadline:
        time.sleep(5)
        poll = cffi_requests.post(
            CAPSOLVER_RESULT,
            json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id},
            timeout=30, impersonate="chrome124",
            proxies={"https": PROXY_URL, "http": PROXY_URL} if USE_PROXY else None,
        )
        result = poll.json()
        status = result.get("status")
        print(f"  [capsolver] poll: status={status}, errorId={result.get('errorId')}")

        if status == "ready":
            g = result["solution"]["gRecaptchaResponse"]
            print(f"  [capsolver] ✓ solved: {g[:50]}...")
            return g
        if result.get("errorId", 0) != 0:
            raise RuntimeError(f"CapSolver error: {result}")
    raise TimeoutError(f"CapSolver timed out after {timeout}s")


# ─── 8-STEP REGISTER FLOW ────────────────────────────────────────────────────
class RegisterError(Exception):
    pass


def step1_warmup(session: cffi_requests.Session) -> None:
    print("\n[Step 1] GET register page (warm-up)...")
    resp = session.get(REGISTER_URL)
    print(f"  status: {resp.status_code}")
    print(f"  cookies: {dict(session.cookies)}")


def step2_captcha_data(session: cffi_requests.Session) -> str:
    print("\n[Step 2] POST captcha/v2/data...")
    payload = build_fingerprint_payload()
    s, d = encrypt_captcha_payload(payload)

    ts = int(time.time() * 1000)
    url = f"https://verify.sec.xiaomi.com/captcha/v2/data?k={CAPTCHA_PARAM_K}&locale=en_US&_t={ts}"
    body = f"s={quote(s)}&d={quote(d)}&a=register"
    resp = session.post(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    data = resp.json()
    print(f"  response code: {data.get('code')}")
    if data.get("code") != 0:
        raise RegisterError(f"captcha/v2/data failed: {data}")

    e_token = parse_qs(urlparse(data["data"]["url"]).query)["e"][0]
    print(f"  e_token: {e_token[:40]}...")
    return e_token


def step3_solve_captcha(e_token: str) -> str:
    print("\n[Step 3] Solve reCAPTCHA Enterprise via CapSolver...")
    return solve_captcha_capsolver(e_token)


def step4_recaptcha_verify(session: cffi_requests.Session, e_token: str, g_recaptcha: str) -> str:
    print("\n[Step 4] POST captcha/v2/recaptcha/verify...")
    ts = int(time.time() * 1000)
    url = f"https://verify.sec.xiaomi.com/captcha/v2/recaptcha/verify?k={CAPTCHA_PARAM_K}&locale=en_US&_t={ts}"
    body = f"e={quote(e_token)}&g={quote(g_recaptcha)}&type=4"
    resp = session.post(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    data = resp.json()
    print(f"  response: code={data.get('code')}, result={data.get('data', {}).get('result')}")

    if data.get("code") != 0 or not data.get("data", {}).get("result"):
        raise RegisterError(f"recaptcha verify failed: {data}")

    vtoken = data["data"]["token"]
    print(f"  vToken: {vtoken[:50]}...")
    return vtoken


def step5_encrypt() -> tuple[str, str, str]:
    print("\n[Step 5] Encrypt email+password (EUI)...")
    out = encrypt_form_fields({"email": EMAIL, "password": PASSWORD})
    eui = out["EUI"]
    enc_email = out["encryptedParams"]["email"]
    enc_password = out["encryptedParams"]["password"]
    print(f"  EUI: {eui[:60]}...")
    print(f"  enc_email: {enc_email[:40]}...")
    return eui, enc_email, enc_password


def step6_send_email_reg_ticket(session: cffi_requests.Session, vtoken: str,
                                eui: str, enc_email: str, enc_password: str) -> dict:
    print("\n[Step 6] POST sendEmailRegTicket...")

    device_id = f"wb_{uuid.uuid4()}"
    # Cookies: vToken membawa captcha pass (BUKAN icode!)
    session.cookies.set("vToken",   vtoken,   domain="global.account.xiaomi.com")
    session.cookies.set("vAction",  "register", domain="global.account.xiaomi.com")
    session.cookies.set("deviceId", device_id, domain="global.account.xiaomi.com")

    headers = {
        "eui": eui,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": REGISTER_URL,
        "Origin": "https://global.account.xiaomi.com",
    }
    body = urlencode({
        "email": enc_email,
        "password": enc_password,
        "region": "ID",
        "sid": "",
        "icode": "",   # SENGAJA KOSONG — captcha pass via vToken cookie
    })
    resp = session.post(
        "https://global.account.xiaomi.com/pass/sendEmailRegTicket",
        data=body, headers=headers,
    )
    text = resp.text
    print(f"  raw response: {text[:200]}")
    data = parse_xiaomi(text)
    print(f"  parsed: {data}")
    if data.get("code") != 0:
        raise RegisterError(f"sendEmailRegTicket failed: {data}")
    print(f"  vCodeLen: {data.get('data', {}).get('vCodeLen')}")
    return data


def step7_read_imap_code(timeout: int = 120) -> str:
    print(f"\n[Step 7] Read 6-digit code from IMAP for {EMAIL}...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            imap.login(IMAP_USER, IMAP_PASS)
            imap.select("INBOX")
            _, msg_data = imap.search(None, '(UNSEEN FROM "noreply@notice.xiaomi.com")')
            msg_ids = msg_data[0].split()
            for msg_id in reversed(msg_ids[-20:]):
                _, raw_data = imap.fetch(msg_id, "(RFC822)")
                msg = email_lib.message_from_bytes(raw_data[0][1])
                to_addr = (msg.get("To", "") or "").lower()
                if EMAIL.lower() not in to_addr:
                    continue
                # Decode body
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
                body = body.replace("=\r\n", "")  # remove MIME soft-break
                match = re.search(r"verification code is[:\s]*(\d{6})", body, re.IGNORECASE)
                if match:
                    code = match.group(1)
                    print(f"  ✓ found code: {code}")
                    imap.logout()
                    return code
            imap.logout()
        except Exception as e:
            print(f"  IMAP error: {e}")
        print(f"  no code yet, retrying in 5s...")
        time.sleep(5)
    raise TimeoutError("Did not receive verification code within timeout")


def step8_verify_email_reg_ticket(session: cffi_requests.Session, code: str) -> dict:
    print("\n[Step 8] POST verifyEmailRegTicket (creating account)...")
    # Re-encrypt fresh (different AES key + EUI per request)
    out = encrypt_form_fields({"email": EMAIL, "password": PASSWORD})
    eui = out["EUI"]
    enc_email = out["encryptedParams"]["email"]
    enc_password = out["encryptedParams"]["password"]
    device_fp = "".join(random.choices("0123456789abcdef", k=32))

    # Headers HAR — penting agar tidak di-block server.
    # HAR entry #8: ada origin + referer ke verify page (bukan register page).
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "eui": eui,
        "origin": "https://global.account.xiaomi.com",
        "referer": (
            "https://global.account.xiaomi.com/fe/service/register/email/verify"
            "?_locale=en&_uRegion=ID"
            f"&_user={quote(EMAIL, safe='')}"
            "&_agreementChecked=true"
        ),
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
        "x-requested-with": "XMLHttpRequest",
    }
    body = (
        f"ticket={code}"
        f"&region=ID"
        f"&email={quote(enc_email, safe='')}"
        f"&env=web"
        f"&qs=%253Fsid%253Dpassport"
        f"&isAcceptLicense=true"
        f"&sid="
        f"&password={quote(enc_password, safe='')}"
        f"&policyName=globalmiaccount"
        f"&callback="
        f"&deviceFingerprint={device_fp}"
    )

    # Debug: print cookies yang akan dikirim
    cookie_names = sorted({c.name for c in session.cookies})
    print(f"  sending cookies: {cookie_names}")

    resp = session.post(
        "https://global.account.xiaomi.com/pass/verifyEmailRegTicket",
        data=body, headers=headers,
    )
    text = resp.text
    print(f"  status: {resp.status_code}")
    print(f"  raw response: {text[:500]}")
    set_cookies = resp.headers.get_list("Set-Cookie") if hasattr(resp.headers, "get_list") else []
    if set_cookies:
        print(f"  response set-cookies ({len(set_cookies)}):")
        for sc in set_cookies:
            print(f"    {sc[:150]}")
    try:
        data = parse_xiaomi(text)
    except json.JSONDecodeError as e:
        raise RegisterError(
            f"verifyEmailRegTicket returned non-JSON: {e}; "
            f"status={resp.status_code}; body={text[:300]}"
        )
    print(f"  parsed: {data}")

    code_val = data.get("code")
    desc = data.get("description", "")

    # Specific error guidance
    if code_val != 0:
        hints = {
            70003: "Tiket OTP salah atau kadaluarsa. Coba lagi dengan ticket baru.",
            70022: "Rate limit — terlalu sering. Tunggu beberapa menit.",
            70016: "Captcha perlu diulang — re-run dari step 2.",
            87001: "Captcha verification error — re-run dari step 2.",
            88205: "Email address ditolak oleh Xiaomi. Coba email lain.",
            70002: "Email atau password salah (atau sudah dipakai).",
        }
        hint = hints.get(code_val, "Lihat description untuk detail.")
        raise RegisterError(
            f"verifyEmailRegTicket failed: code={code_val} "
            f"description={desc} "
            f"hint={hint} "
            f"data={data.get('data')}"
        )
    return data


# ─── MAIN ────────────────────────────────────────────────────────────────────
def register() -> dict:
    """Execute 8-step registration. Return dict with credentials + cookies."""
    if not PASSWORD:
        raise RuntimeError("XIAOMI_PASSWORD not set")
    if not IMAP_USER or not IMAP_PASS:
        raise RuntimeError("IMAP_USER and IMAP_PASS required for OTP")
    if not CAPSOLVER_API_KEY:
        raise RuntimeError("CAPSOLVER_API_KEY not set")

    print("=" * 60)
    print("Xiaomi Account Registration — CapSolver edition")
    print(f"Email:    {EMAIL}")
    print(f"Password: {'*' * len(PASSWORD)}")
    print(f"Proxy:    {PROXY_URL if USE_PROXY else '(none)'}")
    print("=" * 60)

    session = make_session()

    # Pre-cleanup: mark old Xiaomi emails as read
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(IMAP_USER, IMAP_PASS)
        imap.select("INBOX")
        _, d = imap.search(None, '(UNSEEN FROM "noreply@notice.xiaomi.com")')
        ids = d[0].split()
        for mid in ids:
            imap.store(mid, "+FLAGS", "\\Seen")
        if ids:
            print(f"\n[cleanup] marked {len(ids)} old Xiaomi emails as read")
        imap.logout()
    except Exception as e:
        print(f"\n[cleanup] error: {e}")

    step1_warmup(session)

    # Steps 2-4 with retry loop (4 attempts because ~30-50% captcha failure)
    vtoken = None
    for attempt in range(4):
        try:
            e_token = step2_captcha_data(session)
            g_recaptcha = step3_solve_captcha(e_token)
            vtoken = step4_recaptcha_verify(session, e_token, g_recaptcha)
            break
        except (RegisterError, RuntimeError, TimeoutError) as e:
            print(f"\n  attempt {attempt + 1} failed: {e}")
            if attempt < 3:
                print("  retrying from step 2 with new e_token...")
                time.sleep(2)
            else:
                raise

    eui, enc_email, enc_password = step5_encrypt()
    step6_send_email_reg_ticket(session, vtoken, eui, enc_email, enc_password)
    code = step7_read_imap_code()
    step8_verify_email_reg_ticket(session, code)

    print("\nAccount created successfully! Fetching cookies...")

    # Extract cookies
    cookies = {c.name: c.value for c in session.cookies}
    result = {
        "email": EMAIL,
        "password": PASSWORD,
        "cookies": cookies,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    print("\n" + "=" * 60)
    print("ACCOUNT CREATED SUCCESSFULLY!")
    print("=" * 60)
    print(f"Email:    {EMAIL}")
    print(f"Cookies:  {json.dumps(cookies, indent=2)}")

    out_path = Path("xiaomi_account.json")
    out_path.write_text(json.dumps(result, indent=2))
    print(f"\nSaved to {out_path.absolute()}")
    return result


if __name__ == "__main__":
    try:
        register()
    except Exception as e:
        print(f"\n[FAIL] {type(e).__name__}: {e}")
        raise SystemExit(1)