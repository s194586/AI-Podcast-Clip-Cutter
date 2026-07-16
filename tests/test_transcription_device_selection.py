import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from transcription import FasterWhisperBackend, TranscriptionConfig


class FakeSegment:
    def __init__(self, text: str, *, start: float = 0.0, end: float = 3.0):
        self.start = start
        self.end = end
        self.text = text
        self.words = []


class FakeCudaAvailable:
    @staticmethod
    def get_cuda_device_count():
        return 1


class TranscriptionDeviceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.audio_path = self.root / "audio.wav"
        self.audio_path.write_bytes(b"fake audio")
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()

    def tearDown(self):
        self.tempdir.cleanup()

    def _config(self, *, device="auto", compute_type="auto") -> TranscriptionConfig:
        return TranscriptionConfig(
            model=str(self.model_dir),
            device=device,
            compute_type=compute_type,
            cache_dir=self.root / "cache",
        )

    def test_auto_mode_initially_selects_cuda_when_available(self):
        calls = []

        class FakeWhisperModel:
            def __init__(self, model_reference, *, device, compute_type, download_root):
                calls.append({"device": device, "compute_type": compute_type})

            def transcribe(self, audio_path, **kwargs):
                return iter([FakeSegment("CUDA transcript.")]), SimpleNamespace(language="en", duration=3.0)

        with patch.dict(sys.modules, {"ctranslate2": FakeCudaAvailable}):
            with patch("transcription.faster_whisper_backend.WhisperModel", FakeWhisperModel):
                result = FasterWhisperBackend(self._config()).transcribe(self.audio_path)

        self.assertEqual(calls, [{"device": "cuda", "compute_type": "float16"}])
        self.assertEqual(result.device, "cuda")
        self.assertEqual(result.compute_type, "float16")

    def test_missing_cublas_during_transcribe_triggers_cpu_int8_fallback(self):
        calls = []

        class FakeWhisperModel:
            def __init__(self, model_reference, *, device, compute_type, download_root):
                self.device = device
                calls.append({"device": device, "compute_type": compute_type})

            def transcribe(self, audio_path, **kwargs):
                if self.device == "cuda":
                    raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                return iter([FakeSegment("CPU fallback transcript.")]), SimpleNamespace(language="en", duration=3.0)

        with patch.dict(sys.modules, {"ctranslate2": FakeCudaAvailable}):
            with patch("transcription.faster_whisper_backend.WhisperModel", FakeWhisperModel):
                result = FasterWhisperBackend(self._config()).transcribe(self.audio_path)

        self.assertEqual(
            calls,
            [
                {"device": "cuda", "compute_type": "float16"},
                {"device": "cpu", "compute_type": "int8"},
            ],
        )
        self.assertEqual(result.device, "cpu_fallback")
        self.assertEqual(result.compute_type, "int8")
        self.assertEqual(result.segments[0].text, "CPU fallback transcript.")

    def test_failure_while_consuming_segments_triggers_fallback(self):
        calls = []

        def failing_segments():
            yield FakeSegment("Partial CUDA transcript.")
            raise RuntimeError("cuBLAS runtime failed while decoding")

        class FakeWhisperModel:
            def __init__(self, model_reference, *, device, compute_type, download_root):
                self.device = device
                calls.append(device)

            def transcribe(self, audio_path, **kwargs):
                if self.device == "cuda":
                    return failing_segments(), SimpleNamespace(language="en", duration=3.0)
                return iter([FakeSegment("Recovered CPU transcript.")]), SimpleNamespace(language="en", duration=3.0)

        with patch.dict(sys.modules, {"ctranslate2": FakeCudaAvailable}):
            with patch("transcription.faster_whisper_backend.WhisperModel", FakeWhisperModel):
                result = FasterWhisperBackend(self._config()).transcribe(self.audio_path)

        self.assertEqual(calls, ["cuda", "cpu"])
        self.assertEqual(result.device, "cpu_fallback")
        self.assertEqual(result.segments[0].text, "Recovered CPU transcript.")

    def test_fallback_happens_at_most_once(self):
        calls = []

        class FakeWhisperModel:
            def __init__(self, model_reference, *, device, compute_type, download_root):
                self.device = device
                calls.append(device)

            def transcribe(self, audio_path, **kwargs):
                raise RuntimeError(f"CUDA runtime unavailable during {self.device}")

        with patch.dict(sys.modules, {"ctranslate2": FakeCudaAvailable}):
            with patch("transcription.faster_whisper_backend.WhisperModel", FakeWhisperModel):
                with self.assertRaisesRegex(RuntimeError, "CUDA runtime unavailable"):
                    FasterWhisperBackend(self._config()).transcribe(self.audio_path)

        self.assertEqual(calls, ["cuda", "cpu"])

    def test_explicit_cpu_never_initializes_cuda(self):
        calls = []

        class FakeWhisperModel:
            def __init__(self, model_reference, *, device, compute_type, download_root):
                calls.append({"device": device, "compute_type": compute_type})

            def transcribe(self, audio_path, **kwargs):
                return iter([FakeSegment("CPU transcript.")]), SimpleNamespace(language="en", duration=3.0)

        with patch.dict(sys.modules, {"ctranslate2": FakeCudaAvailable}):
            with patch("transcription.faster_whisper_backend.WhisperModel", FakeWhisperModel):
                result = FasterWhisperBackend(self._config(device="cpu")).transcribe(self.audio_path)

        self.assertEqual(calls, [{"device": "cpu", "compute_type": "int8"}])
        self.assertEqual(result.device, "cpu")

    def test_explicit_cuda_returns_clear_error_without_cpu_fallback(self):
        calls = []

        class FakeWhisperModel:
            def __init__(self, model_reference, *, device, compute_type, download_root):
                self.device = device
                calls.append(device)

            def transcribe(self, audio_path, **kwargs):
                raise RuntimeError("CUDA driver could not load cuBLAS")

        with patch("transcription.faster_whisper_backend.WhisperModel", FakeWhisperModel):
            with self.assertRaisesRegex(RuntimeError, "TRANSCRIPTION_DEVICE=auto or TRANSCRIPTION_DEVICE=cpu"):
                FasterWhisperBackend(self._config(device="cuda")).transcribe(self.audio_path)

        self.assertEqual(calls, ["cuda"])

    def test_partial_transcript_from_failed_cuda_attempt_is_discarded(self):
        class FakeWhisperModel:
            def __init__(self, model_reference, *, device, compute_type, download_root):
                self.device = device

            def transcribe(self, audio_path, **kwargs):
                if self.device == "cuda":
                    return self._cuda_segments(), SimpleNamespace(language="en", duration=4.0)
                return iter([FakeSegment("Only final CPU text.")]), SimpleNamespace(language="en", duration=4.0)

            def _cuda_segments(self):
                yield FakeSegment("This partial text must disappear.")
                raise RuntimeError("cudnn runtime is not available")

        with patch.dict(sys.modules, {"ctranslate2": FakeCudaAvailable}):
            with patch("transcription.faster_whisper_backend.WhisperModel", FakeWhisperModel):
                result = FasterWhisperBackend(self._config()).transcribe(self.audio_path)

        texts = [segment.text for segment in result.segments]
        self.assertEqual(texts, ["Only final CPU text."])
        self.assertNotIn("partial", " ".join(texts).lower())


if __name__ == "__main__":
    unittest.main()
