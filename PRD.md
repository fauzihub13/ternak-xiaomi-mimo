# PRD — Automasi Registrasi Akun Xiaomi MiMo

> **Sumber:** `regis only account.xiaomi.com.har` (17 entry, 32 detik, 11 POST + 6 GET). HAR hanya mencatat request API/XHR dari JS — tidak memuat halaman HTML statis atau response body (HAR tanpa konten). Semua asumsi tentang response shape menunggu konfirmasi reverse-engineering frontend JS.

---

## 1. Latar Belakang & Tujuan

**Latar belakang:** Platform `platform.xiaomimimo.com` mewajibkan user memiliki akun **Xiaomi Global Account** (`account.xiaomi.com` / `global.account.xiaomi.com`). Akun MiMo tidak dibuat langsung di platform — registrasi terjadi di layanan akun Xiaomi, lalu user di-redirect via OAuth ke platform.

Tujuan otomasi:
1. Mampu **membuat akun Xiaomi baru** dari script (input: email + password + region; output: akun aktif + session/cookie).
2. Setelah akun dibuat, otomatis mengarahkan akun ke **onboarding MiMo** (login ke `platform.xiaomimimo.com` via STS callback).
3. Output: kredensial akun + cookies siap pakai untuk otomasi §3 PRD lama (balance/invitation/etc).
4. Bisa dijalankan batch (beberapa akun) untuk testing / provisioning internal.

**Non-tujuan:**
- Bypass permanen reCAPTCHA Enterprise (lihat §6).
- Mengelola payment / top-up saldo.
- Scraping data user lain.

---

## 2. Ringkasan Flow (berdasarkan HAR)

**Origin:** `https://global.account.xiaomi.com/fe/service/register?_locale=en&_uRegion=ID`

**Akun yang dibuat:** email `rexatara62@gmail.com` (AES-encrypted di body), region `ID` (Indonesia), `userId` baru = `6878925911`.

### 2.1 Timeline (3 detik kritis + 9 detik jeda baca OTP)

