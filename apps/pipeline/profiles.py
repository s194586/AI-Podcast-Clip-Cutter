from __future__ import annotations

from .context import PipelineContext
from .runner import PipelineStage
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


def legacy_cli_stages(context: PipelineContext) -> tuple[PipelineStage, ...]:
    stages: list[PipelineStage] = [
        PrepareWorkspaceStage(),
        DownloadMediaStage(),
        TranscribeAudioStage(),
        ValidateTranscriptStage(),
        GenerateCandidatesStage(),
    ]
    if not context.analysis_only:
        stages.append(RenderInitialClipsStage())
    stages.append(CleanupInputStage())
    return tuple(stages)


def project_pipeline_stages(context: PipelineContext) -> tuple[PipelineStage, ...]:
    stages: list[PipelineStage] = [
        PrepareWorkspaceStage(),
        DownloadMediaStage(),
        TranscribeAudioStage(),
        ValidateTranscriptStage(),
        GenerateCandidatesStage(),
        ImportCandidatesStage(),
    ]
    if context.auto_review:
        stages.append(ReviewCandidatesStage())
    stages.append(MarkProjectReadyStage())
    return tuple(stages)
