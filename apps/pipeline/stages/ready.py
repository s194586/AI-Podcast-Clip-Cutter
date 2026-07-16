from __future__ import annotations

from apps.api.db.database import init_database, session_scope
from apps.api.db.models import utc_now
from apps.api.db.repositories import ProjectRepository

from ..context import PipelineContext
from ..exceptions import PipelineCancelled, PipelineError
from ..results import PipelineStageResult


class MarkProjectReadyStage:
    stage = "ready"

    def run(self, context: PipelineContext) -> PipelineStageResult:
        context.raise_if_cancelled()
        if context.project_id is None:
            return PipelineStageResult(
                stage=self.stage,
                success=True,
                message="Legacy workflow completed.",
            )
        init_database()
        try:
            with session_scope() as session:
                repository = ProjectRepository(session)
                project = repository.get(context.project_id)
                if project is None:
                    raise PipelineError(f"Unknown project_id: {context.project_id}")
                context.raise_if_cancelled()
                if project.status == "cancelled":
                    raise PipelineCancelled("Project was cancelled before it could be marked ready.")
                repository.update_flow_state(
                    project,
                    status="ready",
                    current_stage="ready",
                    progress_percent=100.0,
                    error_message=None,
                    completed_at=utc_now(),
                )
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(f"Project readiness update failed: {exc}") from exc
        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message="Project is ready for human review.",
            metadata={"project_id": context.project_id},
        )
