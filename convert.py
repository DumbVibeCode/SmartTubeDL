import json
import os
import re
import subprocess
from logger import log_message
from config import format_size

def estimate_progress(current_time, total_duration):
    h, m, s = map(float, current_time.split(":"))
    total_seconds = h * 3600 + m * 60 + s
    return min(int((total_seconds / total_duration) * 100), 100)

def get_audio_bitrate(input_file):
    """Определяет битрейт аудио файла"""
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        command = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=bit_rate",
            "-of", "json",
            input_file
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
            universal_newlines=True
        )

        probe_data = json.loads(result.stdout)
        if 'streams' in probe_data and probe_data['streams']:
            audio_bitrate_bps = int(probe_data['streams'][0]['bit_rate'])
            return f"{audio_bitrate_bps // 1000}k"
    except Exception as e:
        log_message(f'Не удалось определить битрейт, используем 192k: {e}')
        return '192k'

def get_audio_duration(input_file):
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", input_file, "-hide_banner"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", result.stderr)
        if match:
            h, m, s = map(float, match.groups())
            return h * 3600 + m * 60 + s
    except Exception as e:
        log_message(f"Ошибка определения длительности аудио: {e}")
    return 180  # 3 минуты по умолчанию

def convert_to_mp3(input_file, update_status_callback):
    """Конвертирует аудио в MP3 с сохранением исходного битрейта"""
    output_file = os.path.splitext(input_file)[0] + '.mp3'
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    try:
        total_duration = get_audio_duration(input_file)
        bitrate = get_audio_bitrate(input_file)
        log_message(f'Битрейт: {bitrate}')

        with subprocess.Popen(
            [
                "ffmpeg",
                "-i", input_file,
                "-threads", "4",
                "-f", "mp3",
                "-acodec", "libmp3lame",
                "-preset", "fast",
                "-b:a", bitrate,
                "-y",
                output_file
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            universal_newlines=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            encoding='utf-8'
        ) as process:

            for line in process.stderr:
                if "time=" in line:
                    match = re.search(r'time=(\d+:\d+:\d+\.\d+)', line)
                    if match:
                        time_value = match.group(1)
                        percent = estimate_progress(time_value, total_duration)
                        update_status_callback("Конвертация...", percent)

            process.wait()

            if process.returncode == 0:
                os.remove(input_file)
                update_status_callback("Готово!", 100)
                log_message(f'Конвертация завершена: {output_file}')
                return output_file
            else:
                update_status_callback("Ошибка!")
                log_message(f"Ошибка при конвертации в MP3: код {process.returncode}")
                return None

    except Exception as e:
        update_status_callback("Ошибка!")
        log_message(f'Ошибка конвертации в MP3: {e}')
        return None

def convert_to_mp4(input_file, update_status_callback):
    output_file = input_file.rsplit(".", 1)[0] + ".mp4"
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    try:
        process = subprocess.run(
            ["ffmpeg", "-i", input_file, "-vcodec", "copy", "-acodec", "copy", "-y", output_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            encoding='utf-8',
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        if process.returncode == 0:
            os.remove(input_file)
            update_status_callback("Готово!", 100)
            log_message(f'Конвертация завершена: {output_file}')
            return output_file
        else:
            update_status_callback("Ошибка!")
            log_message(f"Ошибка при конвертации в MP4: {process.stderr}")
            return None

    except Exception as e:
        update_status_callback("Ошибка!")
        log_message(f"Ошибка конвертации: {e}")
        log_message('Конвертация не удалась')
        return None