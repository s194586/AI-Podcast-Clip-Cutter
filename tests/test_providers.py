import json
import tempfile
import unittest
from pathlib import Path

from providers.transcription_provider import (
    AssemblyAIProvider,
    DeepgramProvider,
    Segment,
    TranscriptResult,
)
from providers.video_understanding_provider import (
    ClipCandidate,
    GeminiVideoProvider,
    VideoUnderstandingResult,
)


class ProviderAdapterTests(unittest.TestCase):
    def test_transcript_result_serializes_to_json(self):
        payload = TranscriptResult(
            provider="assemblyai",
            language="pl",
            speaker_count=2,
            segments=[
                Segment(start=0.0, end=1.2, text="hello", speaker="Speaker 0", confidence=0.98),
                Segment(start=1.2, end=2.4, text="world", speaker="Speaker 1", confidence=0.95),
            ],
            metadata={"job_id": "abc"},
        )

        encoded = json.dumps(payload.to_dict(), ensure_ascii=False)

        self.assertIn('"provider": "assemblyai"', encoded)
        self.assertIn('"speaker": "Speaker 0"', encoded)

    def test_video_understanding_result_serializes_to_json(self):
        payload = VideoUnderstandingResult(
            provider="gemini_video",
            candidates=[
                ClipCandidate(
                    candidate_start=10.0,
                    candidate_end=25.0,
                    story_score=0.8,
                    hook_score=0.7,
                    context_score=0.6,
                    payoff_score=0.9,
                    reason="good payoff",
                )
            ],
            metadata={"model": "models/gemini-2.5-flash"},
        )

        encoded = json.dumps(payload.to_dict(), ensure_ascii=False)

        self.assertIn('"provider": "gemini_video"', encoded)
        self.assertIn('"story_score": 0.8', encoded)

    def test_assemblyai_placeholder_fails_clearly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "sample.mp4"
            media.write_bytes(b"data")
            with self.assertRaises(RuntimeError) as ctx:
                AssemblyAIProvider().transcribe(media)
        self.assertIn("ASSEMBLYAI_API_KEY", str(ctx.exception))

    def test_deepgram_placeholder_fails_clearly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "sample.mp4"
            media.write_bytes(b"data")
            with self.assertRaises(RuntimeError) as ctx:
                DeepgramProvider().transcribe(media)
        self.assertIn("DEEPGRAM_API_KEY", str(ctx.exception))

    def test_gemini_video_placeholder_fails_clearly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "sample.mp4"
            media.write_bytes(b"data")
            with self.assertRaises(RuntimeError) as ctx:
                GeminiVideoProvider().analyze(media)
        self.assertIn("placeholder", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
