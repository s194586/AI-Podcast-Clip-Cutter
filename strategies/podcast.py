from .base import SelectionStrategy


class PodcastStrategy(SelectionStrategy):
    def __init__(self) -> None:
        super().__init__(
            name="podcast",
            content_type="podcast",
            description="Favor conversational clarity, speaker turns and complete hook-to-payoff dialogue beats.",
            score_weights={
                "heatmap_avg": 0.12,
                "heatmap_peak": 0.06,
                "importance_score": 0.10,
                "speech_density_score": 0.16,
                "emotion_score": 0.05,
                "punchiness_score": 0.05,
                "hook_score": 0.12,
                "payoff_score": 0.12,
                "speaker_turn_score": 0.14,
                "duration_fit_score": 0.04,
                "chaos_score": 0.10,
                "repetition_penalty": 0.08,
            },
            candidate_preferences={
                "prefer_conversation": True,
                "prefer_semantic_completeness": True,
                "prefer_face_driven_crop": True,
            },
            render_hints={
                "crop_mode": "speaker_focus",
                "facecam_priority": "primary",
                "preserve_screen_content": False,
            },
        )
