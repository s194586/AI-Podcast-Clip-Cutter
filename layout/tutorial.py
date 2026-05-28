from __future__ import annotations

from .base import LayoutProfile


def build_tutorial_layout() -> LayoutProfile:
    return LayoutProfile(
        content_type="tutorial",
        layout_mode="full_frame_blur_background",
        layout_policy="screen_preserve_blur_bg",
        crop_priority="screen",
        allow_face_tracking=False,
        face_tracking_weight=0.0,
        preserve_full_frame=True,
        blur_background=True,
        safe_center_crop=True,
        max_crop_motion=0.0,
        smoothing_strength=1.0,
        min_face_area_for_tracking=0.0,
        ignore_edge_faces=False,
    )
