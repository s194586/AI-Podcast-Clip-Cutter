from __future__ import annotations


class PipelineError(RuntimeError):
    """Base class for controlled pipeline failures."""


class WorkspacePreparationError(PipelineError):
    pass


class DownloadStageError(PipelineError):
    pass


class TranscriptionStageError(PipelineError):
    pass


class TranscriptValidationError(PipelineError):
    pass


class CandidateGenerationError(PipelineError):
    pass


class CandidateImportError(PipelineError):
    pass


class ReviewStageError(PipelineError):
    pass


class RenderStageError(PipelineError):
    pass


class PipelineCancelled(PipelineError):
    pass
