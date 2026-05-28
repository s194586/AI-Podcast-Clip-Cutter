from __future__ import annotations

from .base import LayoutProfile


def build_generic_layout() -> LayoutProfile:
    return LayoutProfile(
        content_type="generic",
        layout_mode="safe_center_crop",
        layout_policy="safe_center_crop",
        crop_priority="center",
        allow_face_tracking=False,
        face_tracking_weight=0.0,
        preserve_full_frame=False,
        blur_background=False,
        safe_center_crop=True,
        max_crop_motion=0.0,
        smoothing_strength=1.0,
        min_face_area_for_tracking=0.0,
        ignore_edge_faces=False,
    )
