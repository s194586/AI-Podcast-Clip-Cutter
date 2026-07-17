from __future__ import annotations

from .context import PipelineContext
from .executor import PipelineStage
from .registry import DEFAULT_STAGE_REGISTRY


def legacy_cli_stages(context: PipelineContext) -> tuple[PipelineStage, ...]:
    stages: list[PipelineStage] = [
        DEFAULT_STAGE_REGISTRY.create("prepare_workspace"),
        DEFAULT_STAGE_REGISTRY.create("download_source"),
        DEFAULT_STAGE_REGISTRY.create("transcribe"),
        DEFAULT_STAGE_REGISTRY.create("validate_transcript"),
        DEFAULT_STAGE_REGISTRY.create("generate_candidates"),
    ]
    if not context.analysis_only:
        stages.append(DEFAULT_STAGE_REGISTRY.create("render_initial_clips"))
    stages.append(DEFAULT_STAGE_REGISTRY.create("cleanup_input"))
    return tuple(stages)


def project_pipeline_stages(context: PipelineContext) -> tuple[PipelineStage, ...]:
    stages: list[PipelineStage] = [
        DEFAULT_STAGE_REGISTRY.create("prepare_workspace"),
        DEFAULT_STAGE_REGISTRY.create("download_source"),
        DEFAULT_STAGE_REGISTRY.create("transcribe"),
        DEFAULT_STAGE_REGISTRY.create("validate_transcript"),
        DEFAULT_STAGE_REGISTRY.create("generate_candidates"),
        DEFAULT_STAGE_REGISTRY.create("import_candidates"),
    ]
    if context.auto_review:
        stages.append(DEFAULT_STAGE_REGISTRY.create("review_boundaries"))
    stages.append(DEFAULT_STAGE_REGISTRY.create("mark_ready"))
    return tuple(stages)
