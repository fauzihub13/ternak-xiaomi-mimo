# Setup Catch-All Email — Cloudflare Email Routing

Pakai Cloudflare Email Routing (free, built-in kalau DNS di Cloudflare).
Tidak butuh provider ketiga (ImprovMX, Zoho, dll).

## Langkah 1 — Aktifkan Email Routing

1. Login ke https://dash.cloudflare.com
2. Pilih domain Anda
3. Klik menu **Email** → **Email Routing**
4. Klik **"Get started"** atau **"Enable Email Routing"**
5. Cloudflare akan otomatis menambahkan **MX records** yang dibutuhkan

Kalau MX records-nya tidak auto-add, tambahkan manual:

| Type | Name | Mail server | Priority | TTL |
|---|---|---|---|---|
| MX | `@` | `route1.mx.cloudflare.net` | 39 | Auto |
| MX | `@` | `route2.mx.cloudflare.net` | 38 | Auto |
| MX | `@` | `route3.mx.cloudflare.net` | 37 | Auto |

⚠️ **Hapus MX record lama** kalau ada (mis. dari hosting lama) — bentrok.

## Langkah 2 — Tambah Destination Address (Gmail Anda)

1. Di halaman Email Routing, tab **"Destinations"**
2. Klik **"Add destination"**
3. Isi alamat Gmail Anda (mis. `email.anda@gmail.com`)
4. Klik **"Add"**
5. Cloudflare kirim email verifikasi ke Gmail Anda → buka → klik link konfirmasi
6. Status destination jadi **"Verified"** ✅

## Langkah 3 — Setup Catch-All Rule

1. Tab **"Routes"** atau **"Routing rules"**
2. Klik **"Create address"** atau **"Catch-all address"**
3. Custom address: **kosongkan** atau isi `*`
4. Action: **"Send to an email"** → pilih destination Gmail Anda
5. Klik **"Save"**

Sekarang SEMUA email ke `apapun@domain-anda.com` akan diteruskan ke Gmail Anda.

## Langkah 4 — Test

Kirim email manual ke `test123@domain-anda.com` dari akun Gmail lain.
Kalau masuk ke inbox Gmail Anda dalam <30 detik → setup OK.

Cek juga **spam folder** Gmail kalau tidak masuk.

## Langkah 5 — Gmail App Password (untuk IMAP)

Gmail **tidak izinkan** login pakai password biasa ke IMAP — harus App Password.

1. Buka https://myaccount.google.com/security
2. Aktifkan **2-Step Verification** (kalau belum)
3. Buka https://myaccount.google.com/apppasswords
4. Pilih App: **"Mail"**, Device: **"Other (custom name)"** → ketik `mimo-register`
5. Klik **"Generate"**
6. Copy 16-character password (mis. `abcd efgh ijkl mnop`)
   - Spasi diabaikan — bisa disimpan sebagai `abcdefghijklmnop`

## Langkah 6 — Update `.env`

```ini
# Email yang akan didaftarkan (catch-all domain Anda)
EMAIL=auto-generated@domain-anda.com

# IMAP untuk membaca OTP — pakai Gmail Anda (BUKAN domain catch-all)
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=email.anda@gmail.com
IMAP_PASS=abcdefghijklmnop   # App Password dari langkah 5
```

## Langkah 7 — Jalankan

```bash
# Test 1 akun dulu
python -m mimo.batch --count 1 \
  --email-strategy catch_all \
  --email-domain domain-anda.com

# Kalau sukses, batch penuh
python -m mimo.batch --count 10 \
  --email-strategy catch_all \
  --email-domain domain-anda.com \
  --delay-min 300 --delay-max 900 \
  --out accounts.jsonl
```

## Troubleshooting

### Email tidak masuk ke Gmail
- Cek MX record: `dig MX domain-anda.com` (harus return cloudflare.net)
- Cek spam folder Gmail
- Cek Cloudflare dashboard → Email → Routes → log

### IMAP login gagal
- Pastikan pakai **App Password**, bukan password Gmail biasa
- Pastikan 2-Step Verification aktif
- Test manual: `python3 -c "import imaplib; imap = imaplib.IMAP4_SSL('imap.gmail.com', 993); imap.login('user@gmail.com', 'app_password'); print('OK')"`

### Rate limit Xiaomi ("terlalu sering")
- Naikkan `--delay-min` ke 600s (10 menit)
- Cek apakah IP Anda kena blok (lihat log error)

### Email `+` di-block Xiaomi
- Pakai strategi catch_all (BUKAN gmail_plus)
- Email seperti `abc123@domain.com` lebih aman

## Limit Cloudflare Email Routing (free tier)

| Limit | Nilai |
|---|---|
| Email diteruskan per hari | **Tidak ada batas resmi**, tapi praktis ~ribuan |
| Destination addresses | **200** verified addresses |
| Catch-all rule | 1 per domain |
| Biaya | $0 (sudah termasuk semua fitur) |

Untuk 5-50 akun/hari, free tier lebih dari cukup.