from __future__ import annotations

from .base import VALID_LAYOUT_MODES, LayoutProfile, is_vertical_9_16, normalize_layout_mode
from .podcast import build_podcast_layout


LAYOUT_BUILDERS = {
    "podcast": build_podcast_layout,
}


OVERRIDE_LAYOUT_BUILDERS = {
    "speaker_face_crop": build_podcast_layout,
}


def get_layout_profile(content_type: str | None, layout_mode: str | None = "auto") -> LayoutProfile:
    normalized_type = "podcast"
    normalized_mode = normalize_layout_mode(layout_mode)

    if normalized_mode != "auto":
        builder = OVERRIDE_LAYOUT_BUILDERS.get(normalized_mode, build_podcast_layout)
        profile = builder()
        if normalized_mode == "vertical_crop":
            return LayoutProfile(
                content_type=normalized_type,
                layout_mode=profile.layout_mode,
                layout_policy=profile.layout_policy,
                crop_priority=profile.crop_priority,
                allow_face_tracking=profile.allow_face_tracking,
                face_tracking_weight=profile.face_tracking_weight,
                preserve_full_frame=False,
                blur_background=False,
                safe_center_crop=profile.safe_center_crop,
                output_width=profile.output_width,
                output_height=profile.output_height,
                output_aspect_ratio=profile.output_aspect_ratio,
                max_crop_motion=profile.max_crop_motion,
                smoothing_strength=profile.smoothing_strength,
                min_face_area_for_tracking=profile.min_face_area_for_tracking,
                ignore_edge_faces=profile.ignore_edge_faces,
            )
        return LayoutProfile(content_type=normalized_type, **{k: v for k, v in profile.to_dict().items() if k != "content_type"})

    builder = LAYOUT_BUILDERS.get(normalized_type, build_podcast_layout)
    return builder()


__all__ = [
    "LayoutProfile",
    "VALID_LAYOUT_MODES",
    "get_layout_profile",
    "is_vertical_9_16",
    "normalize_layout_mode",
]
