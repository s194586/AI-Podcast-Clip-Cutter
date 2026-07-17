from __future__ import annotations

from collections.abc import Callable

from .executor import PipelineStage
from .stages import (
    CleanupInputStage,
    DownloadMediaStage,
    GenerateCandidatesStage,
    ImportCandidatesStage,
    MarkProjectReadyStage,
    PrepareWorkspaceStage,
    RenderInitialClipsStage,
    ReviewCandidatesStage,
    TranscribeAudioStage,
    ValidateTranscriptStage,
)


PROJECT_STAGE_ORDER = (
    "prepare_workspace",
    "download_source",
    "transcribe",
    "validate_transcript",
    "generate_candidates",
    "import_candidates",
    "review_boundaries",
    "mark_ready",
)


class PipelineStageRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., PipelineStage]] = {
            "prepare_workspace": PrepareWorkspaceStage,
            "download_source": DownloadMediaStage,
            "transcribe": TranscribeAudioStage,
            "validate_transcript": ValidateTranscriptStage,
            "generate_candidates": GenerateCandidatesStage,
            "import_candidates": ImportCandidatesStage,
            "review_boundaries": ReviewCandidatesStage,
            "mark_ready": MarkProjectReadyStage,
            "render_initial_clips": RenderInitialClipsStage,
            "cleanup_input": CleanupInputStage,
        }

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._factories)

    def create(self, stage_name: str, **options) -> PipelineStage:
        name = str(stage_name or "").strip()
        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise ValueError(f"Unknown pipeline stage: {name!r}") from exc
        return factory(**options)


DEFAULT_STAGE_REGISTRY = PipelineStageRegistry()
