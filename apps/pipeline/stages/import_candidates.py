from __future__ import annotations

from apps.api.db.database import init_database, session_scope
from apps.api.db.repositories import ClipRepository
from apps.api.services.legacy_import_service import import_candidate_file_into_project

from ..context import PipelineContext
from ..exceptions import CandidateImportError
from ..results import PipelineStageResult


class ImportCandidatesStage:
    stage = "importing_candidates"

    def run(self, context: PipelineContext) -> PipelineStageResult:
        if context.project_id is None:
            raise CandidateImportError("Candidate import requires an existing project_id.")
        if not context.candidate_file.exists():
            raise CandidateImportError("Candidate import requires top_windows.json in the project workspace.")

        init_database()
        try:
            with session_scope() as session:
                project = import_candidate_file_into_project(
                    session,
                    project_id=context.project_id,
                    project_root=context.repository_root,
                    workspace_root=context.workspace_path,
                )
                if project is None:
                    raise CandidateImportError(
                        f"Candidate clips could not be imported into project {context.project_id}."
                    )
                clip_count = len(ClipRepository(session).list_for_project(context.project_id))
        except CandidateImportError:
            raise
        except Exception as exc:
            raise CandidateImportError(f"Candidate import failed: {exc}") from exc
        if clip_count <= 0:
            raise CandidateImportError("Candidate import produced no project clips.")
        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message=f"Imported {clip_count} candidate clip(s) into the existing project.",
            produced_artifacts=(context.safe_artifact(context.candidate_file),),
            metadata={"project_id": context.project_id, "clip_count": clip_count},
        )
