"""
Extract Chrome cookies for yt-dlp.

Копирует файл Cookies через Windows API с флагами совместного доступа
(работает пока Chrome открыт), расшифровывает v10/v11 через DPAPI+AES-GCM,
пишет Netscape-формат для yt-dlp.
"""
import os
import sys
import json
import sqlite3
import base64
import ctypes
import ctypes.wintypes
import tempfile

from logger import log_message


def _chrome_cookies_path() -> str | None:
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


def _copy_locked_file(src: str, dst: str) -> bool:
    """Копирует файл через Windows API даже если он заблокирован Chrome."""
    GENERIC_READ          = 0x80000000
    FILE_SHARE_READ       = 0x00000001
    FILE_SHARE_WRITE      = 0x00000002
    FILE_SHARE_DELETE     = 0x00000004
    OPEN_EXISTING         = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE        = ctypes.wintypes.HANDLE(-1).value

    k32 = ctypes.windll.kernel32
    h = k32.CreateFileW(
        src,
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None
    )
    if h == INVALID_HANDLE or h == 0:
        log_message(f"cookies_helper: CreateFileW failed, error={k32.GetLastError()}")
        return False
    try:
        buf = ctypes.create_string_buffer(256 * 1024)
        read = ctypes.wintypes.DWORD(0)
        with open(dst, 'wb') as f:
            while True:
                ok = k32.ReadFile(h, buf, len(buf), ctypes.byref(read), None)
                if not ok or read.value == 0:
                    break
                f.write(buf.raw[:read.value])
        return os.path.getsize(dst) > 0
    finally:
        k32.CloseHandle(h)


def _get_master_key() -> bytes | None:
    """Читает AES-мастер-ключ Chrome из Local State через DPAPI."""
    try:
        ls = os.path.join(
            os.environ.get('LOCALAPPDATA', ''),
            'Google', 'Chrome', 'User Data', 'Local State'
        )
        with open(ls, 'r', encoding='utf-8') as f:
            data = json.load(f)
        enc = base64.b64decode(data['os_crypt']['encrypted_key'])
        if enc[:5] != b'DPAPI':
            return None
        enc = enc[5:]

        class BLOB(ctypes.Structure):
            _fields_ = [('cbData', ctypes.wintypes.DWORD),
                        ('pbData', ctypes.POINTER(ctypes.c_char))]

        buf = ctypes.create_string_buffer(enc)
        b_in, b_out = BLOB(len(enc), buf), BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(b_in), None, None, None, None, 0, ctypes.byref(b_out)):
            key = ctypes.string_at(b_out.pbData, b_out.cbData)
            ctypes.windll.kernel32.LocalFree(b_out.pbData)
            return key
    except Exception as e:
        log_message(f"cookies_helper: master key error: {e}")
    return None


def _decrypt(enc: bytes, master_key: bytes | None) -> str:
    if not enc:
        return ''
    pfx = enc[:3]
    if pfx in (b'v10', b'v11'):
        if not master_key:
            return ''
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            return AESGCM(master_key).decrypt(enc[3:15], enc[15:], None).decode('utf-8', errors='replace')
        except Exception:
            return ''
    if pfx == b'v20':
        return ''   # App-Bound Encryption — только Chrome может расшифровать
    # Старый DPAPI без префикса
    try:
        class BLOB(ctypes.Structure):
            _fields_ = [('cbData', ctypes.wintypes.DWORD),
                        ('pbData', ctypes.POINTER(ctypes.c_char))]
        buf = ctypes.create_string_buffer(enc)
        b_in, b_out = BLOB(len(enc), buf), BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(b_in), None, None, None, None, 0, ctypes.byref(b_out)):
            result = ctypes.string_at(b_out.pbData, b_out.cbData)
            ctypes.windll.kernel32.LocalFree(b_out.pbData)
            return result.decode('utf-8', errors='replace')
    except Exception:
        pass
    return ''


def extract_chrome_cookies_netscape(domains: list[str] | None = None) -> str | None:
    src = _chrome_cookies_path()
    if not src:
        log_message("cookies_helper: Chrome Cookies не найден")
        return None

    tmp_db = tempfile.mktemp(suffix='_chrome.db')
    if not _copy_locked_file(src, tmp_db):
        log_message("cookies_helper: не удалось скопировать Cookies")
        return None

    master_key = _get_master_key()

    try:
        conn = sqlite3.connect(tmp_db)
        if domains:
            rows = []
            for d in domains:
                rows += conn.execute(
                    "SELECT host_key,path,is_secure,expires_utc,name,value,encrypted_value,is_httponly "
                    "FROM cookies WHERE host_key LIKE ?", (f'%{d}%',)
                ).fetchall()
        else:
            rows = conn.execute(
                "SELECT host_key,path,is_secure,expires_utc,name,value,encrypted_value,is_httponly FROM cookies"
            ).fetchall()
        conn.close()
    except Exception as e:
        log_message(f"cookies_helper: ошибка чтения БД: {e}")
        try:
            os.unlink(tmp_db)
        except Exception:
            pass
        return None
    finally:
        try:
            os.unlink(tmp_db)
        except Exception:
            pass

    tmp_txt = tempfile.NamedTemporaryFile(
        mode='w', suffix='_cookies.txt', delete=False, encoding='utf-8'
    )
    tmp_txt.write("# Netscape HTTP Cookie File\n")
    written = skipped = 0

    for host, path, secure, expires_utc, name, value, enc_value, httponly in rows:
        if not value and enc_value:
            value = _decrypt(bytes(enc_value), master_key)
        if not value:
            skipped += 1
            continue
        expires = max(0, (expires_utc - 11_644_473_600_000_000) // 1_000_000) if expires_utc else 0
        subdomain = 'TRUE' if host.startswith('.') else 'FALSE'
        pfx = '#HttpOnly_' if httponly else ''
        tmp_txt.write(f"{pfx}{host}\t{subdomain}\t{path}\t"
                      f"{'TRUE' if secure else 'FALSE'}\t{expires}\t{name}\t{value}\n")
        written += 1

    tmp_txt.close()
    log_message(f"cookies_helper: записано {written} куков, пропущено (v20/зашифровано): {skipped}")

    if written == 0:
        os.unlink(tmp_txt.name)
        return None
    return tmp_txt.name
