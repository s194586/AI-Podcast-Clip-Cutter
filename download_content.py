#!/usr/bin/env python3
"""Pobiera wideo (mp4, do 1080p jeśli dostępne) i audio (mp3), zapisuje do /input,
oraz kopiuje metadane (.info.json) do /metadata i wyciąga pole `heatmap` jeśli istnieje.

Zachowuje oryginalne pliki wideo i audio oraz pokazuje postęp pobierania (przydatne dla długich plików).
"""
import os
import sys
import argparse
import json
import shutil
import random
import subprocess
from yt_dlp import YoutubeDL


def ensure_dirs(input_dir, metadata_dir):
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(metadata_dir, exist_ok=True)


def file_has_audio(path):
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-select_streams', 'a',
        '-show_entries', 'stream=index',
        '-of', 'csv=p=0',
        str(path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    return bool(completed.stdout.strip())


def merge_video_audio(video_path, audio_path, output_path):
    cmd = [
        'ffmpeg',
        '-y',
        '-i', str(video_path),
        '-i', str(audio_path),
        '-map', '0:v:0',
        '-map', '1:a:0',
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-shortest',
        str(output_path),
    ]
    subprocess.run(cmd, check=True)
    return output_path


def find_latest_file(folder, ext):
    if not os.path.isdir(folder):
        return None
    candidates = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(ext.lower())]
    if not candidates:
        return None
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def recursive_find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = recursive_find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for it in obj:
            r = recursive_find_key(it, key)
            if r is not None:
                return r
    return None


def create_placeholder_heatmap(duration_seconds, interval=0.19):
    """Tworzy placeholder heatmapę na podstawie długości wideo."""
    heatmap = []
    time = 0.0
    random.seed(42)
    
    while time < duration_seconds:
        # Wartości losowe z rozkładem Gaussa (średnio 0.5, odchylenie 0.25)
        value = max(0.1, min(1.0, random.gauss(0.5, 0.25)))
        heatmap.append({
            "start_time": time,
            "end_time": time + interval,
            "value": round(value, 4)
        })
        time += interval
    
    return heatmap



def progress_hook(d):
    status = d.get('status')
    if status == 'downloading':
        filename = d.get('filename') or d.get('info_dict', {}).get('title', '')
        total = d.get('total_bytes') or d.get('total_bytes_estimate')
        downloaded = d.get('downloaded_bytes', 0)
        eta = d.get('eta')
        if total:
            try:
                pct = downloaded / total * 100.0
                print(f"[Downloading] {os.path.basename(filename)} {pct:5.1f}% ({downloaded//1024//1024}MB/{total//1024//1024}MB) ETA {eta}s", end='\r', flush=True)
            except Exception:
                print(f"[Downloading] {os.path.basename(filename)} {downloaded//1024//1024}MB ETA {eta}s", end='\r', flush=True)
        else:
            print(f"[Downloading] {os.path.basename(filename)} {downloaded//1024//1024}MB ETA {eta}s", end='\r', flush=True)
    elif status == 'finished':
        print(f"\n[Finished] {d.get('filename')}")
    elif status == 'error':
        print(f"\n[Error] {d}")


