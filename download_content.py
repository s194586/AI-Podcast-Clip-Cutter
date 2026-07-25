#!/usr/bin/env python3
"""Pobiera wideo (mp4, do 1080p jeśli dostępne) i audio (mp3), zapisuje do /input
oraz publikuje zweryfikowaną heatmapę z bieżącego wyniku yt-dlp.

Zachowuje oryginalne pliki wideo i audio oraz pokazuje postęp pobierania (przydatne dla długich plików).
"""
import os
import sys
import argparse
import subprocess
from pathlib import Path
from yt_dlp import YoutubeDL
from yt_dlp.version import __version__ as YT_DLP_VERSION

from heatmap_contract import (
    HeatmapUnavailableError,
    atomic_write_json,
    build_youtube_heatmap,
)
from source_media_contract import build_source_media_manifest


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


def file_has_video(path):
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-select_streams', 'v',
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


def fetch_youtube_metadata(source_url):
    """Return raw metadata from one metadata-only yt-dlp request."""
    ydl_opts = {
        'noplaylist': True,
        'compat_opts': {'no-certifi'},
    }
    with YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(source_url, download=False)


def build_youtube_heatmap_document(info):
    return build_youtube_heatmap(info, extractor_version=YT_DLP_VERSION)


def fetch_youtube_heatmap_metadata(source_url):
    """Return a trusted heatmap document from one metadata-only yt-dlp request."""
    return build_youtube_heatmap_document(fetch_youtube_metadata(source_url))


def current_downloaded_media_file(info, ydl, observed_paths, input_dir):
    """Find a usable MP4 explicitly reported by the current yt-dlp invocation."""
    resolved_input_dir = Path(input_dir).resolve()
    candidates = list(observed_paths)
    if isinstance(info, dict):
        for field in ("filepath", "_filename"):
            value = info.get(field)
            if isinstance(value, str):
                candidates.append(value)
        requested_downloads = info.get("requested_downloads")
        if isinstance(requested_downloads, list):
            for download in requested_downloads:
                if isinstance(download, dict):
                    value = download.get("filepath")
                    if isinstance(value, str):
                        candidates.append(value)
        try:
            candidates.append(ydl.prepare_filename(info))
        except (AttributeError, KeyError, TypeError):
            pass

    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, (str, Path)):
            continue
        path = Path(candidate).resolve()
        if path in seen:
            continue
        seen.add(path)
        try:
            path.relative_to(resolved_input_dir)
        except ValueError:
            continue
        if path.suffix.lower() == ".mp4" and path.is_file():
            if file_has_audio(path) and file_has_video(path):
                return path
    return None


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


def download_content(url, input_dir, metadata_dir, prefer_1080=True, workspace_path=None):
    ensure_dirs(input_dir, metadata_dir)

    # Prefer best video up to 1080p and merge to mp4 with audio.
    format_selector = 'bestvideo[height<=1080]+bestaudio/best' if prefer_1080 else 'best'
    observed_paths = []

    def postprocessor_hook(status):
        if status.get("status") != "finished":
            return
        info_dict = status.get("info_dict")
        if isinstance(info_dict, dict):
            path = info_dict.get("filepath")
            if isinstance(path, str):
                observed_paths.append(path)

    ydl_opts = {
        'format': format_selector,
        'outtmpl': os.path.join(input_dir, '%(title).180B [%(id)s].%(ext)s'),
        'merge_output_format': 'mp4',
        'writeinfojson': True,
        'noplaylist': True,
        'progress_hooks': [progress_hook],
        'postprocessor_hooks': [postprocessor_hook],
        'concurrent_fragment_downloads': 4,
        'keepvideo': True,
        'continuedl': True,
        # yt-dlp otherwise prefers certifi and ignores the container's updated
        # Linux trust store. This supported compatibility option keeps normal
        # certificate verification while using OpenSSL's system CA bundle.
        'compat_opts': {'no-certifi'},
    }

    print('Rozpoczynam pobieranie — to może chwilę potrwać dla długich plików...')
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    merged_mp4 = current_downloaded_media_file(info, ydl, observed_paths, input_dir)

    if merged_mp4:
        print(f'Finalny plik MP4 do cięcia: {merged_mp4}')
    else:
        print('Finalny plik MP4 z audio nie jest dostępny. Sprawdź dane wejściowe.')

    if merged_mp4 is None:
        return None

    workspace = Path(workspace_path).resolve() if workspace_path else Path(input_dir).resolve().parent
    try:
        heatmap_document = build_youtube_heatmap_document(info)
    except HeatmapUnavailableError:
        try:
            os.unlink(os.path.join(metadata_dir, "heatmap.json"))
        except FileNotFoundError:
            pass
        raise
    source_manifest = build_source_media_manifest(
        video_id=heatmap_document["video_id"],
        source_url=url,
        media_file=merged_mp4,
        workspace_path=workspace,
    )
    source_manifest_path = os.path.join(metadata_dir, "source_media.json")
    heatmap_path = os.path.join(metadata_dir, "heatmap.json")
    atomic_write_json(source_manifest_path, source_manifest)
    atomic_write_json(heatmap_path, heatmap_document)
    print(
        f'Heatmapa YouTube Most Replayed zapisana: {heatmap_path} '
        f'({len(heatmap_document["points"])} segmentów)'
    )
    return merged_mp4


def main():
    parser = argparse.ArgumentParser(description='Pobierz wideo i audio oraz zapisz prawdziwą heatmapę YouTube.')
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
