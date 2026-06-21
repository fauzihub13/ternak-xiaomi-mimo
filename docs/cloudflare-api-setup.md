# Cloudflare Email Routing — API Setup

Setup catch-all via Cloudflare API. **2 cara**:

- **Cara A (RECOMMENDED): API Token** — scoped, aman, expiration
- **Cara B: Global API Key** — full access, lebih gampang setup, less secure

---

## Cara A — API Token (Recommended)

### 1. Buat API Token

1. Login ke https://dash.cloudflare.com
2. **My Profile** → **API Tokens** → **Create Token**
3. Klik **Custom Token** (atau pakai template "Edit zone DNS" lalu tambahkan permission)

### 2. Pilih Permissions

Di bagian **Permissions**, klik **+ Add More** dan pilih 3 permission berikut:

| Category | Permission | Access |
|---|---|---|
| **Account** | Email Routing Addresses | Edit |
| **Zone** | Zone | Read |
| **Zone** | Email Routing Rules | Edit |

⚠️ **Nama persis harus "Email Routing Addresses" + "Email Routing Rules"** (bukan "Email Routing" saja — itu permission lain/tidak ada).

### 3. Set Zone Resources

Di bagian **Zone Resources**, pilih **Include → Specific zone → `<domain-anda>`**.
Kalau Anda punya banyak domain, pilih **All zones** (less secure tapi lebih gampang).

### 4. TTL & Create

- **Client IP Address Filtering**: kosongkan (kecuali mau restrict ke IP tertentu)
- **TTL**: pilih expiration date (mis. 1 tahun)
- Klik **Continue to summary** → **Create Token**
- Copy token (ditampilkan sekali)

### 5. Dapatkan Zone ID

Opsi A — manual:
- Klik kanan domain di overview Cloudflare → **Copy Zone ID**

Opsi B — via API:
```bash
mimo-cf-setup --token $CF_API_TOKEN --list-zones
```

### 6. Update `.env`

```ini
CF_API_TOKEN=your_token_here
CF_ZONE_ID=your_zone_id_here
CF_ACCOUNT_ID=your_account_id_here         # auto-detect kalau kosong
CF_DEST_EMAIL=email.anda@gmail.com
```

### 7. Run Setup

```bash
mimo-cf-setup --token $CF_API_TOKEN --domain mimo.kamu.com --dest gmail.anda@gmail.com
```

---

## Cara B — Global API Key (Fallback, Paling Gampang)

⚠️ **Less secure** — full account access. Jangan pakai untuk production/cron di server publik.

### 1. Dapatkan Global API Key

1. Login https://dash.cloudflare.com
2. Klik **My Profile** → **API Tokens** (scroll ke bawah)
3. Section **API Keys** → **Global API Key** → **View**
4. Masukkan password Cloudflare → Copy key

### 2. Dapatkan Email & Zone ID

**Email Anda** = email login Cloudflare (otomatis terdeteksi).

**Zone ID**: cara yang sama — klik kanan domain di overview → **Copy Zone ID**

### 3. Update `.env`

```ini
CF_API_KEY=your_global_api_key_here        # BUKAN CF_API_TOKEN
CF_EMAIL=email.login.cloudflare.anda@gmail.com
CF_ZONE_ID=your_zone_id
```

### 4. Update `cf_setup.py` untuk support Global API Key

Edit `src/mimo/cf_setup.py`:

```python
def _headers(token_or_key: str, email: str | None = None) -> dict:
    """Bearer token (scoped) atau X-Auth-Email + X-Auth-Key (global)."""
    if email:
        return {
            "X-Auth-Email": email,
            "X-Auth-Key": token_or_key,
            "Content-Type": "application/json",
        }
    return {
        "Authorization": f"Bearer {token_or_key}",
        "Content-Type": "application/json",
    }
```

Atau pakai environment variables:
- `CF_API_KEY` — Global API Key
- `CF_API_TOKEN` — Scoped API Token (preferred)

Dan update CLI untuk handle keduanya.

---

## Setup via Dashboard (Manual, Tanpa API)

Kalau API benar-benar tidak memungkinkan:

1. Cloudflare dashboard → pilih domain → **Email** → **Email Routing** → **Enable**
2. Tab **Destinations** → **Add destination** → isi Gmail → verify via link
3. Tab **Routes** → **Catch-all address** → isi Gmail tujuan → Save

Manual OK untuk 1 domain. Kalau banyak domain, pakai API.

---

## Cek MX Records

Setelah setup, verify MX records pointing ke Cloudflare:

```bash
dig MX domain-anda.com
```

Harus return:
```
domain-anda.com.  300  IN  MX  10 route1.mx.cloudflare.net.
domain-anda.com.  300  IN  MX  20 route2.mx.cloudflare.net.
domain-anda.com.  300  IN  MX  30 route3.mx.cloudflare.net.
```

Kalau masih pointing ke mail server lama (mis. `mx.zoho.com`), tunggu propagasi atau hapus manual.

---

## CLI Commands

```bash
# Setup catch-all (idempotent)
mimo-cf-setup --token $CF_API_TOKEN --domain mimo.kamu.com --dest gmail.anda@gmail.com

# Status check
mimo-cf-setup --token $CF_API_TOKEN --zone-id $CF_ZONE_ID --status

# List semua zone
mimo-cf-setup --token $CF_API_TOKEN --list-zones

# Dry run
mimo-cf-setup --token $CF_API_TOKEN --domain mimo.kamu.com --dest gmail.anda@gmail.com --dry-run
```

---

## Troubleshooting

### 403 Forbidden
- Token tidak punya permission yang dibutuhkan. Buat ulang dengan 3 permission di atas.
- Zone ID salah / token di-restrict ke zone lain.

### "destination belum verified"
- Cek inbox Gmail → klik link verifikasi Cloudflare
- Subject biasanya: "Verify your email address" dari `cloudflare.com`
- Run ulang `mimo-cf-setup` setelah verified

### MX tidak propagate
- Tunggu 5-30 menit (DNS propagation)
- Cek di Cloudflare dashboard → DNS → Records → MX records auto-added

### Permission "Email Routing" tidak ketemu
- **Pastikan ketik "Email Routing Addresses"** (Account) atau **"Email Routing Rules"** (Zone)
- Bukan "Email Routing" saja — tidak ada di list permission

### Global API Key ditolak
- Pastikan CF_EMAIL (email login) benar
- Re-view key kalau lupa (harus re-enter password)