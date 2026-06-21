/**
 * Xiaomi form field encryption - exact browser replica
 * Uses CryptoJS + Node.js crypto (PKCS1v15 RSA)
 */
const CryptoJS = require('crypto-js');
const crypto = require('crypto');

const RSA_PUBKEY = `-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCYEVrK/4Mahiv0pUJgTybx4J9P
5dUT/Y0PuwMbk+gMU+jrZnBiXGv6/hCH1avIhoBcE535F8nJQQN3UavZdFkYidso
XuEnat3+eVTp3FslyhRwIBDF09v4vDhRtxFOT+R7uH7h/mzmyA2/+lfIMWGIrffX
prYizbV76+YQKhoqFQIDAQAB
-----END PUBLIC KEY-----`;

const AES_IV = CryptoJS.enc.Utf8.parse('0102030405060708');
const KEY_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*';

function encryptFormFields(fields) {
    // Generate random 16-char AES key
    let aesKey = '';
    for (let i = 0; i < 16; i++) {
        aesKey += KEY_CHARS[Math.floor(Math.random() * KEY_CHARS.length)];
    }

    const keyWA = CryptoJS.enc.Utf8.parse(aesKey);

    // Encrypt each field with AES-CBC PKCS7
    const encryptedParams = {};
    for (const [name, value] of Object.entries(fields)) {
        encryptedParams[name] = CryptoJS.AES.encrypt(value, keyWA, {
            iv: AES_IV,
            padding: CryptoJS.pad.Pkcs7
        }).toString();
    }

    // RSA encrypt the base64-encoded AES key
    const keyB64 = Buffer.from(aesKey).toString('base64');
    const rsaEncrypted = crypto.publicEncrypt({
        key: RSA_PUBKEY,
        padding: crypto.constants.RSA_PKCS1_PADDING
    }, Buffer.from(keyB64)).toString('base64');

    // EUI = rsa_encrypted_key.base64_field_names
    const fieldNames = Buffer.from(Object.keys(fields).join(',')).toString('base64');
    const eui = rsaEncrypted + '.' + fieldNames;

    return { EUI: eui, encryptedParams };
}

// CLI usage
if (require.main === module) {
    const fields = JSON.parse(process.argv[2]);
    console.log(JSON.stringify(encryptFormFields(fields)));
}

module.exports = { encryptFormFields };
