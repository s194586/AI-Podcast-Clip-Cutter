#!/usr/bin/env python3
"""
subtitler.py — Dodawanie dynamicznych napisów na wycięte wideo.

Funkcjonalność:
- Ładuje transkrypcję z JSON (Naruciak_Final.json)
- Dla każdego wycinku z cuts/ znajduje odpowiadające segmenty transkrypcji
- Generuje napisy (1-3 słowa naraz) z bezpieczeństwem czasu
- Dodaje napisy na wideo za pomocą ffmpeg (białe, duża czcionka, czarny obrys)
- Umieszcza napisy w dolnej 1/3 ekranu
- Surowe wideo przenosi do cuts/raw/, z napisami zapisuje w cuts/subtitles/

Użycie:
  python subtitler.py --transcript transcripts/Naruciak_Final.json --input-dir cuts --output-raw cuts/raw --output-subs cuts/subtitles

"""

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import List, Dict, Tuple
import shutil


def parse_time(time_str):
    """Parsuje czas z różnych formatów."""
    if isinstance(time_str, (int, float)):
        return float(time_str)
    
    time_str = str(time_str).strip()
    
    # Format: HH:MM:SS.ms lub MM:SS.ms
    pattern = r'^(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)$'
    match = re.match(pattern, time_str)
    if match:
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        return hours * 3600 + minutes * 60 + seconds
    
    raise ValueError(f'Niepoprawny format czasu: {time_str}')


def load_transcript(path: Path) -> List[Dict]:
    """Ładuje transkrypcję z JSON."""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    if isinstance(data, dict) and 'segments' in data:
        return data['segments']
    return data


def split_into_chunks(text: str, max_words: int = 3) -> List[str]:
    """Dzieli tekst na kawałki (1-3 słowa)."""
    words = text.split()
    if not words:
        return []
    
    chunks = []
    for i in range(0, len(words), max_words):
        chunk = ' '.join(words[i:i + max_words])
        chunks.append(chunk)
    
    return chunks


def build_subtitle_lines(transcript: List[Dict], segment_start: float, segment_duration: float) -> List[Tuple[float, float, str]]:
    """
    Buduje listę linii napisów dla segmentu wideo.
    Zwraca listę krotek: (start_time, end_time, text)
    
    start_time i end_time są względem początku segmentu (0.0 - segment_duration)
    """
    segment_end = segment_start + segment_duration
    subtitles = []
    
    for segment in transcript:
        seg_start = parse_time(segment['start'])
        seg_end = parse_time(segment['end'])
        text = segment.get('text', '').strip()
        
        if not text:
            continue
        
        # Pomiń segmenty poza naszym oknem
        if seg_end <= segment_start or seg_start >= segment_end:
            continue
        
        # Oblicz przecięcie z naszym segmentem
        overlap_start = max(seg_start, segment_start)
        overlap_end = min(seg_end, segment_end)
        
        # Przeliczy na czas względem segmentu
        rel_start = overlap_start - segment_start
        rel_end = overlap_end - segment_start
        
        # Dziel tekst na małe kawałki (1-3 słowa)
        text_chunks = split_into_chunks(text, max_words=3)
        
        if not text_chunks:
            continue
        
        # Rozłóż czas równomiernie na kawałki
        chunk_duration = (rel_end - rel_start) / len(text_chunks)
        
        for i, chunk in enumerate(text_chunks):
            chunk_start = rel_start + i * chunk_duration
            chunk_end = rel_start + (i + 1) * chunk_duration
            subtitles.append((chunk_start, chunk_end, chunk))
    
    return subtitles