| t (s) | # | Method | Endpoint | Body ringkas | Tujuan |
|---|---|---|---|---|---|
| 0.000 | 0 | POST | `global.account.xiaomi.com/pass/sendEmailRegTicket` | `email`, `password`, `region=ID`, `sid=`, `icode=` (semua AES-encrypted) | Kirim kode 6-digit ke email. **Attempt 1 — gagal** karena captcha missing. |
| 0.046 | 1 | POST | `verify.sec.xiaomi.com/captcha/v2/data?k=8027422fb0eb42fbac1b521ec4a7961f&locale=en_US` | `s`, `d`, `a=register` (encrypted) | Minta challenge captcha Xiaomi. |
| 3.857 | 2 | POST | `www.google.com/recaptcha/enterprise/reload?k=6LeBM0ocAAAAAEwYcFUjtxpVbs-0rnbSVXBBXmh4` | (reCAPTCHA internal) | reCAPTCHA Enterprise: reload token. |
| 8.396 | 3 | POST | `www.google.com/recaptcha/enterprise/userverify?k=6LeBM0oc…` | (reCAPTCHA internal) | reCAPTCHA Enterprise: user verify. |
| 8.474 | 4 | POST | `verify.sec.xiaomi.com/captcha/v2/recaptcha/verify?k=8027422fb0eb42fbac1b521ec4a7961f&locale=en_US` | (encrypted token dari reCAPTCHA) | Submit captcha token ke Xiaomi → dapat `icode`. |
| 8.484 | 5 | POST | `www.google.com/recaptcha/enterprise/clr?k=6LeBM0oc…` | (cleanup) | reCAPTCHA cleanup. |
| 8.565 | 6 | POST | `global.account.xiaomi.com/pass/sendEmailRegTicket` | (sama seperti #0, sekarang captcha valid) | Kirim kode ke email. **Attempt 2 — sukses**, dapat `ticket=418020`. |
| 8.788 | 7 | POST | `global.account.xiaomi.com/pass/sms/quota` | `address=rexatara62%40gmail.com`, `templateId=CI93714_EM_153` | Cek quota pengiriman (email di-prefix `_EM_`). |
| 22.474 | 8 | POST | `global.account.xiaomi.com/pass/verifyEmailRegTicket` | `ticket=418020`, `region=ID`, `email`, `env=web`, `qs=%3Fsid%3Dpassport`, `isAcceptLicense=true`, `sid=`, `password`, `policyName=globalmiaccount`, `callback=`, `deviceFingerprint=597117090e887e57b19386c3c54a0a8d` | Submit OTP + accept license → buat akun. **Sukses** → user baru `6878925911`. |
| 23.938 | 9 | GET | `firebase.googleapis.com/v1alpha/projects/-/apps/1:819836638382:web:5cf09e08e726391857c93f/webConfig` | — | Firebase setup (post-login telemetry). |
| 23.965 | 10 | POST | `firebaseinstallations.googleapis.com/v1/projects/xiaomiaccount/installations` | — | Register Firebase installation ID. |
| 24.933 | 11 | GET | `account.xiaomi.com/pass2/config?key=login&key=register&_locale=en&sid=passport&_uRegion=` | — | Load post-register config. |
| 24.933 | 12 | GET | `account.xiaomi.com/pass2/security/home?userId=6878925911&bizFlag=` | — | Halaman setup security (phone bind, dll). |
| 26.108 | 13 | GET | `firebase.googleapis.com/...` | — | (duplicate) |
| 26.881 | 14 | GET | `account.xiaomi.com/pass2/config?...` | — | (duplicate) |
| 26.881 | 15 | GET | `account.xiaomi.com/pass2/security/home?...` | — | (duplicate, React useEffect) |
| 31.670 | 16 | POST | `www.google-analytics.com/g/collect?...` | — | GA4 telemetry. |

**Catatan waktu:** jeda 14 detik (#6 → #8) adalah user membaca email & mengetik OTP. Target otomasi: < 30 detik end-to-end.

---

## 3. Komponen Teknis yang Harus Di-reverse-engineer

### 3.1 Enkripsi AES untuk field `email` & `password`

Bukti: di body request `#0/#6`, `email` & `password` tidak plaintext melainkan base64 dari AES-CBC ciphertext (panjang sesuai input + padding).

```
email    (plaintext): rexatara62@gmail.com
email    (encrypted): mbv+69fBfat9loMLV4hQvVuMjEczD/FfSUjpZs6ruGI=
password (encrypted): aV8lvxx/alnPM+vF2yg1WA==
```

**Yang harus dicari:**
- Key & IV (kemungkinan hard-coded di JS Xiaomi, atau di-derive dari `eui` / device fingerprint).
- Algoritma pasti (AES-CBC, AES-GCM, AES-ECB).
- Format encoding (base64 standard atau URL-safe).

**Cara riset:** load halaman `https://global.account.xiaomi.com/fe/service/register?_locale=en&_uRegion=ID` di Chrome DevTools → tab **Sources** → cari keyword `encrypt`, `AES`, `CryptoJS`. Set breakpoint di submit handler untuk inspect parameter.

### 3.2 Header `eui`

Panjang ~250 char, format base64. Dikirim **konsisten** di semua request Xiaomi (tidak dikirim ke Google/verify.sec). Kemungkinan `deviceId` terenkripsi + timestamp + signature.

```
eui: UQsQRaicsqvTFl6Jatul6kd4Emm0GoYZAynrB4y090RPRfWcUKF+pgrsn8q84Ia2bELFYAu4saEHgrqb4o2eBh6+/343hqTn36O8BBP0oEemFu+TT59UylngTDwa2eAlX4iYJA5mCjCUZb2x/ZGKAS
```

**Risiko:** Jika Xiaomi validasi `eui` konsisten antar request, kita harus regenerate persis sama dengan frontend. Bisa di-emulasi dengan Playwright (pakai eui dari browser sungguhan) atau di-replicate via JS reverse-engineering.

### 3.3 reCAPTCHA Enterprise (`6LeBM0ocAAAAAEwYcFUjtxpVbs-0rnbSVXBBXmh4`)

- Site key: `6LeBM0ocAAAAAEwYcFUjtxpVbs-0rnbSVXBBXmh4`
- Flow: `data` (Xiaomi) → `reload` (Google) → `userverify` (Google) → `clr` (Google) → `recaptcha/verify` (Xiaomi)
- Captcha token dipakai sebagai `icode` di body `sendEmailRegTicket`.

**Opsi bypass:**
- (a) Pakai layanan solver: 2Captcha, Anti-Captcha, CapSolver (~$3/1000 solve) — andalkan.
- (b) Pakai Playwright + extension yang solve otomatis (lebih mahal/rapuh).
- (c) Cari bug/heuristik Xiaomi untuk region tertentu (mis. tanpa captcha untuk low-risk IP) — tidak reliable.

### 3.4 Xiaomi captcha proxy

`verify.sec.xiaomi.com/captcha/v2/data` dan `/captcha/v2/recaptcha/verify` — field `s`, `d` juga terenkripsi. Kemungkinan menggunakan key yang sama dengan `eui` atau derivatif.

### 3.5 `deviceFingerprint`

MD5 32-char hash: `597117090e887e57b19386c3c54a0a8d`. Berbeda dari `eui`. Mungkin = `md5(userAgent + screen + timezone + …)`. Harus stabil per-device.

### 3.6 Header khusus browser Xiaomi

```
x-browser-channel
x-browser-copyright
x-browser-validation
x-browser-year
```

Ditambahkan oleh Xiaomi browser/custom UA. Untuk Chrome biasa, header ini **tidak muncul** di HAR ini — kemungkinan di-inject JS hanya saat pakai Xiaomi internal browser. Untuk otomasi pakai Chrome, mungkin tidak wajib.

### 3.7 Header wajib yang harus di-set (Chrome biasa)

| Header | Value |
|---|---|
| `user-agent` | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36` |
| `accept` | `application/json, text/plain, */*` |
| `accept-language` | `en-US,en;q=0.9` |
| `x-requested-with` | `XMLHttpRequest` |
| `referer` | `https://global.account.xiaomi.com/fe/service/register?_locale=en&_uRegion=ID` |
| `origin` | `https://global.account.xiaomi.com` |
| `sec-ch-ua` | `"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"` |
| `sec-fetch-mode` | `cors` |
| `sec-fetch-site` | `same-origin` |
| `eui` | (generated, lihat §3.2) |

---

## 4. Spesifikasi Fungsional

### 4.1 CLI

```bash
mimo-register \
  --email-prefix "fauzi+test" \
  --email-domain "gmail.com" \
  --password "RandomPass!2026" \
  --region ID \
  --captcha-provider 2captcha \
  --captcha-key $CAPTCHA_KEY \
  --imap-host imap.gmail.com \
  --imap-user "fauzi@gmail.com" \
  --imap-pass $IMAP_PASS \
  --out ./out/account.json
```

### 4.2 Output

```json
{
  "email": "fauzi+test-1693@gmail.com",
  "password": "RandomPass!2026",
  "userId": "6878925911",
  "region": "ID",
  "createdAt": "2026-06-21T03:23:00Z",
  "cookies": {
    "serviceToken": "...",
    "cUserId": "..."
  },
  "session": {
    "eui": "...",
    "deviceFingerprint": "..."
  },
  "nextSteps": {
    "loginMiMo": "https://platform.xiaomimimo.com/...",
    "stsCallback": "https://platform.xiaomimimo.com/sts?sign=..."
  }
}
```

### 4.3 Modul

| Modul | Tanggung jawab |
|---|---|
| `crypto.py` | AES encrypt/decrypt `email` & `password` (key dari §3.1). |
| `captcha.py` | Integrasi 2Captcha / CapSolver untuk `icode` reCAPTCHA Enterprise. |
| `mail.py` | IMAP listener — fetch email dari Gmail, parse OTP 6-digit, timeout 60s. |
| `eui.py` | Generate `eui` header (lihat §3.2). |
| `fingerprint.py` | Generate `deviceFingerprint` MD5. |
| `register.py` | Orkestrasi: §5 step 1-7. |
| `sts_login.py` | Setelah akun dibuat: replay flow OAuth ke `platform.xiaomimimo.com/sts` (lihat PRD lama §4). |
| `cli.py` | Typer CLI. |

### 4.4 Skema respons (diperlukan konfirmasi — HAR tidak sertakan body)

Berdasarkan pola response Xiaomi pada umumnya:

```json
// POST /pass/sendEmailRegTicket (sukses, #6)
{ "code": 0, "description": "success", "data": { "ticket": "418020", "expire": 600, "templateId": "CI93714_EM_153" } }

// POST /pass/sendEmailRegTicket (gagal, #0)
{ "code": 70022, "description": "Need captcha verification", "data": { "captchaUrl": "..." } }

// POST /pass/verifyEmailRegTicket (sukses, #8)
{ "code": 0, "description": "success", "data": { "userId": "6878925911", "serviceToken": "...", "cUserId": "...", "location": "/fe/service/account?userId=6878925911" } }

// POST /pass/verifyEmailRegTicket (gagal)
{ "code": 70003, "description": "Invalid or expired ticket" }
```

*Nilai code di atas placeholder — HAR tidak merekam body response.*

---

## 5. Alur Detail (state machine)

```
START
  │
  ▼
S1. PREPARE
    - Resolve captcha site key (hard-coded atau dari /pass2/config)
    - Hit endpoint device info init untuk seed `eui` (lihat §3.2)
    - Open IMAP listener untuk email OTP
    - Sleep random 0.5-1.5s (jitter)
  │
  ▼
S2. ATTEMPT_SEND_TICKET
    POST /pass/sendEmailRegTicket (body tanpa icode)
    │
    ├── 200 + code==0  ──►  ke S6 (lompatan — tidak realistis tanpa captcha)
    ├── 200 + code==70022 (Need captcha) ──► ke S3
    └── lain ──► retry 3x, lalu FAIL
  │
  ▼
S3. SOLVE_CAPTCHA
    a. POST /captcha/v2/data   (encrypted s,d,a=register)
    b. Solve reCAPTCHA Enterprise via 2Captcha (90-120s)
    c. POST /captcha/v2/recaptcha/verify  (dengan token)
    d. Response → `icode`
    │
    └── jika gagal ──► FAIL dengan biaya captcha tercatat
  │
  ▼
S4. SEND_TICKET (ulang dengan icode)
    POST /pass/sendEmailRegTicket (body + icode)
    │
    ├── 200 + code==0 + ticket + templateId ──► S5
    ├── 200 + code==rate_limit ──► backoff 30s, ulangi S3
    └── lain ──► FAIL
  │
  ▼
S5. WAIT_OTP
    IMAP listener: cari email dari `*@xiaomi.com` / `noreply@account.xiaomi.com`
    subject berisi "verification code" / "验证码"
    Body parse regex `(\d{6})`
    Timeout 120s
    │
    └── timeout ──► ticket expired, FAIL (tidak retry — quota SMS sudah terpakai)
  │
  ▼
S6. VERIFY_TICKET
    POST /pass/verifyEmailRegTicket {ticket, email, password, region, env, qs, isAcceptLicense, policyName, deviceFingerprint, ...}
    │
    ├── 200 + code==0 + data.userId ──► S7 (akun berhasil dibuat)
    ├── 200 + code==70003 (invalid ticket) ──► FAIL
    └── lain ──► retry 1x lalu FAIL
  │
  ▼
S7. POST_REGISTER_FETCH (best-effort, non-blocking)
    GET /pass2/config
    GET /pass2/security/home?userId={userId}
    Simpan cookies sesi jika ada
  │
  ▼
S8. REDIRECT_TO_MIMO (opsional, lihat PRD lama §4)
    GET platform.xiaomimimo.com/sts?sign=...&followup=...
    [Tahap ini butuh HAR kedua — lihat PRD lama]
  │
  ▼
DONE
```

---

## 6. Risiko & Mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| **AES key berubah tiap deploy Xiaomi** | Enkripsi invalid → semua request gagal | Monitor error rate, fallback ke Playwright untuk harvest `eui` & key real-time. |
| **reCAPTCHA Enterprise update** | Solver gagal | Multi-provider (2Captcha + CapSolver + Anti-Captcha), auto-failover. |
| **IP / device reputation rendah** | Captcha selalu muncul, atau hard block | Gunakan residential proxy (region-matched), rotasi per akun. |
| **Rate limit SMS / email** | Akun berikutnya gagal | Limit max 5 akun / jam / IP. Jeda 5-10 menit antar akun. |
| **IMAP Gmail sering butuh app password** | Listener OTP mati | Pakai akun IMAP khusus (bukan akun utama). Setup App Password. |
| **Cookie `eui` harus konsisten** | Xiaomi deteksi anomali → ban | Selalu generate eui dari Playwright session, reuse untuk semua request dalam 1 akun. |
| **2FA / phone verification muncul acak** | Flow blocked | Phase 1: skip akun yang trigger 2FA; report sebagai `requires_phone`. |
| **Etika / ToS Xiaomi** | Akun di-ban permanen | Jangan dipakai untuk spam/abuse. Hanya untuk akun pribadi/dev. |

---

## 7. Acceptance Criteria

1. CLI `mimo-register` berhasil membuat akun baru end-to-end (email unik + OTP via IMAP) dalam < 5 menit untuk akun pertama.
2. Akun baru dapat login ke `account.xiaomi.com` (cek via GET `/pass2/security/home?userId=...`).
3. Akun baru dapat di-redirect ke `platform.xiaomimimo.com` dan muncul halaman profile (kolom `agreement:false`).
4. Mode batch mampu membuat 5 akun berurutan tanpa banned / hard block.
5. Semua secret (password, captcha-key, imap-pass) **tidak pernah** muncul di log.
6. Rekam HAR dari hasil otomasi, bandingkan dengan HAR asli — semua endpoint identik.
7. Test unit (`pytest`) lulus tanpa panggilan jaringan nyata (mock crypto + captcha).

---

## 8. Stack & Struktur

**Bahasa:** Python 3.11+.

**Dependensi utama:**
- `httpx[http2]` — HTTP client.
- `tenacity` — retry.
- `cryptography` — AES.
- `pydantic` v2 — schema.
- `typer` — CLI.
- `imap-tools` — IMAP listener.
- `playwright` — fallback untuk harvest `eui` / key real-time.
- `pytest`, `pytest-asyncio`, `respx` (mock httpx).

**Struktur:**

```
mimo-register/
├── pyproject.toml
├── README.md
├── PRD.md                              ← file ini
├── src/mimo/
│   ├── __init__.py
│   ├── crypto.py                       # AES email/password
│   ├── eui.py                          # generate eui header
│   ├── fingerprint.py                  # md5 deviceFingerprint
│   ├── captcha/
│   │   ├── base.py
│   │   ├── twocaptcha.py
│   │   └── capsolver.py
│   ├── mail/
│   │   └── imap_listener.py
│   ├── endpoints/
│   │   ├── send_email_reg_ticket.py
│   │   ├── verify_email_reg_ticket.py
│   │   ├── sms_quota.py
│   │   └── pass2_config.py
│   ├── register.py                     # orchestrator (state machine §5)
│   ├── sts_login.py                    # PRD-lama §4 OAuth ke platform
│   ├── models.py
│   ├── cli.py
│   └── utils.py
├── tests/
│   ├── fixtures/
│   │   └── har_responses.json          # mock body response
│   ├── test_crypto.py
│   ├── test_register.py
│   └── test_e2e.py
├── scripts/
│   ├── harvest_eui.py                  # Playwright: ambil eui & JS-loaded key
│   └── parse_har.py                    # util debug
└── accounts.example.json
```

---

## 9. Open Questions (wajib dijawab sebelum implementasi)

1. **AES key & algorithm:**从哪里 (从哪里=from where in JS Xiaomi)? Butuh breakpoint DevTools saat submit form. *(Sumber paling mungkin: `static/js/encrypt.min.js` atau di-bundle dengan chunk utama.)*
2. **Captcha policy per region:** Apakah region `ID` selalu butuh captcha, atau ada trigger tertentu (cookie, IP)?
3. **Apakah response code 70022 eksak, atau bentuk lain?** HAR tidak simpan body — perlu rekam ulang dengan "Save all as HAR with content".
4. **`x-browser-*` headers** wajib atau opsional? Chrome biasa tidak kirim header ini; Xiaomi-only browser yang kirim.
5. **Setelah akun dibuat, apakah Xiaomi redirect otomatis ke `platform.xiaomimimo.com`?** Di HAR ini redirect ke `account.xiaomi.com/fe/service/account?userId=...` (security setup). STS ke platform mungkin terjadi belakangan.
6. **Apakah perlu simpan `serviceToken` & `cUserId`?** Jika ya, dari response body request mana?
7. **Batas aman create akun per hari per IP?** Eksperimen atau lihat dokumentasi Xiaomi (kemungkinan tidak ada publik).
8. **Apakah ada `deviceId` long-term yang dikirim via header `eui`?** Berapa lama TTL-nya?

---

## 10. Lampiran

### A. Endpoints lengkap (HAR `regis only account.xiaomi.com.har`)

| Endpoint | Method | Tujuan |
|---|---|---|
| `global.account.xiaomi.com/pass/sendEmailRegTicket` | POST | Kirim OTP ke email |
| `global.account.xiaomi.com/pass/verifyEmailRegTicket` | POST | Submit OTP → buat akun |
| `global.account.xiaomi.com/pass/sms/quota` | POST | Cek quota (email/SMS) |
| `verify.sec.xiaomi.com/captcha/v2/data` | POST | Minta challenge captcha Xiaomi |
| `verify.sec.xiaomi.com/captcha/v2/recaptcha/verify` | POST | Validasi token captcha |
| `www.google.com/recaptcha/enterprise/reload` | POST | reCAPTCHA Enterprise reload |
| `www.google.com/recaptcha/enterprise/userverify` | POST | reCAPTCHA Enterprise verify |
| `www.google.com/recaptcha/enterprise/clr` | POST | reCAPTCHA Enterprise cleanup |
| `account.xiaomi.com/pass2/config?key=login&key=register&_locale=en&sid=passport` | GET | Config post-register |
| `account.xiaomi.com/pass2/security/home?userId={userId}&bizFlag=` | GET | Halaman security setup |
| `firebase.googleapis.com/v1alpha/projects/-/apps/{appId}/webConfig` | GET | Firebase web config |
| `firebaseinstallations.googleapis.com/v1/projects/xiaomiaccount/installations` | POST | Register Firebase installation |
| `www.google-analytics.com/g/collect?v=2&tid=G-XWN774PE8J` | POST | GA4 telemetry |

### B. Konstanta yang ditemukan

```
reCAPTCHA site key       : 6LeBM0ocAAAAAEwYcFUjtxpVbs-0rnbSVXBBXmh4
Xiaomi captcha app key   : 8027422fb0eb42fbac1b521ec4a7961f
region HAR              : ID (Indonesia)
locale HAR              : en
i18n locale HAR         : en_US
Firebase project        : xiaomiaccount
Firebase appId          : 1:819836638382:web:5cf09e08e726391857c93f
GA4 measurement ID      : G-XWN774PE8J
```

### C. Pola body terenkripsi (HAR #0, contoh email `rexatara62@gmail.com`)

```
email    (plaintext):  rexatara62@gmail.com
email    (encrypted):  mbv+69fBfat9loMLV4hQvVuMjEczD/FfSUjpZs6ruGI=    (24 bytes ciphertext → AES-128/192/256 dengan output 24)
password (encrypted):  aV8lvxx/alnPM+vF2yg1WA==                         (16 bytes ciphertext → AES dengan output 16)
```

Catatan: panjang ciphertext = panjang plaintext + IV (16 byte untuk AES-CBC). Jadi kemungkinan besar AES-CBC dengan IV di-prefix atau di-suffix ciphertext.