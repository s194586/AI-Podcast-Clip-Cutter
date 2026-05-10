from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from pathlib import Path
import subprocess
import time
from typing import Any

import numpy as np

from transcription.base import TranscriptSegment, normalize_speaker_label

from .base import DiarizationBackend, DiarizationConfig, DiarizationResult


FRAME_SIZE = 400
HOP_SIZE = 160
EPSILON = 1e-8


@dataclass
class _Cluster:
    centroid: np.ndarray
    indices: list[int]


class HeuristicDiarizationBackend(DiarizationBackend):
    name = "heuristic_cluster"

    def __init__(self, config: DiarizationConfig):
        self.config = config

    def assign_speakers(self, audio_path: Path, segments: list[TranscriptSegment]) -> DiarizationResult:
        started_at = time.perf_counter()
        if not self.config.enabled:
            self._apply_fallback(segments)
            return DiarizationResult(
                backend=self.name,
                enabled=False,
                status="disabled",
                speaker_count=1 if segments else 0,
                diarization_seconds=0.0,
                used_fallback=True,
                extra_metadata=self._build_diagnostics(segments, eligible_segments=0, assigned_segments=0),
            )

        try:
            waveform = self._load_audio(audio_path, sample_rate=self.config.sample_rate)
            feature_rows, feature_indices = self._extract_features(waveform, segments, self.config.sample_rate)
            if len(feature_rows) < 2:
                self._apply_fallback(segments)
                return DiarizationResult(
                    backend=self.name,
                    enabled=True,
                    status="fallback_single_speaker",
                    speaker_count=1 if segments else 0,
                    diarization_seconds=time.perf_counter() - started_at,
                    used_fallback=True,
                    extra_metadata=self._build_diagnostics(
                        segments,
                        eligible_segments=len(feature_rows),
                        assigned_segments=0,
                    ),
                )

            labels = self._cluster_features(feature_rows)
            label_map = self._normalize_labels(labels, feature_indices)

            for segment_index, cluster_label in zip(feature_indices, labels):
                segments[segment_index].speaker = label_map[cluster_label]

            self._fill_unassigned_segments(segments)
            speaker_count = len({segment.speaker for segment in segments if segment.speaker})
            return DiarizationResult(
                backend=self.name,
                enabled=True,
                status="applied",
                speaker_count=speaker_count,
                diarization_seconds=time.perf_counter() - started_at,
                used_fallback=False,
                extra_metadata=self._build_diagnostics(
                    segments,
                    eligible_segments=len(feature_rows),
                    assigned_segments=len(feature_indices),
                    cluster_label_distribution=dict(sorted(Counter(labels).items())),
                ),
            )
        except Exception as exc:
            self._apply_fallback(segments)
            return DiarizationResult(
                backend=self.name,
                enabled=True,
                status="fallback_error",
                speaker_count=1 if segments else 0,
                diarization_seconds=time.perf_counter() - started_at,
                used_fallback=True,
                extra_metadata=self._build_diagnostics(
                    segments,
                    eligible_segments=0,
                    assigned_segments=0,
                    error=str(exc),
                ),
            )

    def _apply_fallback(self, segments: list[TranscriptSegment]) -> None:
        for segment in segments:
            segment.speaker = "Speaker 0"

    def _load_audio(self, audio_path: Path, sample_rate: int) -> np.ndarray:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "s16le",
            "-",
        ]
        result = subprocess.run(cmd, capture_output=True, check=True)
        waveform = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        return waveform

    def _extract_features(
        self,
        waveform: np.ndarray,
        segments: list[TranscriptSegment],
        sample_rate: int,
    ) -> tuple[np.ndarray, list[int]]:
        features: list[np.ndarray] = []
        indices: list[int] = []
        for index, segment in enumerate(segments):
            duration = segment.end - segment.start
            if duration < self.config.min_segment_seconds:
                continue
            feature_row = self._segment_features(
                waveform,
                sample_rate,
                start=segment.start,
                end=segment.end,
            )
            if feature_row is None:
                continue
            features.append(feature_row)
            indices.append(index)
        if not features:
            return np.zeros((0, 0), dtype=np.float32), indices
        matrix = np.vstack(features)
        means = matrix.mean(axis=0, keepdims=True)
        stds = matrix.std(axis=0, keepdims=True)
        normalized = (matrix - means) / np.maximum(stds, EPSILON)
        return normalized.astype(np.float32), indices

    def _segment_features(
        self,
        waveform: np.ndarray,
        sample_rate: int,
        *,
        start: float,
        end: float,
    ) -> np.ndarray | None:
        start_index = max(0, int(start * sample_rate))
        end_index = min(len(waveform), int(end * sample_rate))
        chunk = waveform[start_index:end_index]
        if len(chunk) < FRAME_SIZE:
            return None

        chunk = chunk - np.mean(chunk)
        rms = math.sqrt(float(np.mean(chunk**2)) + EPSILON)
        zcr = float(np.mean(np.abs(np.diff(np.signbit(chunk)).astype(np.float32))))

        frames = self._frame_audio(chunk)
        window = np.hanning(FRAME_SIZE).astype(np.float32)
        spectra = np.abs(np.fft.rfft(frames * window, axis=1)) + EPSILON
        power = spectra**2
        freqs = np.fft.rfftfreq(FRAME_SIZE, d=1.0 / sample_rate)

        spectral_sum = power.sum(axis=1) + EPSILON
        centroids = (power * freqs).sum(axis=1) / spectral_sum
        bandwidths = np.sqrt(((freqs - centroids[:, None]) ** 2 * power).sum(axis=1) / spectral_sum)

        cumulative = np.cumsum(power, axis=1)
        rolloff_threshold = spectral_sum[:, None] * 0.85
        rolloff_indices = (cumulative >= rolloff_threshold).argmax(axis=1)
        rolloffs = freqs[rolloff_indices]

        pitch_mask = (freqs >= 70) & (freqs <= 350)
        if np.any(pitch_mask):
            dominant_pitch = freqs[pitch_mask][power[:, pitch_mask].mean(axis=0).argmax()]
        else:
            dominant_pitch = 0.0

        band_edges = np.geomspace(80, sample_rate / 2, num=9)
        band_energies: list[float] = []
        for left, right in zip(band_edges[:-1], band_edges[1:]):
            mask = (freqs >= left) & (freqs < right)
            if not np.any(mask):
                band_energies.append(0.0)
                continue
            band_energies.append(float(np.log(power[:, mask].mean() + EPSILON)))

        feature_vector = np.array(
            [
                np.log(rms + EPSILON),
                zcr,
                float(np.mean(centroids) / max(sample_rate, 1)),
                float(np.std(centroids) / max(sample_rate, 1)),
                float(np.mean(bandwidths) / max(sample_rate, 1)),
                float(np.mean(rolloffs) / max(sample_rate, 1)),
                float(dominant_pitch / max(sample_rate, 1)),
                *band_energies,
            ],
            dtype=np.float32,
        )
        return feature_vector

    def _frame_audio(self, chunk: np.ndarray) -> np.ndarray:
        if len(chunk) < FRAME_SIZE:
            return np.zeros((0, FRAME_SIZE), dtype=np.float32)
        frame_count = 1 + max(0, (len(chunk) - FRAME_SIZE) // HOP_SIZE)
        frames = np.zeros((frame_count, FRAME_SIZE), dtype=np.float32)
        for frame_index in range(frame_count):
            start = frame_index * HOP_SIZE
            frames[frame_index] = chunk[start : start + FRAME_SIZE]
        return frames

    def _cluster_features(self, feature_rows: np.ndarray) -> list[int]:
        clusters: list[_Cluster] = []
        labels: list[int] = []

        for row_index, row in enumerate(feature_rows):
            if not clusters:
                clusters.append(_Cluster(centroid=row.copy(), indices=[row_index]))
                labels.append(0)
                continue

            similarities = [self._cosine_similarity(row, cluster.centroid) for cluster in clusters]
            best_index = int(np.argmax(similarities))
            best_similarity = similarities[best_index]

            if (
                best_similarity < self.config.similarity_threshold
                and len(clusters) < self.config.max_speakers
            ):
                new_label = len(clusters)
                clusters.append(_Cluster(centroid=row.copy(), indices=[row_index]))
                labels.append(new_label)
                continue

            labels.append(best_index)
            clusters[best_index].indices.append(row_index)
            clusters[best_index].centroid = feature_rows[clusters[best_index].indices].mean(axis=0)

        for _ in range(2):
            for cluster_index, cluster in enumerate(clusters):
                cluster.indices = [row_index for row_index, label in enumerate(labels) if label == cluster_index]
                if cluster.indices:
                    cluster.centroid = feature_rows[cluster.indices].mean(axis=0)

            for row_index, row in enumerate(feature_rows):
                similarities = [self._cosine_similarity(row, cluster.centroid) for cluster in clusters]
                labels[row_index] = int(np.argmax(similarities))

        return labels

    def _normalize_labels(self, labels: list[int], feature_indices: list[int]) -> dict[int, str]:
        ordered_labels: list[int] = []
        for segment_index, label in sorted(zip(feature_indices, labels), key=lambda item: item[0]):
            if label not in ordered_labels:
                ordered_labels.append(label)
        return {label: normalize_speaker_label(f"Speaker {index}") for index, label in enumerate(ordered_labels)}

    def _fill_unassigned_segments(self, segments: list[TranscriptSegment]) -> None:
        last_speaker = "Speaker 0"
        for segment in segments:
            if segment.speaker:
                last_speaker = segment.speaker
            else:
                segment.speaker = last_speaker

        next_speaker = "Speaker 0"
        for segment in reversed(segments):
            if segment.speaker:
                next_speaker = segment.speaker
            else:
                segment.speaker = next_speaker

        counts = Counter(segment.speaker for segment in segments if segment.speaker)
        fallback_speaker = counts.most_common(1)[0][0] if counts else "Speaker 0"
        for segment in segments:
            if not segment.speaker:
                segment.speaker = fallback_speaker

    def _build_diagnostics(
        self,
        segments: list[TranscriptSegment],
        *,
        eligible_segments: int,
        assigned_segments: int,
        **extra: Any,
    ) -> dict[str, Any]:
        speakers = [normalize_speaker_label(segment.speaker) for segment in segments if segment.speaker]
        speaker_counts = Counter(speakers)
        switches = sum(1 for left, right in zip(speakers, speakers[1:]) if left != right)
        dominant_ratio = speaker_counts.most_common(1)[0][1] / len(speakers) if speakers else 0.0
        diagnostics = {
            "eligible_segments": int(eligible_segments),
            "assigned_segments": int(assigned_segments),
            "max_speakers": self.config.max_speakers,
            "speaker_distribution": dict(sorted(speaker_counts.items())),
            "speaker_switches": switches,
            "dominant_speaker_ratio": round(float(dominant_ratio), 4),
        }
        diagnostics.update(extra)
        return diagnostics

    def _cosine_similarity(self, left: np.ndarray, right: np.ndarray) -> float:
        denominator = (np.linalg.norm(left) * np.linalg.norm(right)) + EPSILON
        return float(np.dot(left, right) / denominator)
