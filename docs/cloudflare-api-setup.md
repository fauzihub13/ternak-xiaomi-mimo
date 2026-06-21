# Cloudflare Email Routing — API Setup

Setup catch-all via Cloudflare API (no dashboard clicks).

## Keuntungan vs Dashboard

- ✅ Bulk setup untuk banyak domain sekaligus
- ✅ Idempotent (cocok untuk CI/CD / re-run aman)
- ✅ Versionable (commit ke git)
- ✅ Auto-discover zone ID & account ID

## 1. Buat API Token

1. Login ke https://dash.cloudflare.com
2. Buka **My Profile** → **API Tokens** → **Create Token**
3. Pilih **Custom Token** (bukan template)
4. Set permission berikut:

| Scope | Permission |
|---|---|
| Account → Account Resources → Account | **Edit** |
| Zone → Zone Resources → Specific zone → `<domain-anda>` | Read |
| Zone → Email Routing | **Edit** |

5. Klik **Continue to summary** → **Create Token**
6. Copy token (ditampilkan sekali) — simpan sebagai `CF_API_TOKEN`

## 2. Dapatkan Zone ID & Account ID

Opsi A — manual dari dashboard:
- Klik kanan domain di overview → **Copy Zone ID** → `CF_ZONE_ID`
- Klik avatar pojok kanan atas → pilih **Account ID** di bagian bawah → `CF_ACCOUNT_ID`

Opsi B — via API:
```bash
CF_API_TOKEN=<token> python -m mimo.cf_setup --token <token> --list-zones
```

## 3. Update `.env`

```ini
CF_API_TOKEN=your_token_here
CF_ZONE_ID=your_zone_id_here
CF_ACCOUNT_ID=your_account_id_here     # optional — auto-detect
CF_DEST_EMAIL=email.anda@gmail.com
```

## 4. CLI commands

### Setup catch-all (idempotent)

```bash
# With explicit zone_id
mimo-cf-setup --token $CF_API_TOKEN --zone-id $CF_ZONE_ID \
  --dest $CF_DEST_EMAIL

# With domain name (auto-lookup zone_id)
mimo-cf-setup --token $CF_API_TOKEN --domain mimo.domain-anda.com \
  --dest $CF_DEST_EMAIL

# Dry run
mimo-cf-setup --token $CF_API_TOKEN --domain mimo.domain-anda.com \
  --dest $CF_DEST_EMAIL --dry-run
```

Output:
```
[*] setup catch-all → email.anda@gmail.com
  creating destination: email.anda@gmail.com…
  ✓ destination created (cek email untuk verifikasi)
  ⚠ destination belum verified — buka email email.anda@gmail.com dan klik link
     Setelah verified, run ulang script ini untuk create catch-all rule

# Setelah verifikasi email, run ulang:
[*] setup catch-all → email.anda@gmail.com
  ✓ destination already exists: email.anda@gmail.com (verified=True)
  creating catch-all rule → email.anda@gmail.com…
  ✓ catch-all rule created: id=abc123
```

### Status

```bash
mimo-cf-setup --token $CF_API_TOKEN --zone-id $CF_ZONE_ID --status
```

Output:
```
============================================================
Cloudflare Email Routing — Status
============================================================
Zone ID   : abc123
Account ID: xyz789

Destinations:
  ✓ email.anda@gmail.com  (created=2026-06-21)

Routing rules:
  ✓ [0] Catch-all to Gmail  match: all=  → forward=['email.anda@gmail.com']
============================================================
```

### List zones

```bash
mimo-cf-setup --token $CF_API_TOKEN --list-zones
```

## 5. Test end-to-end

Setelah setup OK:
```bash
# Test IMAP + catch-all
python -m mimo.setup_test

# Test register 1 akun
python -m mimo.batch --count 1 --email-strategy catch_all \
  --email-domain mimo.domain-anda.com
```

## API Endpoints (untuk debugging)

| Method | URL | Fungsi |
|---|---|---|
| GET | `/zones/{zone_id}/email/routing/rules` | List semua rules |
| POST | `/zones/{zone_id}/email/routing/rules` | Create rule |
| DELETE | `/zones/{zone_id}/email/routing/rules/{id}` | Delete rule |
| GET | `/accounts/{account_id}/email/routing/addresses` | List destinations |
| POST | `/accounts/{account_id}/email/routing/addresses` | Create destination |

curl example:
```bash
TOKEN=your_token
ZONE=your_zone_id
ACC=your_account_id

# List rules
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE/email/routing/rules" | jq

# List destinations
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$ACC/email/routing/addresses" | jq
```

## Limit (Free tier)

| Limit | Nilai |
|---|---|
| Destinations | 200 verified addresses |
| Routing rules | Unlimited |
| Email diteruskan per hari | Tidak ada batas resmi (untuk batch 5-50 akun/hari aman) |
| Cost | $0 |

## Troubleshooting

### 403 Forbidden
- API token tidak punya permission. Buat ulang dengan permission benar.
- Zone ID salah (token hanya untuk zone tertentu).

### "destination belum verified"
- Cek inbox Gmail Anda
- Klik link verifikasi dari Cloudflare (subject: "Verify your email address")
- Run ulang `mimo-cf-setup` setelah verified

### MX record belum propagate
- Cloudflare otomatis add MX records, tapi propagasi DNS sampai 24 jam
- Test: `dig MX domain-anda.com` harus return `cloudflare.net`