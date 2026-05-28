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
  4. Sprawdza transkrypcję i timing napisów (subtitler_checker.py)
  5. Analizuje viralowe momenty (analyze_virals.py)
  6. Wycina segmenty (cutter.py)
  7. Dodaje napisy (subtitler.py)
  8. Opcjonalnie: czyszcze pliki z input/ (--cleanup)
"""

import argparse
import json
import os
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Optional
import shutil
import time

from content_classifier import VALID_CONTENT_TYPE_MODES, normalize_content_type_mode
from layout import VALID_LAYOUT_MODES, normalize_layout_mode
from pipeline_modes import (
    AI_MODE_LOCAL_ONLY,
    VALID_AI_MODES,
    VALID_SUBTITLE_CHECKER_MODES,
    allows_gemini,
    default_subtitle_checker_mode,
    normalize_ai_mode,
    normalize_subtitle_checker_mode,
    requires_gemini,
    subtitle_checker_sample_limit,
    subtitle_checker_uses_ai,
)

os.environ["UV_NATIVE_TLS"] = "1"

try:
    import certifi
except Exception as certifi_import_error:
    certifi = None
    CERTIFI_IMPORT_ERROR = certifi_import_error
else:
    CERTIFI_IMPORT_ERROR = None

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


SSL_CERT_ENV_VARS = (
    'SSL_CERT_FILE',
    'REQUESTS_CA_BUNDLE',
    'CURL_CA_BUNDLE',
    'GRPC_DEFAULT_SSL_ROOTS_FILE_PATH',
)


def bootstrap_ssl_certificates(quiet: bool = False, allow_insecure_fallback: bool = False):
    cert_path = None
    if certifi is None:
        if not quiet:
            print("  Warning: certifi is not installed. Run `uv add certifi` to install the CA bundle.")
    else:
        cert_path = certifi.where()
        for env_name in SSL_CERT_ENV_VARS:
            os.environ[env_name] = cert_path

    if allow_insecure_fallback:
        ssl._create_default_https_context = ssl._create_unverified_context
        os.environ['PYTHONHTTPSVERIFY'] = '0'
    elif 'PYTHONHTTPSVERIFY' in os.environ:
        os.environ.pop('PYTHONHTTPSVERIFY', None)

    return cert_path

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
    
    def __init__(
        self,
        url: Optional[str],
        cleanup: bool = False,
        skip_download: bool = False,
        skip_subtitle_checker: bool = False,
        skip_smart_context: bool = False,
        force_subtitle_checker: bool = False,
        auto_fix_subtitles: bool = True,
        ai_mode: str = 'gemini_optional',
        subtitle_checker_mode: Optional[str] = None,
        subtitle_checker_ai_samples: int = 8,
        transcription_backend: str = 'faster_whisper',
        whisper_model: str = 'small',
        transcription_device: str = 'auto',
        transcription_compute_type: str = 'auto',
        enable_diarization: bool = True,
        diarization_backend: str = 'heuristic_cluster',
        diarization_max_speakers: int = 4,
        content_type: str = 'auto',
        layout_mode: str = 'auto',
    ):
        self.url = url
        self.cleanup = cleanup
        self.skip_download = skip_download
        self.skip_subtitle_checker = skip_subtitle_checker
        self.ai_mode = normalize_ai_mode(AI_MODE_LOCAL_ONLY if skip_smart_context else ai_mode)
        self.skip_smart_context = self.ai_mode == AI_MODE_LOCAL_ONLY
        self.force_subtitle_checker = force_subtitle_checker
        self.auto_fix_subtitles = auto_fix_subtitles
        resolved_checker_mode = subtitle_checker_mode or default_subtitle_checker_mode(self.ai_mode)
        self.subtitle_checker_mode = normalize_subtitle_checker_mode(resolved_checker_mode)
        if not allows_gemini(self.ai_mode) and subtitle_checker_uses_ai(self.subtitle_checker_mode):
            self.subtitle_checker_mode = 'local_only'
        self.subtitle_checker_ai_samples = max(0, int(subtitle_checker_ai_samples))
        self.transcription_backend = str(transcription_backend or 'faster_whisper').strip() or 'faster_whisper'
        self.whisper_model = str(whisper_model or 'small').strip() or 'small'
        self.transcription_device = str(transcription_device or 'auto').strip() or 'auto'
        self.transcription_compute_type = str(transcription_compute_type or 'auto').strip() or 'auto'
        self.enable_diarization = bool(enable_diarization)
        self.diarization_backend = str(diarization_backend or 'heuristic_cluster').strip() or 'heuristic_cluster'
        self.diarization_max_speakers = max(1, int(diarization_max_speakers))
        self.content_type = normalize_content_type_mode(content_type)
        self.layout_mode = normalize_layout_mode(layout_mode)
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
        self.subtitle_check_report_file = self.metadata_dir / 'subtitle_check_report.json'
        self.cutting_logic_file = self.metadata_dir / 'cutting_logic.json'
        self.content_profile_file = self.metadata_dir / 'content_profile.json'
        self.heatmap_file = self.metadata_dir / 'heatmap.json'
        self.windows_file = self.script_dir / 'top_windows.json'
        self.gemini_transport = os.environ.get('GEMINI_TRANSPORT', '').strip().lower()
        self.gemini_available = False

    def build_subprocess_env(self):
        env = os.environ.copy()
        env['UV_NATIVE_TLS'] = '1'
        env.setdefault('PYTHONUTF8', '1')
        env.setdefault('PYTHONIOENCODING', 'utf-8')
        return env

    def cleanup_previous_run(self):
        """Czyści artefakty poprzedniego runa, ale zostawia pliki źródłowe i konfiguracyjne."""
        print("\n" + "=" * 60)
        print("🧹 Czyszczenie poprzedniego runa")
        print("=" * 60)

        deleted_count = 0
        for directory in (self.cuts_raw_dir, self.cuts_subs_dir):
            for file_path in directory.glob("*"):
                if not file_path.is_file():
                    continue
                try:
                    file_path.unlink()
                    print(f"  ✓ Usunięto: {file_path.relative_to(self.script_dir)}")
                    deleted_count += 1
                except Exception as exc:
                    print(f"  ✗ Nie udało się usunąć {file_path.name}: {exc}")

        metadata_keep = {"heatmap.json"}
        metadata_keep_suffixes = (".info.json", ".heatmap.json", ".md", ".txt", ".gitkeep")
        for file_path in self.metadata_dir.glob("*"):
            if not file_path.is_file():
                continue
            if file_path.name in metadata_keep or file_path.name.endswith(metadata_keep_suffixes):
                continue
            try:
                file_path.unlink()
                print(f"  ✓ Usunięto: {file_path.relative_to(self.script_dir)}")
                deleted_count += 1
            except Exception as exc:
                print(f"  ✗ Nie udało się usunąć {file_path.name}: {exc}")

        if self.windows_file.exists():
            try:
                self.windows_file.unlink()
                print(f"  ✓ Usunięto: {self.windows_file.relative_to(self.script_dir)}")
                deleted_count += 1
            except Exception as exc:
                print(f"  ✗ Nie udało się usunąć {self.windows_file.name}: {exc}")

        if deleted_count == 0:
            print("  (Brak starych artefaktów do usunięcia)")

    def get_gemini_api_key(self) -> Optional[str]:
        return os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY') or os.environ.get('API_KEY')

    def is_ssl_error(self, output: str) -> bool:
        message = (output or '').lower()
        return any(
            token in message
            for token in (
                'certificate_verify_failed',
                'ssl handshake failed',
                'openssl_uplink',
                'tls',
                'schannel',
                'crypt_e_no_revocation_check',
                'unable to get local issuer certificate',
            )
        )

    def build_gemini_probe_script(self, allow_insecure_fallback: bool = False) -> str:
        insecure_block = ""
        if allow_insecure_fallback:
            insecure_block = (
                "import ssl\n"
                "ssl._create_default_https_context = ssl._create_unverified_context\n"
                "os.environ['PYTHONHTTPSVERIFY'] = '0'\n"
            )

        return (
            "import json\n"
            "import os\n"
            "import urllib.request\n"
            "from pathlib import Path\n"
            "try:\n"
            "    import certifi\n"
            "except Exception:\n"
            "    certifi = None\n"
            "for env_name in ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'GRPC_DEFAULT_SSL_ROOTS_FILE_PATH'):\n"
            "    if certifi is not None:\n"
            "        os.environ[env_name] = certifi.where()\n"
            f"{insecure_block}"
            "try:\n"
            "    from dotenv import load_dotenv\n"
            "except Exception:\n"
            "    load_dotenv = None\n"
            "if load_dotenv is not None:\n"
            "    dotenv_path = Path.cwd() / '.env'\n"
            "    if dotenv_path.exists():\n"
            "        load_dotenv(dotenv_path)\n"
            "api_key = os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY') or os.environ.get('API_KEY')\n"
            "if not api_key:\n"
            "    raise SystemExit('Missing Gemini API key.')\n"
            "url = f'https://generativelanguage.googleapis.com/v1beta/models?key={api_key}&pageSize=1'\n"
            "with urllib.request.urlopen(url, timeout=20) as response:\n"
            "    payload = json.loads(response.read().decode('utf-8'))\n"
            "print(payload.get('models', [{}])[0].get('name', ''))\n"
        )

    def run_gemini_probe(self, allow_insecure_fallback: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, '-c', self.build_gemini_probe_script(allow_insecure_fallback)],
            capture_output=True,
            text=True,
            cwd=self.script_dir,
            env=self.build_subprocess_env(),
        )

    def test_gemini_via_curl(self, api_key: str) -> tuple[bool, str]:
        curl_binary = shutil.which('curl.exe') or shutil.which('curl')
        if not curl_binary:
            return False, "curl is not available in PATH."

        cmd = [
            curl_binary,
            '--silent',
            '--show-error',
            '--location',
            '--ssl-no-revoke',
            '--max-time',
            '30',
            f'https://generativelanguage.googleapis.com/v1beta/models?key={api_key}&pageSize=1',
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=self.script_dir,
            env=self.build_subprocess_env(),
        )
        output = (result.stdout or '') + (result.stderr or '')
        if result.returncode != 0:
            return False, output.strip()

        try:
            payload = json.loads(result.stdout or '{}')
            model_name = payload.get('models', [{}])[0].get('name', '')
            return True, model_name or 'Gemini model list OK'
        except json.JSONDecodeError:
            return False, output.strip()

    def verify_gemini_connection(self):
        if not allows_gemini(self.ai_mode):
            print("  Pomijam test Gemini, bo aktywny tryb to local_only.")
            return

        api_key = self.get_gemini_api_key()
        if not api_key:
            raise ManagerError("Brak klucza Gemini w .env (GOOGLE_API_KEY / GEMINI_API_KEY / API_KEY).")

        print("🔐 Testuję połączenie SSL z Gemini...")
        bootstrap_ssl_certificates()

        probe = self.run_gemini_probe()
        if probe.returncode == 0:
            model_name = (probe.stdout or '').strip() or 'unknown'
            self.gemini_transport = self.gemini_transport or 'sdk'
            os.environ['GEMINI_TRANSPORT'] = self.gemini_transport
            print(f"  ✓ Gemini reachable via Python SSL ({model_name})")
            return

        probe_output = ((probe.stdout or '') + "\n" + (probe.stderr or '')).strip()
        if self.is_ssl_error(probe_output):
            print("  Warning: Python SSL nadal odrzuca certyfikat Gemini. Próbuję trybu debugowego bez weryfikacji certyfikatu...")
            bootstrap_ssl_certificates(quiet=True, allow_insecure_fallback=True)
            insecure_probe = self.run_gemini_probe(allow_insecure_fallback=True)
            if insecure_probe.returncode == 0:
                model_name = (insecure_probe.stdout or '').strip() or 'unknown'
                self.gemini_transport = 'sdk'
                os.environ['GEMINI_TRANSPORT'] = self.gemini_transport
                print(f"  Warning: Gemini działa tylko z debug SSL fallback ({model_name}).")
                return

            curl_ok, curl_message = self.test_gemini_via_curl(api_key)
            if curl_ok:
                self.gemini_transport = 'curl'
                os.environ['GEMINI_TRANSPORT'] = 'curl'
                os.environ['CURL_SSL_NO_REVOKE'] = '1'
                print(f"  ✓ Gemini reachable via curl SSL fallback ({curl_message})")
                return

        diagnostics = probe_output or "Unknown Gemini SSL failure."
        if certifi is None and CERTIFI_IMPORT_ERROR is not None:
            diagnostics += f"\ncertifi import error: {CERTIFI_IMPORT_ERROR}"
        raise ManagerError(
            "Nie udało się zestawić połączenia z Gemini. "
            "Sprawdź certyfikaty systemowe / proxy albo uruchom `uv add certifi`.\n"
            f"Szczegóły:\n{diagnostics}"
        )

    def verify_external_tools(self):
        """Sprawdza, czy narzędzia wymagane do obróbki audio/wideo są dostępne."""
        missing_tools = [tool for tool in ('ffmpeg', 'ffprobe') if shutil.which(tool) is None]
        if missing_tools:
            tools = ', '.join(missing_tools)
            raise ManagerError(
                f"Brak wymaganego narzędzia: {tools}. "
                "Zainstaluj FFmpeg i upewnij się, że ffmpeg oraz ffprobe są dostępne w PATH. "
                "Windows: winget install Gyan.FFmpeg albo choco install ffmpeg, potem uruchom terminal ponownie."
            )
    
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
            result = subprocess.run(cmd, check=True, capture_output=True, text=True, env=self.build_subprocess_env())
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
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.build_subprocess_env())
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
            if self.has_video_stream(candidate) and self.has_audio_stream(candidate):
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
                subprocess.run(cmd, check=True, env=self.build_subprocess_env())
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
        if not self.url:
            print("✗ Nie podano URL do pobrania.")
            return False
        cmd = [
            sys.executable, str(self.script_dir / 'download_content.py'),
            self.url,
            '--input', str(self.input_dir),
            '--metadata', str(self.metadata_dir),
        ]
        
        return self.run_command(cmd, "1️⃣  Pobieranie wideo z YouTube")
    
    def transcribe_podcast(self) -> bool:
        """Krok 2: Transkrybuj audio."""
        if self.transcript_file.exists():
            print(f"  Plik transkrypcji już istnieje: {self.transcript_file.name}. Pomijam transkrypcję.")
            return True
        
        audio_file = self.find_latest_audio()
        if not audio_file:
            print("✗ Nie znaleziono pliku audio w input/")
            return False
        
        print(f"  Znalezione audio: {audio_file.name}")
        
        cmd = [
            sys.executable, str(self.script_dir / 'transcribe.py'),
            '--file', str(audio_file),
            '--out', str(self.transcript_file),
            '--backend', self.transcription_backend,
            '--whisper-model', self.whisper_model,
            '--device', self.transcription_device,
            '--compute-type', self.transcription_compute_type,
            '--diarization-backend', self.diarization_backend,
            '--max-speakers', str(self.diarization_max_speakers),
        ]
        if not self.enable_diarization:
            cmd.append('--disable-diarization')
        
        return self.run_command(cmd, "2️⃣  Transkrypcja audio")

    def check_subtitles(self) -> bool:
        """Krok 3: Zweryfikuj transkrypcję, timing i potencjalne halucynacje."""
        if self.subtitle_checker_mode == 'off':
            print("  Pomijam Subtitle Checker (subtitle-checker-mode=off).")
            return True
        if not self.transcript_file.exists():
            print(f"✗ Plik transkrypcji nie istnieje: {self.transcript_file}")
            return False

        audio_file = self.find_latest_audio()
        if not audio_file:
            print("✗ Nie znaleziono pliku audio w input/")
            return False

        if self.subtitle_check_report_file.exists() and not self.force_subtitle_checker:
            source_mtime = max(self.transcript_file.stat().st_mtime, audio_file.stat().st_mtime)
            if self.subtitle_check_report_file.stat().st_mtime >= source_mtime:
                try:
                    with open(self.subtitle_check_report_file, 'r', encoding='utf-8') as f:
                        report = json.load(f)
                    summary = report.get('summary', {})
                    status = summary.get('status', 'unknown')
                    score = summary.get('score', '?')
                    if status == 'fail':
                        if self.subtitle_checker_mode == 'local_only':
                            print(f"  Raport Subtitle Checkera ma status FAIL (score: {score}), ale tryb local_only traktuje go diagnostycznie. Pomijam blokadę.")
                            return True
                        print(f"✗ Istniejący raport AI Subtitler Checkera ma status FAIL (score: {score}).")
                        print(f"  Raport: {self.subtitle_check_report_file}")
                        print("  Użyj --force-subtitle-checker po poprawkach albo --skip-subtitle-checker, aby pominąć.")
                        return False
                    print(f"  Raport AI Subtitler Checkera aktualny: {status.upper()} (score: {score}). Pomijam.")
                    return True
                except Exception:
                    print("  Nie udało się odczytać istniejącego raportu checkera. Uruchamiam ponownie.")

        print(f"  Sprawdzane audio: {audio_file.name}")
        cmd = [
            sys.executable, str(self.script_dir / 'subtitler_checker.py'),
            '--audio', str(audio_file),
            '--transcript', str(self.transcript_file),
            '--report', str(self.subtitle_check_report_file),
        ]
        if self.subtitle_checker_mode == 'local_only':
            cmd.append('--warn-only')
        if self.auto_fix_subtitles:
            cmd.append('--fix')
        sample_limit = subtitle_checker_sample_limit(
            self.subtitle_checker_mode,
            default_full_samples=self.subtitle_checker_ai_samples,
        )
        if sample_limit <= 0:
            cmd.append('--skip-ai')
        else:
            cmd.extend(['--max-samples', str(sample_limit)])

        return self.run_command(cmd, "3️⃣  AI Subtitler Checker")
    
    def analyze_virals(self) -> bool:
        """Krok 4: Przeanalizuj i znajdź viralowe momenty."""
        if not self.transcript_file.exists():
            print(f"✗ Plik transkrypcji nie istnieje: {self.transcript_file}")
            return False
        
        if not self.heatmap_file.exists():
            print(f"✗ Plik heatmapy nie istnieje: {self.heatmap_file}")
            return False
        
        video_file = self.find_latest_video()
        cmd = [
            sys.executable, str(self.script_dir / 'analyze_virals.py'),
            '--transcript', str(self.transcript_file),
            '--heatmap', str(self.heatmap_file),
            '--save-json', str(self.windows_file),
            '--cutting-log', str(self.cutting_logic_file),
            '--ai-mode', self.ai_mode,
            '--content-profile', str(self.content_profile_file),
            '--content-type', self.content_type,
            '--layout-mode', self.layout_mode,
        ]
        if video_file:
            cmd.extend(['--video', str(video_file)])
        if self.skip_smart_context:
            cmd.append('--skip-smart-context')
        
        return self.run_command(cmd, "5️⃣  Analiza viralowych momentów")
    
    def classify_content(self) -> bool:
        """Krok 4: Zbuduj lokalny profil typu materiaĹ‚u."""
        if not self.transcript_file.exists():
            print(f"âś— Plik transkrypcji nie istnieje: {self.transcript_file}")
            return False

        cmd = [
            sys.executable, str(self.script_dir / 'content_classifier.py'),
            '--transcript', str(self.transcript_file),
            '--content-type', self.content_type,
            '--output', str(self.content_profile_file),
        ]
        if self.heatmap_file.exists():
            cmd.extend(['--heatmap', str(self.heatmap_file)])
        video_file = self.find_latest_video()
        if video_file:
            cmd.extend(['--video', str(video_file)])

        return self.run_command(cmd, "4. Content classification")

    def cut_segments(self) -> bool:
        """Krok 5: Wytnij segmenty z wideo."""
        if not self.windows_file.exists():
            print(f"✗ Plik okien nie istnieje: {self.windows_file}")
            return False
        
        video_file = self.find_latest_video()
        if not video_file:
            print("✗ Nie znaleziono pliku wideo w input/")
            return False
        
        print(f"  Znalezione wideo: {video_file.name}")
        
        cmd = [
            sys.executable, str(self.script_dir / 'cutter.py'),
            '--video', str(video_file),
            '--windows', str(self.windows_file),
            '--transcript', str(self.transcript_file),
            '--output-dir', str(self.cuts_raw_dir),
            '--cutting-log', str(self.cutting_logic_file),
            '--layout-mode', self.layout_mode,
        ]
        
        return self.run_command(cmd, "6️⃣  Wycinanie segmentów")
    
    def add_subtitles(self) -> bool:
        """Krok 6: Dodaj napisy na wycinki."""
        if not self.transcript_file.exists():
            print(f"✗ Plik transkrypcji nie istnieje: {self.transcript_file}")
            return False
        
        cmd = [
            sys.executable, str(self.script_dir / 'subtitler.py'),
            '--transcript', str(self.transcript_file),
            '--input-dir', str(self.cuts_raw_dir),
            '--output-raw', str(self.cuts_raw_dir),
            '--output-subs', str(self.cuts_subs_dir),
        ]
        
        return self.run_command(cmd, "7️⃣  Dodawanie napisów")
    
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
        refinement_status = "UNKNOWN"
        transport = (self.gemini_transport or os.environ.get('GEMINI_TRANSPORT') or 'unknown').upper()
        clip_count = len(subs_files)
        requested = 0
        refined = 0
        fallback = 0
        ai_status = ''
        if self.cutting_logic_file.exists():
            try:
                with open(self.cutting_logic_file, 'r', encoding='utf-8') as file_handle:
                    cutting_log = json.load(file_handle)
                requested = int(cutting_log.get('clips_requested') or 0)
                refined = int(cutting_log.get('clips_refined_with_ai') or 0)
                fallback = int(cutting_log.get('clips_with_local_fallback') or 0)
                ai_status = str(cutting_log.get('ai_status') or '').strip()
                log_transport = str(cutting_log.get('ai_transport') or '').strip()
                if log_transport:
                    transport = log_transport.upper()
                if str(cutting_log.get('ai_mode') or '').strip() == 'local_only':
                    refinement_status = 'LOCAL ONLY'
                else:
                    refinement_status = 'FULL SUCCESS' if requested and refined == requested and fallback == 0 else 'PARTIAL'
            except Exception:
                refinement_status = 'UNKNOWN'
        print(f"  Wygenerowano {clip_count} klipów, AI refinement: {refinement_status} (Transport: {transport})")
        print(f"  AI sukcesy: {refined}/{requested} | Fallbacki: {fallback}")
        print(f"  Selection mode: {self.ai_mode} | AI status: {ai_status or 'n/a'}")
        if self.content_profile_file.exists():
            try:
                with open(self.content_profile_file, 'r', encoding='utf-8') as file_handle:
                    content_profile = json.load(file_handle)
                print(
                    f"  Content type: {content_profile.get('content_type', 'unknown')} | "
                    f"Strategy: {content_profile.get('strategy_name', 'unknown')} | "
                    f"Confidence: {content_profile.get('confidence', 0.0)} | "
                    f"Source: {content_profile.get('source', 'unknown')}"
                )
            except Exception:
                pass
        print()
    
    def run(self):
        """Uruchamia cały workflow."""
        print("\n" + "="*60)
        print("🚀 VIRAL CUTTER AI — WORKFLOW MANAGER")
        print("="*60)
        print(f"URL: {self.url if self.url else '(local input mode)'}")
        print(f"AI Mode: {self.ai_mode}")
        print(f"Skip Download: {'Tak' if self.skip_download else 'Nie'}")
        print(f"Skip Subtitle Checker: {'Tak' if self.skip_subtitle_checker else 'Nie'}")
        print(f"Subtitle Checker Mode: {self.subtitle_checker_mode}")
        print(f"Skip Smart Context: {'Tak' if self.skip_smart_context else 'Nie'}")
        print(f"Transcription Backend: {self.transcription_backend} ({self.whisper_model})")
        print(
            f"Diarization: {'Tak' if self.enable_diarization else 'Nie'}"
            f" [{self.diarization_backend}, max speakers: {self.diarization_max_speakers}]"
        )
        print(f"Content Type: {self.content_type}")
        print(f"Layout Mode: {self.layout_mode}")
        print(f"Auto Fix Subtitles: {'Tak' if self.auto_fix_subtitles else 'Nie'}")
        print(f"Cleanup: {'Tak' if self.cleanup else 'Nie'}")
        print()
        
        try:
            # Przygotowanie
            self.verify_external_tools()
            self.ensure_directories()
            self.cleanup_previous_run()
            if allows_gemini(self.ai_mode):
                try:
                    self.verify_gemini_connection()
                    self.gemini_available = True
                except ManagerError:
                    self.gemini_available = False
                    if requires_gemini(self.ai_mode):
                        raise
                    print("  Warning: Gemini verification failed. Continuing with local fallback.")
            else:
                self.gemini_available = False
            
            # Workflow
            steps = []
            has_video = self.find_latest_video() is not None
            has_transcript = self.transcript_file.exists()

            if has_video and has_transcript:
                print("  Znaleziono istniejące pliki w input/ i transcripts/. Pomijam pobieranie i transkrypcję.")
            else:
                if self.skip_download:
                    if has_video:
                        print("  Pomijam pobieranie wideo (plik już jest w input/).")
                    else:
                        print("  Brak pliku wideo w input/. Wykonam pobieranie mimo --skip-download.")
                        steps.append((self.download_content, "Pobieranie"))

                    if not has_transcript:
                        steps.append((self.transcribe_podcast, "Transkrypcja"))
                else:
                    if not has_video:
                        if not self.url:
                            raise ManagerError("Brak pliku wideo w input/ i nie podano --url do pobrania.")
                        steps.append((self.download_content, "Pobieranie"))
                    if not has_transcript:
                        steps.append((self.transcribe_podcast, "Transkrypcja"))

            if self.skip_subtitle_checker:
                print("  Pomijam AI Subtitler Checker (--skip-subtitle-checker).")
            else:
                steps.append((self.check_subtitles, "AI Subtitler Checker"))

            steps.extend([
                (self.classify_content, "Klasyfikacja typu materiału"),
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
        description='Orkiestracja workflow: download -> transkrypcja -> checker -> analiza -> cutting -> napisy',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Przykłady:
  python manager.py --url "https://www.youtube.com/watch?v=..."
  python manager.py --url "..." --cleanup
  python manager.py --url "..." --skip-download  (do testowania, pomiń pobieranie)
  python manager.py --url "..." --skip-subtitle-checker  (debug bez checkera)
        '''
    )
    
    parser.add_argument(
        '--url',
        required=False,
        help='Adres URL do wideo na YouTube. Opcjonalny, jesli masz juz pliki w input/.',
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

    parser.add_argument(
        '--skip-subtitle-checker',
        action='store_true',
        help='Pomiń AI Subtitler Checker',
    )

    parser.add_argument(
        '--force-subtitle-checker',
        action='store_true',
        help='Uruchom AI Subtitler Checker nawet jeśli raport jest aktualny',
    )

    parser.add_argument(
        '--skip-smart-context',
        action='store_true',
        help='Pomiń analizę Gemini i użyj lokalnych granic z heatmapy',
    )

    parser.add_argument(
        '--ai-mode',
        choices=VALID_AI_MODES,
        default='gemini_optional',
        help='Tryb pipeline: local_only, gemini_optional albo gemini_enabled',
    )

    parser.add_argument(
        '--subtitle-checker-mode',
        choices=VALID_SUBTITLE_CHECKER_MODES,
        default=None,
        help='Tryb checkera: off, local_only, limited albo full',
    )

    parser.add_argument(
        '--subtitle-checker-ai-samples',
        type=int,
        default=8,
        help='Liczba probek AI dla subtitle checkera w trybie full',
    )

    parser.add_argument(
        '--transcription-backend',
        default='faster_whisper',
        help='Backend transkrypcji. Obecnie: faster_whisper',
    )

    parser.add_argument(
        '--whisper-model',
        default='small',
        help='Model faster-whisper, np. tiny, base, small albo medium',
    )

    parser.add_argument(
        '--transcription-device',
        default='auto',
        help='Device dla transkrypcji: auto, cpu albo cuda',
    )

    parser.add_argument(
        '--transcription-compute-type',
        default='auto',
        help='Compute type dla faster-whisper: auto, int8, float16 itd.',
    )

    parser.set_defaults(enable_diarization=True)
    parser.add_argument(
        '--enable-diarization',
        dest='enable_diarization',
        action='store_true',
        help='Wlacz lokalne speaker attribution po transkrypcji (domyslnie wlaczone)',
    )
    parser.add_argument(
        '--disable-diarization',
        dest='enable_diarization',
        action='store_false',
        help='Wylacz diarization i ustaw fallback Speaker 0',
    )

    parser.add_argument(
        '--diarization-backend',
        default='heuristic_cluster',
        help='Backend diarization. Obecnie: heuristic_cluster',
    )

    parser.add_argument(
        '--diarization-max-speakers',
        type=int,
        default=4,
        help='Maksymalna liczba speakerow przypisywanych lokalnie',
    )

    parser.add_argument(
        '--content-type',
        choices=VALID_CONTENT_TYPE_MODES,
        default='auto',
        help='Typ materialu: auto albo podcast. MVP routuje obie opcje jako podcast/talking-head.',
    )

    parser.add_argument(
        '--layout-mode',
        choices=VALID_LAYOUT_MODES,
        default='auto',
        help='Override layoutu 9:16: auto albo speaker_face_crop.',
    )

    parser.set_defaults(auto_fix_subtitles=True)
    parser.add_argument(
        '--auto-fix-subtitles',
        action='store_true',
        help='Automatycznie napraw wykryte błędy w transkrypcji (domyślnie włączone)',
    )
    parser.add_argument(
        '--no-auto-fix-subtitles',
        dest='auto_fix_subtitles',
        action='store_false',
        help='Uruchom checker bez zapisywania automatycznych poprawek',
    )
    
    return parser.parse_args()


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    if load_dotenv is not None:
        dotenv_path = Path(__file__).parent / '.env'
        if dotenv_path.exists():
            load_dotenv(dotenv_path)
    args = parse_args()
    
    manager = WorkflowManager(
        url=args.url,
        cleanup=args.cleanup,
        skip_download=args.skip_download,
        skip_subtitle_checker=args.skip_subtitle_checker,
        skip_smart_context=args.skip_smart_context,
        force_subtitle_checker=args.force_subtitle_checker,
        auto_fix_subtitles=args.auto_fix_subtitles,
        ai_mode=args.ai_mode,
        subtitle_checker_mode=args.subtitle_checker_mode,
        subtitle_checker_ai_samples=args.subtitle_checker_ai_samples,
        transcription_backend=args.transcription_backend,
        whisper_model=args.whisper_model,
        transcription_device=args.transcription_device,
        transcription_compute_type=args.transcription_compute_type,
        enable_diarization=args.enable_diarization,
        diarization_backend=args.diarization_backend,
        diarization_max_speakers=args.diarization_max_speakers,
        content_type=args.content_type,
        layout_mode=args.layout_mode,
    )
    
    manager.run()


if __name__ == '__main__':
    main()