def format_srt_time(seconds: float) -> str:
    """Formatuje czas dla SRT (HH:MM:SS,mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    ms = int((secs - int(secs)) * 1000)
    return f'{hours:02d}:{minutes:02d}:{int(secs):02d},{ms:03d}'


def create_subtitle_srt(subtitles: List[Tuple[float, float, str]]) -> str:
    """Tworzy SRT z listy napisów."""
    srt_lines = []
    
    for idx, (start, end, text) in enumerate(subtitles, 1):
        srt_lines.append(str(idx))
        srt_lines.append(f'{format_srt_time(start)} --> {format_srt_time(end)}')
        srt_lines.append(text)
        srt_lines.append('')
    
    return '\n'.join(srt_lines)


def extract_segment_time_from_filename(filename: str) -> Tuple[float, float]:
    """
    Ekstrahuje czas segmentu z nazwy pliku.
    Format: segment_X_MM-SS_mmm_MM-SS_mmm.mp4
    lub: segment_X_MM-SS_mm_MM-SS_mm.mp4
    """
    # Usuń rozszerzenie
    name = Path(filename).stem
    
    # Pattern: segment_\d+_(\d{2})-(\d{2}_\d+)_(\d{2})-(\d{2}_\d+)
    # Bardziej elastyczny regex - akceptuje różne liczby cyfr po podkreśleniu
    pattern = r'segment_\d+_(\d{2})-(\d{2}_\d+)_(\d{2})-(\d{2}_\d+)'
    match = re.search(pattern, name)
    
    if not match:
        raise ValueError(f'Nie można sparsować czasu z nazwy pliku: {filename}')
    
    # Zamień _ na . dla sekund
    start_minutes = int(match.group(1))
    start_secs = float(match.group(2).replace('_', '.'))
    end_minutes = int(match.group(3))
    end_secs = float(match.group(4).replace('_', '.'))
    
    start_time = start_minutes * 60 + start_secs
    end_time = end_minutes * 60 + end_secs
    
    return start_time, end_time


def add_subtitles_to_video(
    input_video: Path,
    output_video: Path,
    srt_file: Path,
    font_size: int = 60,
    font_name: str = 'Arial',
) -> None:
    """
    Dodaje napisy do wideo za pomocą ffmpeg.
    - Białe napisy z czarnym obrysem
    - Umieszcza w dolnej 1/3 ekranu
    - Używa filtru subtitles
    """
    output_video.parent.mkdir(parents=True, exist_ok=True)
    
    # Filtr ffmpeg: subtitles z dostosowaniem pozycji i stylizacji
    filter_str = (
        f"subtitles='{srt_file}':force_style="
        f"'FontName={font_name},FontSize={font_size},"
        f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,"
        f"MarginL=50,MarginR=50,MarginV=80'"
    )
    
    cmd = [
        'ffmpeg',
        '-y',
        '-i', str(input_video),
        '-vf', filter_str,
        '-c:a', 'aac',
        '-b:a', '192k',
        str(output_video),
    ]
    
    print(f'  Dodaję napisy: {output_video.name}')
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def process_cut_file(
    cut_file: Path,
    transcript: List[Dict],
    output_raw: Path,
    output_subs: Path,
) -> None:
    """
    Przetwarza jeden wycięty plik:
    1. Ekstrahuje czas z nazwy pliku
    2. Buduje napisy z transkrypcji
    3. Przenosi surowe wideo do cuts/raw/
    4. Dodaje napisy i zapisuje do cuts/subtitles/
    """
    output_raw.mkdir(parents=True, exist_ok=True)
    output_subs.mkdir(parents=True, exist_ok=True)
    
    try:
        segment_start, segment_end = extract_segment_time_from_filename(cut_file.name)
        segment_duration = segment_end - segment_start
    except ValueError as e:
        print(f'  ⚠ Błąd przetwarzania {cut_file.name}: {e}')
        return
    
    # Buduj napisy
    subtitles = build_subtitle_lines(transcript, segment_start, segment_duration)
    
    if not subtitles:
        print(f'  ⚠ Brak napisów dla {cut_file.name}')
    
    # Przenieś surowe wideo do cuts/raw/
    raw_output = output_raw / cut_file.name
    shutil.copy2(cut_file, raw_output)
    print(f'✓ Skopiowano surowe wideo: {raw_output.name}')
    
    # Stwórz SRT
    srt_content = create_subtitle_srt(subtitles)
    srt_file = cut_file.parent / f'{cut_file.stem}.srt'
    with open(srt_file, 'w', encoding='utf-8') as f:
        f.write(srt_content)
    
    # Dodaj napisy do wideo
    subs_output = output_subs / cut_file.name
    try:
        add_subtitles_to_video(cut_file, subs_output, srt_file, font_size=60, font_name='Arial')
        print(f'✓ Dodano napisy: {subs_output.name}')
    except Exception as e:
        print(f'  ✗ Błąd przy dodawaniu napisów: {e}')
        return
    finally:
        # Usuń tymczasowy plik SRT
        if srt_file.exists():
            srt_file.unlink()


def parse_args():
    parser = argparse.ArgumentParser(
        description='Dodawanie dynamicznych napisów na wycięte wideo',
    )
    parser.add_argument(
        '--transcript',
        default='transcripts/final_transcript.json',
        help='Ścieżka do transkrypcji JSON',
    )
    parser.add_argument(
        '--input-dir',
        default='cuts',
        help='Katalog z wyciętymi wideo',
    )
    parser.add_argument(
        '--output-raw',
        default='cuts/raw',
        help='Katalog wyjściowy dla surowych wideo (bez napisów)',
    )
    parser.add_argument(
        '--output-subs',
        default='cuts/subtitles',
        help='Katalog wyjściowy dla wideo z napisami',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    
    transcript_path = Path(args.transcript)
    input_dir = Path(args.input_dir)
    output_raw = Path(args.output_raw)
    output_subs = Path(args.output_subs)
    
    if not transcript_path.exists():
        print(f'✗ Plik transkrypcji nie istnieje: {transcript_path}')
        return
    
    if not input_dir.exists():
        print(f'✗ Katalog wejściowy nie istnieje: {input_dir}')
        return
    
    print(f'📖 Ładuję transkrypcję: {transcript_path}')
    transcript = load_transcript(transcript_path)
    
    # Znajdź wszystkie wycięte wideo
    cut_files = sorted(input_dir.glob('segment_*.mp4'))
    
    if not cut_files:
        print(f'⚠ Nie znaleziono plików segment_*.mp4 w {input_dir}')
        return
    
    print(f'🎬 Znaleziono {len(cut_files)} wyciętych wideo')
    print()
    
    for cut_file in cut_files:
        print(f'Przetwarzam: {cut_file.name}')
        process_cut_file(cut_file, transcript, output_raw, output_subs)
        print()
    
    print(f'✓ Gotowe!')
    print(f'  Surowe wideo: {output_raw.resolve()}')
    print(f'  Z napisami:   {output_subs.resolve()}')


if __name__ == '__main__':
    main()
