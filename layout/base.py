from __future__ import annotations

from dataclasses import asdict, dataclass


DEFAULT_OUTPUT_WIDTH = 1080
DEFAULT_OUTPUT_HEIGHT = 1920
DEFAULT_OUTPUT_ASPECT_RATIO = "9:16"

VALID_LAYOUT_MODES = (
    "auto",
    "speaker_face_crop",
)


@dataclass(frozen=True)
class LayoutProfile:
    content_type: str
    layout_mode: str
    layout_policy: str
    crop_priority: str
    allow_face_tracking: bool
    face_tracking_weight: float
    preserve_full_frame: bool
    blur_background: bool
    safe_center_crop: bool
    output_width: int = DEFAULT_OUTPUT_WIDTH
    output_height: int = DEFAULT_OUTPUT_HEIGHT
    output_aspect_ratio: str = DEFAULT_OUTPUT_ASPECT_RATIO
    max_crop_motion: float = 0.12
    smoothing_strength: float = 0.7
    min_face_area_for_tracking: float = 0.02
    ignore_edge_faces: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_render_hints(self) -> dict[str, object]:
        payload = self.to_dict()
        payload["face_tracking_allowed"] = self.allow_face_tracking
        return payload


def normalize_layout_mode(layout_mode: str | None) -> str:
    normalized = str(layout_mode or "auto").strip().lower() or "auto"
    return normalized if normalized in VALID_LAYOUT_MODES else "auto"


def is_vertical_9_16(width: int | float, height: int | float) -> bool:
    try:
        width_value = int(round(float(width)))
        height_value = int(round(float(height)))
    except Exception:
        return False
    return width_value > 0 and height_value > 0 and width_value * 16 == height_value * 9
