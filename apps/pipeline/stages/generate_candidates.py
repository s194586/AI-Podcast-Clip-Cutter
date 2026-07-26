from __future__ import annotations

import json

from candidate_windows import CandidateWindowConfig, generate_candidate_windows
from heatmap_contract import atomic_write_json

from ..context import PipelineContext
from ..exceptions import CandidateGenerationError
from ..results import PipelineStageResult


class GenerateCandidatesStage:
    stage = "generating_candidates"

    def run(self, context: PipelineContext) -> PipelineStageResult:
        if not context.heatmap_peaks_file.exists():
            raise CandidateGenerationError(
                "Candidate generation requires metadata/heatmap_peaks.json."
            )
        try:
            with context.heatmap_peaks_file.open(encoding="utf-8") as file_handle:
                peak_document = json.load(file_handle)
            candidate_document = generate_candidate_windows(
                peak_document, CandidateWindowConfig()
            )
        except Exception as exc:
            raise CandidateGenerationError(
                f"Candidate windows could not be generated: {exc}"
            ) from exc

        try:
            atomic_write_json(context.candidate_windows_file, candidate_document)
        except OSError as exc:
            raise CandidateGenerationError(
                f"Canonical candidate windows could not be written: {exc}"
            ) from exc

        adapter = _compatibility_adapter(candidate_document)
        try:
            atomic_write_json(context.candidate_file, adapter)
        except OSError as exc:
            raise CandidateGenerationError(
                f"Candidate compatibility adapter could not be written: {exc}"
            ) from exc

        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message="Generated replay-interest candidate windows.",
            produced_artifacts=(
                context.safe_artifact(context.candidate_windows_file),
                context.safe_artifact(context.candidate_file),
            ),
            metadata={
                "candidate_count": len(candidate_document["candidates"]),
                "generator": candidate_document["generator"],
                "generator_version": candidate_document["generator_version"],
                "canonical_artifact": context.safe_artifact(context.candidate_windows_file),
                "compatibility_adapter": context.safe_artifact(context.candidate_file),
            },
        )


def _compatibility_adapter(candidate_document: dict) -> dict:
    """Build the minimal legacy ``top_windows.json`` adapter from canonical candidates."""
    return {
        "schema_version": 2,
        "source": "candidate_windows_compatibility_adapter",
        "canonical_artifact": "metadata/candidate_windows.json",
        "candidate_id_scheme": candidate_document["candidate_id_scheme"],
        "candidate_id_version": candidate_document["candidate_id_version"],
        "top_windows": [
            {
                "id": candidate["candidate_id"],
                "candidate_id": candidate["candidate_id"],
                "rank": candidate["rank"],
                "source_peak_rank": candidate["source_peak_rank"],
                "peak_time": candidate["peak_time"],
                "start": candidate["start_time"],
                "end": candidate["end_time"],
                "duration": candidate["duration_seconds"],
                "boundary_source": "replay_interest_peak",
                "selection_source": "youtube_most_replayed",
                "replay_interest": candidate["replay_interest"],
            }
            for candidate in candidate_document["candidates"]
        ],
    }