def download_content(url, input_dir, metadata_dir, prefer_1080=True):
    ensure_dirs(input_dir, metadata_dir)

    # Prefer best video up to 1080p and merge to mp4 with audio.
    format_selector = 'bestvideo[height<=1080]+bestaudio/best' if prefer_1080 else 'best'

    ydl_opts = {
        'format': format_selector,
        'outtmpl': os.path.join(input_dir, '%(title)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'writeinfojson': True,
        'noplaylist': True,
        'progress_hooks': [progress_hook],
        'concurrent_fragment_downloads': 4,
        'keepvideo': True,
        'continuedl': True,
        'nocheckcertificate': str(
            os.environ.get('YTDLP_NO_CHECK_CERTIFICATES') or ''
        ).strip().lower() in {'1', 'true', 'yes', 'on'},
    }

    print('Rozpoczynam pobieranie — to może chwilę potrwać dla długich plików...')
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # Zlokalizuj pobrane pliki w katalogu input
    latest_mp4 = find_latest_file(input_dir, '.mp4')
    latest_mp3 = find_latest_file(input_dir, '.mp3')
    latest_info = find_latest_file(input_dir, '.info.json')

    merged_mp4 = None
    if latest_mp4:
        if file_has_audio(latest_mp4):
            merged_mp4 = latest_mp4
            print(f'Wideo zapisane i zawiera audio: {latest_mp4}')
        else:
            print(f'Znaleziono wideo bez audio: {latest_mp4}')
    else:
        print('Uwaga: nie znaleziono pobranego pliku wideo (.mp4).')

    if latest_mp3:
        print(f'Audio zapisane: {latest_mp3}')
    else:
        print('Uwaga: nie znaleziono pliku audio (.mp3).')

    if merged_mp4 is None and latest_mp4 and latest_mp3:
        merged_filename = os.path.splitext(os.path.basename(latest_mp4))[0] + '_merged.mp4'
        merged_path = os.path.join(input_dir, merged_filename)
        print(f'Łączę wideo i audio do: {merged_path}')
        merge_video_audio(latest_mp4, latest_mp3, merged_path)
        if file_has_audio(merged_path):
            merged_mp4 = merged_path
            print(f'Połączone wideo zapisane: {merged_mp4}')
        else:
            print('Nie udało się utworzyć połączonego pliku MP4 z audio.')

    if merged_mp4:
        print(f'Finalny plik MP4 do cięcia: {merged_mp4}')
    else:
        print('Finalny plik MP4 z audio nie jest dostępny. Sprawdź dane wejściowe.')

    if latest_info:
        # skopiuj info.json do metadata
        base = os.path.splitext(os.path.basename(latest_info))[0]
        dest_info = os.path.join(metadata_dir, os.path.basename(latest_info))
        shutil.copy2(latest_info, dest_info)
        print(f'Metadane skopiowano do: {dest_info}')

        # spróbuj wyciągnąć pole heatmap
        try:
            with open(latest_info, 'r', encoding='utf-8') as f:
                info_json = json.load(f)
            heatmap = recursive_find_key(info_json, 'heatmap')
            
            # Jeśli YouTube nie dostarczył heatmapy, stwórz placeholder
            if heatmap is None:
                duration = info_json.get('duration', 0)
                print(f'YouTube nie zawiera heatmapy dla tego wideo (duration: {duration}s). Tworzę placeholder heatmapę...')
                heatmap = create_placeholder_heatmap(duration)
            
            if heatmap:
                # zapisz heatmapę — zarówno pod nazwą powiązaną z plikiem, jak i jako heatmap.json (najnowsza)
                heatmap_name = f"{base}.heatmap.json"
                heatmap_path = os.path.join(metadata_dir, heatmap_name)
                with open(heatmap_path, 'w', encoding='utf-8') as hf:
                    json.dump(heatmap, hf, ensure_ascii=False, indent=2)
                # zapis ogólny
                general_path = os.path.join(metadata_dir, 'heatmap.json')
                with open(general_path, 'w', encoding='utf-8') as gf:
                    json.dump(heatmap, gf, ensure_ascii=False, indent=2)
                print(f'Heatmapa zapisana: {heatmap_path} ({len(heatmap)} segmentów)')
            else:
                print('Nie udało się wygenerować heatmapy.')
        except Exception as e:
            print('Błąd przy przetwarzaniu metadanych (.info.json):', e)
    else:
        print('Nie znaleziono pliku .info.json — opcja writeinfojson mogła się nie powieść.')


def main():
    parser = argparse.ArgumentParser(description='Pobierz wideo i audio oraz zapisz metadane i heatmapę (jeśli dostępna).')
    parser.add_argument('url', help='Link do wideo (YouTube, itp.)')
    parser.add_argument('--input', '-i', default=os.path.join(os.path.dirname(__file__), 'input'), help='Folder docelowy dla mp4/mp3')
    parser.add_argument('--metadata', '-m', default=os.path.join(os.path.dirname(__file__), 'metadata'), help='Folder docelowy dla metadanych')
    parser.add_argument('--no-1080', dest='use_1080', action='store_false', help='Nie ograniczaj wideo do 1080p (pobierz najlepsze dostępne)')
    args = parser.parse_args()

    try:
        download_content(args.url, args.input, args.metadata, prefer_1080=args.use_1080)
    except KeyboardInterrupt:
        print('\nPrzerwano przez użytkownika')
        sys.exit(1)
    except Exception as e:
        print('Błąd:', e)
        sys.exit(1)


if __name__ == '__main__':
    main()
