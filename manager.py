#!/usr/bin/env python3
"""
manager.py — Główny orkestrator workflow

Uruchamia cały workflow od pobrania wideo do dodania napisów na wycinki.

Użycie:
  python manager.py --url "https://www.youtube.com/watch?v=..."
  python manager.py --url "..." --cleanup

Workflow:
  1. Sprawdza i tworzy niezbędne foldery
  2. Pobiera wideo (download_content.py)
  3. Transkrybuje audio (transcribe_podcast.py)
  4. Analizuje viralowe momenty (analyze_virals.py)
  5. Wycina segmenty (cutter.py)
  6. Dodaje napisy (subtitler.py)
  7. Opcjonalnie: czyszcze pliki z input/ (--cleanup)
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional
import shutil
import time

try:
    from tqdm import tqdm
except ImportError:
    # Fallback jeśli tqdm nie jest zainstalowany
    class tqdm:
        def __init__(self, iterable=None, desc=None, total=None):
            self.iterable = iterable
            self.desc = desc
            self.total = total
        
        def __iter__(self):
            if self.desc:
                print(f"{self.desc}")
            return iter(self.iterable) if self.iterable else iter([])
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def update(self, n=1):
            pass
        
        @staticmethod
        def write(msg):
            print(msg)


class ManagerError(Exception):
    """Wyjątek dla błędów managera."""
    pass


class WorkflowManager:
    """Orkestrator workflow transkrypcji i cuttingu."""
    
    def __init__(self, url: str, cleanup: bool = False, skip_download: bool = False):
        self.url = url
        self.cleanup = cleanup
        self.skip_download = skip_download
        self.script_dir = Path(__file__).parent
        
        # Foldery
        self.input_dir = self.script_dir / 'input'
        self.metadata_dir = self.script_dir / 'metadata'
        self.transcripts_dir = self.script_dir / 'transcripts'
        self.cuts_dir = self.script_dir / 'cuts'
        self.cuts_raw_dir = self.cuts_dir / 'raw'
        self.cuts_subs_dir = self.cuts_dir / 'subtitles'
        
        # Pliki
        self.transcript_file = self.transcripts_dir / 'final_transcript.json'
        self.heatmap_file = self.metadata_dir / 'heatmap.json'
        self.windows_file = self.script_dir / 'top_windows.json'
    
    def ensure_directories(self):
        """Tworzy niezbędne foldery."""
        print("📁 Sprawdzam i tworzę niezbędne foldery...")
        
        dirs = [
            self.input_dir,
            self.metadata_dir,
            self.transcripts_dir,
            self.cuts_dir,
            self.cuts_raw_dir,
            self.cuts_subs_dir,
        ]
        
        for directory in dirs:
            directory.mkdir(parents=True, exist_ok=True)
            print(f"  ✓ {directory.relative_to(self.script_dir)}")
    
    def probe_streams(self, path: Path, stream_type: Optional[str] = None) -> int:
        """Sprawdza liczbę strumieni audio/video w pliku za pomocą ffprobe."""
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', stream_type if stream_type else 'a',
            '-show_entries', 'stream=index',
            '-of', 'csv=p=0',
            str(path),
        ]

        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            lines = [line for line in result.stdout.splitlines() if line.strip()]
            return len(lines)
        except subprocess.CalledProcessError:
            return 0

    def has_audio_stream(self, path: Path) -> bool:
        return self.probe_streams(path, 'a') > 0

    def has_video_stream(self, path: Path) -> bool:
        return self.probe_streams(path, 'v') > 0

    def extract_audio(self, video_file: Path) -> Optional[Path]:
        """Wyciągnij audio z pliku wideo lub kontenera z audio."""
        audio_output = self.input_dir / f'{video_file.stem}.mp3'

        if audio_output.exists():
            return audio_output

        print(f"  Wyciągam audio z wideo: {video_file.name}")
        cmd = [
            'ffmpeg',
            '-y',
            '-i', str(video_file),
            '-q:a', '9',
            str(audio_output),
        ]

        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"  ✓ Audio wyciągnięte: {audio_output.name}")
            return audio_output
        except subprocess.CalledProcessError as e:
            print(f"  ✗ Błąd wyciągania audio: {e}")
            return None

    def find_latest_audio(self) -> Optional[Path]:
        """Znajduje najnowszy plik audio w input/ lub wyciąga go z pliku zawierającego audio."""
        audio_extensions = ['.mp3', '.m4a', '.wav', '.aac']
        candidates = []

        for ext in audio_extensions:
            candidates.extend(self.input_dir.glob(f'*{ext}'))

        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return candidates[0]

        container_extensions = ['.mp4', '.mkv', '.mov', '.webm']
        candidates = []
        for ext in container_extensions:
            candidates.extend(self.input_dir.glob(f'*{ext}'))

        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        for candidate in candidates:
            if self.has_audio_stream(candidate):
                return self.extract_audio(candidate)

        return None
    
    def find_latest_video(self) -> Optional[Path]:
        """Znajduje najnowszy plik wideo w input/ z prawdziwym strumieniem wideo."""
        video_extensions = ['.mp4', '.mkv', '.mov', '.webm']
        candidates = []

        for ext in video_extensions:
            candidates.extend(self.input_dir.glob(f'*{ext}'))

        if not candidates:
            return None

        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        for candidate in candidates:
            if self.has_video_stream(candidate):
                return candidate

        return None
    
    def run_command(
        self,
        cmd: list,
        description: str,
        max_retries: int = 1,
    ) -> bool:
        """
        Uruchamia polecenie z obsługą błędów i retryem.
        
        Args:
            cmd: Lista poleceń dla subprocess
            description: Opis kroku
            max_retries: Maksymalnie ile razy spróbować
        
        Returns:
            True jeśli sukces, False jeśli błąd
        """
        for attempt in range(1, max_retries + 1):
            print(f"\n{'='*60}")
            print(f"📋 {description}")
            if attempt > 1:
                print(f"   (Próba {attempt}/{max_retries})")
            print(f"{'='*60}")
            
            try:
                result = subprocess.run(cmd, check=True)
                return True
            except subprocess.CalledProcessError as e:
                print(f"✗ Błąd: {description} zawiódł")
                if attempt < max_retries:
                    wait_time = 2 ** (attempt - 1)
                    print(f"  Czekam {wait_time}s przed retry...")
                    time.sleep(wait_time)
                else:
                    print(f"  Wykonano {max_retries} prób(y). Przerwanie.")
                    return False
            except Exception as e:
                print(f"✗ Nieoczekiwany błąd: {e}")
                return False
        
        return False
    
    def download_content(self) -> bool:
        """Krok 1: Pobierz wideo z YouTube."""
        cmd = [
            'python', str(self.script_dir / 'download_content.py'),
            self.url,
            '--input', str(self.input_dir),
            '--metadata', str(self.metadata_dir),
        ]
        
        return self.run_command(cmd, "1️⃣  Pobieranie wideo z YouTube")
    
    def transcribe_podcast(self) -> bool:
        """Krok 2: Transkrybuj audio."""
        audio_file = self.find_latest_audio()
        
        if not audio_file:
            print("✗ Nie znaleziono pliku audio w input/")
            return False
        
        print(f"  Znalezione audio: {audio_file.name}")
        
        cmd = [
            'python', str(self.script_dir / 'transcribe_podcast.py'),
            '--file', str(audio_file),
            '--out', str(self.transcript_file),
        ]
        
        # Retry 2 razy dla transkrypcji (czasami API się wysypuje)
        return self.run_command(cmd, "2️⃣  Transkrypcja audio", max_retries=2)
    
    def analyze_virals(self) -> bool:
        """Krok 3: Przeanalizuj i znajdź viralowe momenty."""
        if not self.transcript_file.exists():
            print(f"✗ Plik transkrypcji nie istnieje: {self.transcript_file}")
            return False
        
        if not self.heatmap_file.exists():
            print(f"✗ Plik heatmapy nie istnieje: {self.heatmap_file}")
            return False
        
        cmd = [
            'python', str(self.script_dir / 'analyze_virals.py'),
            '--transcript', str(self.transcript_file),
            '--heatmap', str(self.heatmap_file),
            '--save-json', str(self.windows_file),
        ]
        
        return self.run_command(cmd, "3️⃣  Analiza viralowych momentów")
    
    def cut_segments(self) -> bool:
        """Krok 4: Wytnij segmenty z wideo."""
        if not self.windows_file.exists():
            print(f"✗ Plik okien nie istnieje: {self.windows_file}")
            return False
        
        video_file = self.find_latest_video()
        if not video_file:
            print("✗ Nie znaleziono pliku wideo w input/")
            return False
        
        print(f"  Znalezione wideo: {video_file.name}")
        
        cmd = [
            'python', str(self.script_dir / 'cutter.py'),
            '--video', str(video_file),
            '--windows', str(self.windows_file),
            '--output-dir', str(self.cuts_dir),
        ]
        
        return self.run_command(cmd, "4️⃣  Wycinanie segmentów")
    
    def add_subtitles(self) -> bool:
        """Krok 5: Dodaj napisy na wycinki."""
        if not self.transcript_file.exists():
            print(f"✗ Plik transkrypcji nie istnieje: {self.transcript_file}")
            return False
        
        cmd = [
            'python', str(self.script_dir / 'subtitler.py'),
            '--transcript', str(self.transcript_file),
            '--input-dir', str(self.cuts_dir),
            '--output-raw', str(self.cuts_raw_dir),
            '--output-subs', str(self.cuts_subs_dir),
        ]
        
        return self.run_command(cmd, "5️⃣  Dodawanie napisów")
    
    def cleanup_input(self):
        """Usuń ciężkie pliki wideo z input/."""
        if not self.cleanup:
            return
        
        print("\n" + "="*60)
        print("🧹 Czyszczenie (--cleanup)")
        print("="*60)
        
        video_extensions = ['.mp4', '.mkv', '.mov', '.webm', '.m4a', '.mp3']
        deleted_count = 0
        
        for ext in video_extensions:
            for file in self.input_dir.glob(f'*{ext}'):
                try:
                    file.unlink()
                    print(f"  ✓ Usunięto: {file.name}")
                    deleted_count += 1
                except Exception as e:
                    print(f"  ✗ Błąd przy usuwaniu {file.name}: {e}")
        
        if deleted_count == 0:
            print("  (Brak plików do usunięcia)")
    
    def print_summary(self):
        """Wyświetla podsumowanie generowanych plików."""
        print("\n" + "="*60)
        print("✓ WORKFLOW UKOŃCZONY")
        print("="*60)
        print("\n📊 Wygenerowane pliki:")
        print()
        
        # Surowe wycinki
        raw_files = list(self.cuts_raw_dir.glob('*.mp4'))
        if raw_files:
            print(f"  📂 Surowe wycinki ({len(raw_files)} plików):")
            for f in sorted(raw_files)[:5]:
                print(f"    - {f.name}")
            if len(raw_files) > 5:
                print(f"    ... i {len(raw_files) - 5} więcej")
        
        print()
        
        # Wycinki z napisami
        subs_files = list(self.cuts_subs_dir.glob('*.mp4'))
        if subs_files:
            print(f"  📂 Wycinki z napisami ({len(subs_files)} plików):")
            for f in sorted(subs_files)[:5]:
                print(f"    - {f.name}")
            if len(subs_files) > 5:
                print(f"    ... i {len(subs_files) - 5} więcej")
        
        print()
        print(f"  📁 Katalog output: {self.cuts_subs_dir.resolve()}")
        print()
    
    def run(self):
        """Uruchamia cały workflow."""
        print("\n" + "="*60)
        print("🚀 VIRAL CUTTER AI — WORKFLOW MANAGER")
        print("="*60)
        print(f"URL: {self.url}")
        print(f"Skip Download: {'Tak' if self.skip_download else 'Nie'}")
        print(f"Cleanup: {'Tak' if self.cleanup else 'Nie'}")
        print()
        
        try:
            # Przygotowanie
            self.ensure_directories()
            
            # Workflow
            steps = []
            
            if not self.skip_download:
                steps.append((self.download_content, "Pobieranie"))
                steps.append((self.transcribe_podcast, "Transkrypcja"))
            
            steps.extend([
                (self.analyze_virals, "Analiza"),
                (self.cut_segments, "Wycinanie"),
                (self.add_subtitles, "Napisy"),
            ])
            
            for step_func, step_name in steps:
                success = step_func()
                if not success:
                    raise ManagerError(f"Workflow przerwany na kroku: {step_name}")
            
            # Czyszczenie
            self.cleanup_input()
            
            # Podsumowanie
            self.print_summary()
            
        except ManagerError as e:
            print(f"\n✗ BŁĄD: {e}")
            sys.exit(1)
        except KeyboardInterrupt:
            print("\n\n⚠ Przerwano przez użytkownika")
            sys.exit(130)
        except Exception as e:
            print(f"\n✗ NIEOCZEKIWANY BŁĄD: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description='Orchstracja workflow: download → transkrypcja → analiza → cutting → napisy',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Przykłady:
  python manager.py --url "https://www.youtube.com/watch?v=..."
  python manager.py --url "..." --cleanup
  python manager.py --url "..." --skip-download  (do testowania, pomiń pobieranie)
        '''
    )
    
    parser.add_argument(
        '--url',
        required=True,
        help='Adres URL do wideo na YouTube',
    )
    
    parser.add_argument(
        '--cleanup',
        action='store_true',
        help='Po zakończeniu usuń ciężkie pliki z input/',
    )
    
    parser.add_argument(
        '--skip-download',
        action='store_true',
        help='Pomiń pobieranie i transkrypcję (testowanie)',
    )
    
    return parser.parse_args()


def main():
    args = parse_args()
    
    manager = WorkflowManager(
        url=args.url,
        cleanup=args.cleanup,
        skip_download=args.skip_download,
    )
    
    manager.run()


if __name__ == '__main__':
    main()
