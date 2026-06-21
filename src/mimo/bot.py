"""Xiaomi MiMo bot — login + SSO + bind referral + apply UltraSpeed.

Flow:
  1. Login ke account.xiaomi.com (pakai serviceToken/cookies dari register.py output)
     - Pakai CapSolver kalau muncul captcha
  2. SSO ke platform.xiaomimimo.com (/sts callback)
  3. (opsional) Bind referral code
  4. (opsional) Apply MiMo UltraSpeed beta

Usage:
    python -m mimo.bot --account accounts.jsonl --row 0
    python -m mimo.bot --account accounts.jsonl --referral MX5V9X
    python -m mimo.bot --email x@y.com --password 'pw' --referral MX5V9X
"""

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Optional

from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

from .crypto import encrypt_captcha_payload, encrypt_form_fields
from .register import build_fingerprint_payload

# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

IMPERSONATE = "chrome124"
PROXY_URL   = os.getenv("PROXY_URL", "socks5://127.0.0.1:40000")
USE_PROXY   = os.getenv("USE_PROXY", "1") == "1"

CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")
CAPSOLVER_CREATE  = "https://api.capsolver.com/createTask"
CAPSOLVER_RESULT  = "https://api.capsolver.com/getTaskResult"

CAPTCHA_SITEKEY = "6LeBM0ocAAAAAEwYcFUjtxpVbs-0rnbSVXBBXmh4"
CAPTCHA_DATA_TPL    = "https://verify.sec.xiaomi.com/captcha/v2/data?k=8027422fb0eb42fbac1b521ec4a7961f&locale=en_US&_t={ts}"
CAPTCHA_VERIFY_TPL  = "https://verify.sec.xiaomi.com/captcha/v2/recaptcha/verify?k=8027422fb0eb42fbac1b521ec4a7961f&locale=en_US&_t={ts}"

# Xiaomi endpoints
LOGIN_URL     = "https://account.xiaomi.com/pass/serviceLoginAuth2"
SSO_LOGIN_URL = "https://account.xiaomi.com/pass/serviceLogin"
LOGIN_PAGE    = "https://account.xiaomi.com/fe/service/login/password"

# MiMo platform
MIMO_BASE          = "https://platform.xiaomimimo.com"
MIMO_REFERRAL_BIND = f"{MIMO_BASE}/api/v1/invitation/bind"
MIMO_ULTRASPEED    = f"{MIMO_BASE}/api/v1/mimo-speed/apply"

# ═══════════════════════════════════════════════════════════════════════════════
# Console helpers
# ═══════════════════════════════════════════════════════════════════════════════

class C:
    RESET   = "\033[0m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    BOLD    = "\033[1m"


def info(msg):  print(f"{C.CYAN}[*]{C.RESET} {msg}")
def ok(msg):    print(f"{C.GREEN}[✓]{C.RESET} {msg}")
def warn(msg):  print(f"{C.YELLOW}[!]{C.RESET} {msg}")
def err(msg):   print(f"{C.RED}[✗]{C.RESET} {msg}")
def step(msg):  print(f"{C.MAGENTA}[▸]{C.RESET} {msg}")


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP helpers
# ═══════════════════════════════════════════════════════════════════════════════

def make_session() -> cffi_requests.Session:
    kwargs = {"impersonate": IMPERSONATE}
    if USE_PROXY and PROXY_URL:
        kwargs["proxies"] = {"https": PROXY_URL, "http": PROXY_URL}
    s = cffi_requests.Session(**kwargs)
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def parse_xiaomi(text: str) -> dict:
    """Strip &&&START&&& prefix and parse JSON."""
    clean = text.strip()
    if clean.startswith("&&&START&&&"):
        clean = clean[len("&&&START&&&"):].strip()
    return json.loads(clean)


def md5_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest().upper()


# ═══════════════════════════════════════════════════════════════════════════════
# CapSolver integration (reuse-style)
# ═══════════════════════════════════════════════════════════════════════════════

def solve_captcha_capsolver(e_token: str, website_url: str = LOGIN_PAGE,
                            timeout: int = 300) -> str | None:
    if not CAPSOLVER_API_KEY:
        raise RuntimeError("CAPSOLVER_API_KEY not set")
    create_body = {
        "clientKey": CAPSOLVER_API_KEY,
        "task": {
            "type": "ReCaptchaV2EnterpriseTaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": CAPTCHA_SITEKEY,
            "enterprisePayload": {"s": e_token},
        },
    }
    proxies = {"https": PROXY_URL, "http": PROXY_URL} if USE_PROXY else None
    resp = cffi_requests.post(CAPSOLVER_CREATE, json=create_body, timeout=30,
                              impersonate=IMPERSONATE, proxies=proxies)
    result = resp.json()
    if result.get("errorId", 0) != 0:
        raise RuntimeError(f"CapSolver createTask error: {result}")
    task_id = result["taskId"]
    info(f"CapSolver task: {task_id}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        poll = cffi_requests.post(CAPSOLVER_RESULT,
                                  json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id},
                                  timeout=30, impersonate=IMPERSONATE, proxies=proxies)
        result = poll.json()
        if result.get("status") == "ready":
            ok(f"Captcha solved")
            return result["solution"]["gRecaptchaResponse"]
        if result.get("errorId", 0) != 0:
            raise RuntimeError(f"CapSolver error: {result}")
    raise TimeoutError("CapSolver timeout")


