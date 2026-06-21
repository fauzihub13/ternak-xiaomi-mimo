"""CLI kecil untuk encrypt field & lihat output (debug / verifikasi)."""

import json
import sys

from mimo.crypto import encrypt_form_fields, encrypt_captcha_payload


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print('  mimo-encrypt \'{"email":"x@y.com","password":"P@ss"}\'   # EUI + encrypted params')
        print('  mimo-encrypt payload <json>                              # captcha payload → s/d')
        sys.exit(1)

    if sys.argv[1] == "payload":
        if len(sys.argv) < 3:
            print("Usage: mimo-encrypt payload '<json>'")
            sys.exit(1)
        payload = json.loads(sys.argv[2])
        s, d = encrypt_captcha_payload(payload)
        print(json.dumps({"s": s, "d": d}, indent=2))
        return

    fields = json.loads(sys.argv[1])
    out = encrypt_form_fields(fields)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()