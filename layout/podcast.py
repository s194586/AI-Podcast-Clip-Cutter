from __future__ import annotations

from .base import LayoutProfile


def build_podcast_layout() -> LayoutProfile:
    return LayoutProfile(
        content_type="podcast",
        layout_mode="speaker_face_crop",
        layout_policy="face_active_speaker",
        crop_priority="speaker_face",
        allow_face_tracking=True,
        face_tracking_weight=0.9,
        preserve_full_frame=False,
        blur_background=False,
        safe_center_crop=False,
        max_crop_motion=0.08,
        smoothing_strength=0.82,
        min_face_area_for_tracking=0.015,
        ignore_edge_faces=False,
    )