# ═══════════════════════════════════════════════════════════════════════════════
# Captcha flow (diperlukan kalau login / SSO muncul captcha)
# ═══════════════════════════════════════════════════════════════════════════════

def handle_captcha(session: cffi_requests.Session, *,
                   action: str = "login",
                   referer_url: str = LOGIN_PAGE) -> str | None:
    """Solve captcha & set vToken cookie di session.

    Args:
        action: "login" atau "register" — determines Xiaomi's captcha action
                identifier (server validates against this). Login flow HAR
                uses 'login', register flow HAR uses 'register'.
        referer_url: URL of the page triggering the captcha. Sent as p18/p34
                     in fingerprint payload (Xiaomi validates referer consistency).
    Returns:
        vToken string or None kalau gagal.
    """
    step(f"Initiating captcha challenge (action={action})…")

    s, d = encrypt_captcha_payload(
        build_fingerprint_payload(scene=action, referer_url=referer_url)
    )
    ts = int(time.time() * 1000)
    url = CAPTCHA_DATA_TPL.format(ts=ts)

    e_token = None
    try:
        resp = session.post(
            url,
            data=f"s={urllib.parse.quote(s)}&d={urllib.parse.quote(d)}&a={action}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        data = resp.json()
        if "url" in data:
            parsed = urllib.parse.urlparse(data["url"])
            params = urllib.parse.parse_qs(parsed.query)
            e_token = params.get("e", [None])[0]
        elif "e" in data:
            e_token = data["e"]
    except Exception as e:
        err(f"captcha data failed: {e}")
        return None

    if not e_token:
        m = re.search(r"e=([a-zA-Z0-9_\-]+)", resp.text)
        if m:
            e_token = m.group(1)
    if not e_token:
        err(f"e_token not found: {resp.text[:200]}")
        return None
    info(f"e_token: {e_token[:30]}...")

    g_token = solve_captcha_capsolver(e_token, website_url=LOGIN_PAGE)
    if not g_token:
        return None

    step("Verifying captcha solution...")
    ts = int(time.time() * 1000)
    url = CAPTCHA_VERIFY_TPL.format(ts=ts)
    resp = session.post(
        url,
        data=f"e={urllib.parse.quote(e_token)}&g={urllib.parse.quote(g_token)}&type=4",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    data = resp.json()
    if data.get("code") != 0 or not data.get("data", {}).get("result"):
        err(f"captcha verify failed: {resp.text[:200]}")
        return None
    v_token = data["data"]["token"]
    ok(f"vToken: {v_token[:30]}...")
    return v_token


# ═══════════════════════════════════════════════════════════════════════════════
# Login Xiaomi
# ═══════════════════════════════════════════════════════════════════════════════

def login_with_cookies(account: dict) -> dict | None:
    """Use existing Xiaomi cookies (dari register) to skip login + captcha.

    Returns dict with session populated + cookies, atau None kalau cookies invalid.
    """
    if dry_run := False:
        pass  # never dry-run here
    step(f"Login via existing cookies: {account.get('email')}")
    cookies = account.get("cookies", {})
    if not cookies or "passToken" not in cookies:
        err("no cookies / no passToken in account")
        return None

    session = make_session()
    session.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://account.xiaomi.com",
        "Referer": LOGIN_PAGE,
    })
    # Inject cookies
    for name, value in cookies.items():
        if not value:
            continue
        session.cookies.set(name, value, domain=".xiaomi.com")
    return _login_result(account["email"], cookies, None, session)


