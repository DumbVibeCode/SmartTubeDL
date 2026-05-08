"""
Extract Chrome cookies directly from the locked SQLite database.

Uses sqlite3's immutable=1 URI mode to read Chrome's Cookies file
even while Chrome is running (bypasses the file lock).
Decrypts cookie values (DPAPI + AES-GCM for v10/v11; skips v20 App-Bound).
Writes a Netscape-format temp file for yt-dlp.
"""
import os
import sys
import json
import sqlite3
import base64
import tempfile

from logger import log_message


def _chrome_local_state_path():
    if sys.platform != 'win32':
        return None
    return os.path.join(
        os.environ.get('LOCALAPPDATA', ''),
        'Google', 'Chrome', 'User Data', 'Local State'
    )


def _chrome_cookies_path():
    if sys.platform != 'win32':
        return None
    base = os.path.join(
        os.environ.get('LOCALAPPDATA', ''),
        'Google', 'Chrome', 'User Data', 'Default'
    )
    for sub in ('Network', ''):
        p = os.path.join(base, sub, 'Cookies') if sub else os.path.join(base, 'Cookies')
        if os.path.exists(p):
            return p
    return None


def _get_master_key():
    """Read and decrypt Chrome's AES master key from Local State via DPAPI."""
    try:
        ls_path = _chrome_local_state_path()
        if not ls_path or not os.path.exists(ls_path):
            return None
        with open(ls_path, 'r', encoding='utf-8') as f:
            ls = json.load(f)
        enc_key_b64 = ls.get('os_crypt', {}).get('encrypted_key')
        if not enc_key_b64:
            return None
        enc_key = base64.b64decode(enc_key_b64)
        if enc_key[:5] != b'DPAPI':
            return None
        enc_key = enc_key[5:]
        import ctypes
        import ctypes.wintypes
        # Use CryptUnprotectData via ctypes (avoids pywin32 dependency)
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [('cbData', ctypes.wintypes.DWORD),
                        ('pbData', ctypes.POINTER(ctypes.c_char))]
        buf = ctypes.create_string_buffer(enc_key)
        blob_in = DATA_BLOB(len(enc_key), buf)
        blob_out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None, 0,
                ctypes.byref(blob_out)):
            master_key = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
            return master_key
    except Exception as e:
        log_message(f"cookies_helper: не удалось прочитать master key: {e}")
    return None


def _decrypt_value(encrypted_value: bytes, master_key: bytes | None) -> str:
    if not encrypted_value:
        return ''
    prefix = encrypted_value[:3]
    if prefix in (b'v10', b'v11'):
        if master_key is None:
            return ''
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            nonce = encrypted_value[3:15]
            ciphertext = encrypted_value[15:]
            return AESGCM(master_key).decrypt(nonce, ciphertext, None).decode('utf-8', errors='replace')
        except Exception:
            return ''
    elif prefix == b'v20':
        # App-Bound Encryption (Chrome 127+) — needs Chrome process to decrypt
        return ''
    else:
        # Legacy DPAPI-encrypted (no v1x prefix)
        try:
            import ctypes
            import ctypes.wintypes
            class DATA_BLOB(ctypes.Structure):
                _fields_ = [('cbData', ctypes.wintypes.DWORD),
                             ('pbData', ctypes.POINTER(ctypes.c_char))]
            buf = ctypes.create_string_buffer(encrypted_value)
            blob_in = DATA_BLOB(len(encrypted_value), buf)
            blob_out = DATA_BLOB()
            if ctypes.windll.crypt32.CryptUnprotectData(
                    ctypes.byref(blob_in), None, None, None, None, 0,
                    ctypes.byref(blob_out)):
                result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
                ctypes.windll.kernel32.LocalFree(blob_out.pbData)
                return result.decode('utf-8', errors='replace')
        except Exception:
            pass
        return ''


def extract_chrome_cookies_netscape(domains: list[str] | None = None) -> str | None:
    """
    Returns path to a Netscape-format cookies.txt with Chrome cookies.
    Caller is responsible for deleting the file after use.
    `domains`: list of domain substrings to filter (e.g. ['youtube.com', 'google.com']).
                None = all cookies.
    """
    cookies_path = _chrome_cookies_path()
    if not cookies_path:
        log_message("cookies_helper: файл Chrome Cookies не найден")
        return None

    master_key = _get_master_key()

    # immutable=1 lets us read the file even while Chrome holds a lock on it
    uri = 'file:///' + cookies_path.replace('\\', '/').replace(' ', '%20') + '?mode=ro&immutable=1'
    try:
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        log_message(f"cookies_helper: не удалось открыть Cookies DB: {e}")
        return None

    try:
        if domains:
            rows = []
            for domain in domains:
                rows.extend(conn.execute(
                    "SELECT host_key, path, is_secure, expires_utc, name, "
                    "       value, encrypted_value, is_httponly "
                    "FROM cookies WHERE host_key LIKE ?",
                    (f'%{domain}%',)
                ).fetchall())
        else:
            rows = conn.execute(
                "SELECT host_key, path, is_secure, expires_utc, name, "
                "       value, encrypted_value, is_httponly FROM cookies"
            ).fetchall()
    except Exception as e:
        log_message(f"cookies_helper: ошибка запроса к Cookies DB: {e}")
        conn.close()
        return None
    finally:
        conn.close()

    if not rows:
        log_message("cookies_helper: куки не найдены")
        return None

    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='_chrome_cookies.txt', delete=False, encoding='utf-8'
    )
    tmp.write("# Netscape HTTP Cookie File\n")
    written = 0
    skipped_encrypted = 0

    for row in rows:
        value = row['value']
        if not value and row['encrypted_value']:
            value = _decrypt_value(bytes(row['encrypted_value']), master_key)
        if not value:
            skipped_encrypted += 1
            continue

        # Chrome stores expiry as microseconds since Windows epoch (1601-01-01)
        expires_utc = row['expires_utc'] or 0
        expires_unix = max(0, (expires_utc - 11_644_473_600_000_000) // 1_000_000) if expires_utc else 0

        host = row['host_key']
        include_subdomains = 'TRUE' if host.startswith('.') else 'FALSE'
        secure = 'TRUE' if row['is_secure'] else 'FALSE'
        prefix = '#HttpOnly_' if row['is_httponly'] else ''

        tmp.write(
            f"{prefix}{host}\t{include_subdomains}\t{row['path']}\t"
            f"{secure}\t{expires_unix}\t{row['name']}\t{value}\n"
        )
        written += 1

    tmp.close()

    log_message(f"cookies_helper: записано {written} куков, пропущено (App-Bound/зашифровано): {skipped_encrypted}")
    if written == 0:
        os.unlink(tmp.name)
        return None

    return tmp.name
