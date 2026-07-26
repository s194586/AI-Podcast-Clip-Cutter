from __future__ import annotations

from heatmap_contract import atomic_write_json, load_heatmap_document
from heatmap_peaks import PeakDetectorConfig, detect_heatmap_peaks

from ..context import PipelineContext
from ..exceptions import HeatmapPeakDetectionError
from ..results import PipelineStageResult


class DetectHeatmapPeaksStage:
    stage = "detecting_heatmap_peaks"

    def run(self, context: PipelineContext) -> PipelineStageResult:
        heatmap_document = load_heatmap_document(context.heatmap_file)
        peak_document = detect_heatmap_peaks(heatmap_document, PeakDetectorConfig())
        try:
            atomic_write_json(context.heatmap_peaks_file, peak_document)
        except OSError as exc:
            raise HeatmapPeakDetectionError(
                f"Heatmap peak results could not be written: {exc}"
            ) from exc

        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message="Detected local replay-interest peaks.",
            produced_artifacts=(context.safe_artifact(context.heatmap_peaks_file),),
            metadata={
                "peak_count": len(peak_document["peaks"]),
                "algorithm": peak_document["algorithm"],
                "algorithm_version": peak_document["algorithm_version"],
            },
        )