def login_xiaomi(email: str, password: str, *, max_captcha_retries: int = 4,
                 dry_run: bool = False) -> dict | None:
    """Login ke account.xiaomi.com.
    Returns dict {email, cookies, passToken, serviceToken, userId, cUserId, session} atau None.
    """
    if dry_run:
        ok(f"[DRY RUN] would login {email}")
        return {"email": email, "dry_run": True, "cookies": {}, "session": None}

    step(f"Login: {email}")
    session = make_session()
    session.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://account.xiaomi.com",
        "Referer": LOGIN_PAGE,
    })

    # Warm-up: GET login page → set initial cookies
    session.get(LOGIN_PAGE, impersonate=IMPERSONATE)

    # Encrypt email for body
    enc = encrypt_form_fields({"email": email})
    enc_user = enc["encryptedParams"]["email"]
    eui = enc["EUI"]
    pw_hash = md5_hash(password)
    dev_fp = hashlib.md5(os.urandom(16)).hexdigest()

    def do_login(capt_code: str = "") -> cffi_requests.Response:
        data = {
            "sid": "api-platform",
            "callback": "",   # empty — Xiaomi rejects invalid callback with code 10025
            "qs": "%3Fsid%3Dpassport",
            "serviceParam": "",
            "_sign": "",
            "user": enc_user,
            "cc": "+86",
            "hash": pw_hash,
            "_json": "true",
            "policyName": "globalmiaccount",
            "captCode": capt_code,
            "deviceFingerprint": dev_fp,
        }
        if eui:
            data["_EUI"] = eui
        return session.post(LOGIN_URL, data=data, impersonate=IMPERSONATE)

    # Attempt 1: no captcha
    resp = do_login()
    try:
        result = parse_xiaomi(resp.text)
    except json.JSONDecodeError:
        err(f"login parse failed: {resp.text[:300]}")
        return None
    code = result.get("code", -1)
    location = result.get("location")

    if code == 0 or location:
        ok(f"login success (code={code})")
        cookies = {c.name: c.value for c in getattr(session.cookies, "jar", session.cookies)}
        return _login_result(email, cookies, location, session)

    # If captcha required
    if code in (70016, 87001):
        warn(f"captcha required (code={code})")
        for attempt in range(max_captcha_retries):
            v_token = handle_captcha(session, action="login")
            if not v_token:
                time.sleep(3)
                continue
            # vToken as COOKIE (not body param)
            session.cookies.set("vToken", v_token, domain="account.xiaomi.com")
            session.cookies.set("vAction", "login", domain="account.xiaomi.com")
            session.cookies.set("deviceId", f"wb_{uuid.uuid4()}",
                                domain="account.xiaomi.com")
            resp = do_login()
            try:
                result = parse_xiaomi(resp.text)
            except json.JSONDecodeError:
                err(f"login parse after captcha: {resp.text[:300]}")
                continue
            code = result.get("code", -1)
            location = result.get("location")
            if code == 0 or location:
                ok("login success after captcha")
                cookies = {c.name: c.value for c in getattr(session.cookies, "jar", session.cookies)}
                return _login_result(email, cookies, location, session)
        err("login failed after all captcha retries")
        return None

    if code == 70002:
        err("wrong password (70002)")
    else:
        err(f"login failed: code={code} — {resp.text[:200]}")
    return None


