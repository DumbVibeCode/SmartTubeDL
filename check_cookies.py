import os, sys, ctypes, ctypes.wintypes, sqlite3, tempfile

src = os.path.join(os.environ['LOCALAPPDATA'],
                   'Google','Chrome','User Data','Default','Network','Cookies')
print(f"Источник: {src}")
print(f"Существует: {os.path.exists(src)}")

# Копируем через Windows API
GENERIC_READ          = 0x80000000
FILE_SHARE_READ       = 0x00000001
FILE_SHARE_WRITE      = 0x00000002
FILE_SHARE_DELETE     = 0x00000004
OPEN_EXISTING         = 3
FILE_ATTRIBUTE_NORMAL = 0x80

k32 = ctypes.windll.kernel32
h = k32.CreateFileW(src, GENERIC_READ,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)

INVALID = ctypes.wintypes.HANDLE(-1).value
print(f"Handle: {h}  (INVALID={INVALID})")
print(f"LastError: {k32.GetLastError()}")

if h == INVALID or h == 0:
    print("Не удалось открыть файл через WinAPI")
    sys.exit(1)

tmp = tempfile.mktemp(suffix='.db')
buf = ctypes.create_string_buffer(256 * 1024)
read = ctypes.wintypes.DWORD(0)
total = 0
with open(tmp, 'wb') as f:
    while True:
        ok = k32.ReadFile(h, buf, len(buf), ctypes.byref(read), None)
        if not ok or read.value == 0:
            break
        f.write(buf.raw[:read.value])
        total += read.value
k32.CloseHandle(h)
print(f"Скопировано байт: {total}  ->  {tmp}")

if total == 0:
    print("Файл пустой — ошибка копирования")
    sys.exit(1)

# Открываем копию
conn = sqlite3.connect(tmp)
total_c = conn.execute('SELECT count(*) FROM cookies').fetchone()[0]
print(f"\nВсего куков: {total_c}")
yt = conn.execute("SELECT count(*) FROM cookies WHERE host_key LIKE '%youtube%'").fetchone()[0]
print(f"YouTube куков: {yt}")
print("\nПримеры (name, len(value), len(enc_value)):")
for r in conn.execute("SELECT name, length(value), length(encrypted_value) FROM cookies WHERE host_key LIKE '%youtube%' LIMIT 5"):
    print(" ", r)
conn.close()
os.unlink(tmp)
