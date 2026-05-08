import argparse
import json
import subprocess
from pathlib import Path

MAX_SHORT_DURATION = 60.0


def parse_time(value):
    if isinstance(value, (int, float)):
        return float(value)
    parts = [part for part in str(value).strip().replace(',', '.').split(':') if part]
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f'Niepoprawny format czasu: {value}')


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


def load_windows(windows_file):
    with open(windows_file, 'r', encoding='utf-8') as f:
        windows = json.load(f)
    if not isinstance(windows, list):
        raise ValueError('Plik segmentów musi zawierać listę obiektów JSON.')
    return windows


def load_transcript(transcript_file):
    path = Path(transcript_file)
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict) and 'segments' in data:
        data = data['segments']
    if not isinstance(data, list):
        return []
    segments = []
    for item in data:
        try:
            text = str(item.get('text', '')).strip()
            start = parse_time(item['start'])
            end = parse_time(item['end'])
        except Exception:
            continue
        if end <= start:
            continue
        segments.append({'start': start, 'end': end, 'text': text})
    return sorted(segments, key=lambda item: item['start'])


def append_cutting_log(log_path, entry):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                log = json.load(f)
        except Exception:
            log = {}
    else:
        log = {}
    log.setdefault('cutter_adjustments', []).append(entry)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def containing_segment(segments, timestamp):
    for segment in segments:
        if segment['start'] < timestamp < segment['end']:
            return segment
    return None


def enforce_no_mid_sentence(start, end, segments, *, max_duration=MAX_SHORT_DURATION):
    if not segments:
        return start, min(end, start + max_duration), ['Brak transkrypcji w cutterze, zastosowano tylko limit 60s.']

    decisions = []
    original_start = start
    original_end = end

    start_segment = containing_segment(segments, start)
    if start_segment:
        start = start_segment['start']
        decisions.append(f'Przesunięto start z {original_start:.2f}s do {start:.2f}s, aby nie wejść w środek zdania.')

    end_segment = containing_segment(segments, end)
    if end_segment:
        extended_end = end_segment['end']
        if extended_end - start <= max_duration:
            end = extended_end
            decisions.append(f'Przesunięto koniec z {original_end:.2f}s do {end:.2f}s, aby domknąć wypowiedź.')
        else:
            safe_ends = [
                segment['end']
                for segment in segments
                if start < segment['end'] <= start + max_duration and segment['end'] <= end_segment['start']
            ]
            if safe_ends:
                end = max(safe_ends)
                decisions.append(f'Skrócono koniec do {end:.2f}s, bo puenta przekraczała limit 60s.')
            else:
                end = min(start + max_duration, end_segment['start'])
                decisions.append(f'Ustawiono koniec na {end:.2f}s przed kolejnym zdaniem, aby zachować limit 60s.')

    if end - start > max_duration:
        safe_ends = [segment['end'] for segment in segments if start < segment['end'] <= start + max_duration]
        if safe_ends:
            end = max(safe_ends)
        else:
            end = start + max_duration
        decisions.append(f'Przycięto klip do limitu {max_duration:.0f}s bez kończenia w środku segmentu, jeśli było to możliwe.')

    if end <= start:
        end = min(original_end, start + max_duration)
        decisions.append('Skorygowano nielogiczne granice cięcia po walidacji cutterem.')

    return start, end, decisions


def find_input_video(input_path):
    path = Path(input_path)
    if path.is_file():
        return path

    candidates = []
    if path.is_dir():
        candidates = list(path.glob('*.mp4')) + list(path.glob('*.mkv')) + list(path.glob('*.mov')) + list(path.glob('*.webm'))
    else:
        candidates = list(Path('input').glob('*.mp4')) + list(Path('input').glob('*.mkv')) + list(Path('input').glob('*.mov')) + list(Path('input').glob('*.webm'))

    if not candidates:
        raise FileNotFoundError('Nie znaleziono pliku wideo w katalogu input/. Podaj --video.')

    scored = []
    for candidate in candidates:
        has_audio = file_has_audio(candidate)
        has_video = file_has_video(candidate)
        scored.append((has_video and has_audio, has_video, has_audio, candidate.stat().st_mtime, candidate))

    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    best = scored[0][4]
    if not scored[0][0]:
        print(f'UWAGA: znaleziono tylko plik bez pełnego AV. Wybrano: {best} (video={scored[0][1]}, audio={scored[0][2]})')
    return best


def cut_segment(video_path, output_path, start, duration):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        'ffmpeg',
        '-y',
        '-i', str(video_path),
        '-ss', f'{start:.3f}',
        '-t', f'{duration:.3f}',
        '-map', '0',
        '-vf', 'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920',
        '-c:v', 'libx264',
        '-preset', 'superfast',
        '-crf', '23',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-movflags', '+faststart',
        str(output_path)
    ]
    subprocess.run(cmd, check=True)


def format_time(seconds):
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f'{minutes:02d}-{secs:05.2f}'.replace('.', '_')


def parse_args():
    parser = argparse.ArgumentParser(description='Wycinanie surowych shotów Shorts z wideo przy pomocy ffmpeg')
    parser.add_argument('--video', default=None, help='Ścieżka do pliku wideo w input/')
    parser.add_argument('--windows', default='top_windows.json', help='Plik JSON z listą wybranych okien (start/end)')
    parser.add_argument('--transcript', default='transcripts/final_transcript.json', help='Transkrypcja JSON do ochrony No Mid-Sentence')
    parser.add_argument('--output-dir', default='cuts/raw', help='Katalog wyjściowy dla surowych shotów')
    parser.add_argument('--cutting-log', default='metadata/cutting_logic.json', help='Log decyzji Smart Context Cutter')
    return parser.parse_args()


def main():
    args = parse_args()
    video_path = find_input_video(args.video) if args.video else find_input_video('input')
    windows = load_windows(args.windows)
    transcript = load_transcript(args.transcript)

    for idx, window in enumerate(windows, start=1):
        start = float(window['start'])
        end = float(window['end'])
        start, end, decisions = enforce_no_mid_sentence(start, end, transcript)
        duration = end - start
        output_path = Path(args.output_dir) / f'segment_{idx}_{format_time(start)}_{format_time(end)}.mp4'
        if decisions:
            append_cutting_log(args.cutting_log, {
                'segment_index': idx,
                'source_window': {
                    'start': window.get('start'),
                    'end': window.get('end'),
                    'summary': window.get('summary'),
                },
                'final_start': start,
                'final_end': end,
                'final_duration': duration,
                'decisions': decisions,
            })
        print(f'Wycinam segment {idx}: {start:.2f}s - {end:.2f}s -> {output_path}')
        cut_segment(video_path, output_path, start, duration)

    print(f'Gotowe. Pliki zapisano w {Path(args.output_dir).resolve()}')


if __name__ == '__main__':
    main()