def _login_result(email: str, cookies: dict, location: str | None,
                  session: cffi_requests.Session) -> dict:
    return {
        "email": email,
        "cookies": cookies,
        "passToken":    cookies.get("passToken", ""),
        "serviceToken": cookies.get("serviceToken", ""),
        "userId":       cookies.get("userId", ""),
        "cUserId":      cookies.get("cUserId", ""),
        "location":     location,
        "session":      session,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SSO → platform.xiaomimimo.com
# ═══════════════════════════════════════════════════════════════════════════════

def sso_to_mimo(login_data: dict, dry_run: bool = False) -> dict | None:
    """Follow OAuth redirect: account.xiaomi.com → /sts → platform.xiaomimimo.com.

    Key insight: MiMo API returns 401 with `loginUrl` (signed by Xiaomi) kalau
    session belum ada. Kita pakai loginUrl itu untuk inisiasi SSO flow.

    Returns {session, cookies} atau None.
    """
    if dry_run:
        ok(f"[DRY RUN] would SSO {login_data['email']}")
        return {"session": None, "cookies": {}}

    step("SSO → MiMo platform...")
    session = make_session()
    session.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    # Inject Xiaomi login cookies
    for name, value in (login_data.get("cookies") or {}).items():
        if not value:
            continue
        session.cookies.set(name, value, domain=".xiaomi.com")
    for key in ("passToken", "serviceToken", "userId", "cUserId"):
        val = login_data.get(key, "")
        if val:
            session.cookies.set(key, val, domain=".xiaomi.com")

    # 1. Hit MiMo API — 401 dengan loginUrl yang sudah ditandatangani Xiaomi
    try:
        resp = session.post(
            f"{MIMO_BASE}/api/v1/auth/login",
            json={"account": login_data["email"]},
            impersonate=IMPERSONATE,
        )
        if resp.status_code != 401:
            err(f"unexpected status from MiMo: {resp.status_code} — {resp.text[:200]}")
        data = resp.json() if resp.text else {}
        login_url = data.get("loginUrl", "")
        if not login_url:
            err(f"no loginUrl in 401 response: {resp.text[:200]}")
            return None
        info(f"got loginUrl from MiMo 401")
    except Exception as e:
        err(f"MiMo auth probe failed: {e}")
        return None

    # 2. Manual follow redirect chain: loginUrl → serviceLogin → MiMo STS → /auth/login
    user_id = None
    try:
        resp = session.get(login_url, impersonate=IMPERSONATE, allow_redirects=False)
        info(f"SSO step 1: {resp.status_code} → {resp.headers.get('Location', '?')[:80]}")
        redirects = 0
        while resp.status_code in (301, 302, 303, 307, 308) and redirects < 5:
            loc = resp.headers.get("Location")
            if not loc:
                break
            if loc.startswith("/"):
                from urllib.parse import urlparse, urlunparse
                parsed = urlparse(resp.url)
                loc = urlunparse((parsed.scheme, parsed.netloc, loc, "", "", ""))
            resp = session.get(loc, impersonate=IMPERSONATE, allow_redirects=False)
            redirects += 1
            info(f"SSO step {redirects + 1}: {resp.status_code} → {resp.headers.get('Location', '?')[:80]}")
        info(f"SSO ended: {resp.status_code} {resp.url[:80]} after {redirects} redirects")

        # Extract userId from final URL
        m = re.search(r"userId=(\d+)", resp.url)
        if m:
            user_id = m.group(1)
            info(f"extracted userId: {user_id}")

        # 3. POST /auth/login untuk aktivasi MiMo session token
        if user_id and "auth/login" in resp.url:
            final_url = resp.url
            login_resp = session.post(
                final_url,
                json={"userId": user_id},
                impersonate=IMPERSONATE,
            )
            info(f"POST /auth/login: {login_resp.status_code}")
            if login_resp.status_code == 200:
                ok("MiMo session activated")
            else:
                warn(f"MiMo activate: {login_resp.status_code} {login_resp.text[:200]}")
    except Exception as e:
        err(f"SSO failed: {e}")
        return None

    # 3. curl_cffi iterates session.cookies as strings; use .jar for Cookie objects
    cookie_jar = getattr(session.cookies, "jar", session.cookies)
    mimo_cookies = {c.name: c.value for c in cookie_jar}
    ph = mimo_cookies.get("api-platform_ph", "")
    st = mimo_cookies.get("api-platform_serviceToken", "")
    if ph or st:
        ok(f"MiMo session (ph={ph[:20]}..., serviceToken={st[:20]}...)")
    else:
        warn(f"no api-platform cookies — got: {list(mimo_cookies.keys())}")
        for k in mimo_cookies:
            if "platform" in k.lower() or "service" in k.lower() or "ph" in k.lower():
                info(f"  candidate: {k}={mimo_cookies[k][:20]}")

    return {"session": session, "cookies": mimo_cookies}


# ═══════════════════════════════════════════════════════════════════════════════
# Bind referral + apply UltraSpeed
# ═══════════════════════════════════════════════════════════════════════════════

def bind_referral(session: cffi_requests.Session, referral_code: str,
                  dry_run: bool = False) -> bool:
    step(f"Bind referral: {referral_code}")
    if dry_run:
        ok(f"[DRY RUN] would bind {referral_code}")
        return True
    try:
        resp = session.post(
            MIMO_REFERRAL_BIND, json={"code": referral_code},
            headers={
                "Content-Type": "application/json",
                "Origin": MIMO_BASE,
                "Referer": f"{MIMO_BASE}/",
            },
            impersonate=IMPERSONATE,
        )
        info(f"referral bind → {resp.status_code}")
        try:
            data = resp.json()
            info(f"  response: {json.dumps(data)[:200]}")
        except Exception:
            pass
        if resp.status_code in (200, 201):
            ok("referral bound")
            return True
        warn(f"referral bind returned {resp.status_code} (may already be bound)")
        return True  # treat as non-fatal
    except Exception as e:
        err(f"referral bind exception: {e}")
        return False


def apply_ultraspeed(session: cffi_requests.Session, *,
                     name: str = "", phone: str = "", email: str = "",
                     company: str = "", industry: str = "",
                     scenario: str = "", additional_info: str = "",
                     dry_run: bool = False) -> bool:
    step("Apply UltraSpeed beta...")
    if dry_run:
        ok("[DRY RUN] would apply UltraSpeed")
        return True
    payload = {
        "name": name, "phone": phone, "email": email,
        "company": company, "industry": industry,
        "scenario": scenario, "additionalInfo": additional_info,
    }
    try:
        resp = session.post(
            MIMO_ULTRASPEED, json=payload,
            headers={
                "Content-Type": "application/json",
                "Origin": MIMO_BASE,
                "Referer": f"{MIMO_BASE}/",
            },
            impersonate=IMPERSONATE,
        )
        info(f"ultraspeed → {resp.status_code}")
        try:
            data = resp.json()
            info(f"  response: {json.dumps(data)[:200]}")
        except Exception:
            pass
        if resp.status_code in (200, 201):
            ok("UltraSpeed application submitted")
            return True
        if resp.status_code == 401:
            warn("UltraSpeed 401 (known issue)")
            return False
        warn(f"UltraSpeed {resp.status_code}")
        return False
    except Exception as e:
        err(f"UltraSpeed exception: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Account loading
# ═══════════════════════════════════════════════════════════════════════════════

def load_account(path: str, row: int = 0) -> dict:
    """Load akun dari JSONL file (output mimo.register atau mimo.batch)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Account file not found: {path}")
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    successes = []
    for ln in lines:
        obj = json.loads(ln)
        if obj.get("status") == "success":
            successes.append(obj)
    if not successes:
        raise ValueError(f"No successful accounts in {path}")
    if row >= len(successes):
        raise IndexError(f"row {row} out of range (max {len(successes) - 1})")
    return successes[row]


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def run(email: str, password: str, *,
        referral_code: str = "",
        name: str = "", phone: str = "",
        company: str = "", industry: str = "",
        scenario: str = "", additional_info: str = "",
        dry_run: bool = False,
        existing_cookies: dict | None = None) -> dict:
    """Run full MiMo flow.

    Args:
        email, password: Xiaomi account credentials.
        existing_cookies: dict dari register.json output (passToken, serviceToken, ...).
                         Kalau ada, skip login+captcha, langsung ke SSO.
    """
    result = {
        "email": email,
        "login": False, "sso": False, "referral": False, "ultraspeed": False,
        "status": "failed", "error": None,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        # Try login via existing cookies first (no captcha needed)
        if existing_cookies and existing_cookies.get("passToken"):
            login_data = login_with_cookies({"email": email, "cookies": existing_cookies})
            if not login_data:
                warn("login with existing cookies failed, falling back to fresh login + captcha")
                login_data = login_xiaomi(email, password, dry_run=dry_run)
        else:
            login_data = login_xiaomi(email, password, dry_run=dry_run)
        if not login_data:
            result["error"] = "login failed"
            return result
        result["login"] = True

        if dry_run:
            result.update({"sso": True, "referral": True, "ultraspeed": True,
                          "status": "dry_run"})
            return result

        mimo = sso_to_mimo(login_data)
        if not mimo or not mimo.get("session"):
            result["error"] = "SSO failed"
            return result
        result["sso"] = True
        mimo_session = mimo["session"]

        if referral_code:
            result["referral"] = bind_referral(mimo_session, referral_code, dry_run=dry_run)
        else:
            warn("no referral — skipping")
            result["referral"] = True

        result["ultraspeed"] = apply_ultraspeed(
            mimo_session, name=name, phone=phone, email=email,
            company=company, industry=industry,
            scenario=scenario, additional_info=additional_info, dry_run=dry_run,
        )

        if all([result["login"], result["sso"], result["referral"], result["ultraspeed"]]):
            result["status"] = "success"
        elif result["login"] and result["sso"]:
            result["status"] = "partial"
    except Exception as e:
        result["error"] = str(e)
        err(f"unexpected: {e}")
    return result


def main():
    load_dotenv()

    ap = argparse.ArgumentParser(
        description="MiMo bot: login Xiaomi → SSO MiMo → bind referral → apply UltraSpeed",
    )
    ap.add_argument("--account", help="JSONL file (output mimo.register / mimo.batch)")
    ap.add_argument("--row", type=int, default=0,
                    help="row index in JSONL (0 = first success)")
    ap.add_argument("--email", help="email (override / kalau tidak pakai --account)")
    ap.add_argument("--password", help="password (override)")
    ap.add_argument("--referral", default=os.getenv("REFERRAL_CODE", ""),
                    help="referral code")
    # UltraSpeed form
    ap.add_argument("--name", default="")
    ap.add_argument("--phone", default="")
    ap.add_argument("--company", default="")
    ap.add_argument("--industry", default="")
    ap.add_argument("--scenario", default="")
    ap.add_argument("--additional-info", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Load credentials
    existing_cookies = None
    if args.account:
        acc = load_account(args.account, args.row)
        email = acc["email"]
        password = acc["password"]
        existing_cookies = acc.get("cookies", {})
    elif args.email and args.password:
        email = args.email
        password = args.password
    else:
        err("provide --account <file> OR --email + --password")
        sys.exit(1)

    info(f"Email:    {email}")
    info(f"Referral: {args.referral or '(none)'}")
    info(f"CapSolver: {'configured' if CAPSOLVER_API_KEY else 'NOT SET'}")
    info(f"Proxy:    {PROXY_URL if USE_PROXY else '(none)'}")
    if existing_cookies and existing_cookies.get("passToken"):
        info(f"Cookies:  using existing passToken (skip captcha)")
    if args.dry_run:
        warn("DRY RUN — no actual actions")
    print()

    result = run(
        email=email, password=password,
        referral_code=args.referral,
        name=args.name, phone=args.phone,
        company=args.company, industry=args.industry,
        scenario=args.scenario, additional_info=args.additional_info,
        dry_run=args.dry_run,
        existing_cookies=existing_cookies,
    )

    # Summary
    print()
    print(f"{'─' * 50}")
    color = C.GREEN if result["status"] == "success" else C.YELLOW if result["status"] == "partial" else C.RED
    print(f"  Status: {color}{C.BOLD}{result['status'].upper()}{C.RESET}")
    for s in ("login", "sso", "referral", "ultraspeed"):
        icon = "✓" if result[s] else "✗"
        col = C.GREEN if result[s] else C.RED
        print(f"    {col}{icon}{C.RESET} {s}")
    if result.get("error"):
        print(f"    {C.RED}Error: {result['error']}{C.RESET}")
    print(f"{'─' * 50}")

    sys.exit(0 if result["status"] in ("success", "partial", "dry_run") else 1)


if __name__ == "__main__":
    main()