from .base import SelectionStrategy


class GenericStrategy(SelectionStrategy):
    def __init__(self) -> None:
        super().__init__(
            name="generic",
            content_type="generic",
            description="Safe fallback strategy with balanced local scoring across heatmap, story shape and clarity.",
            score_weights={
                "heatmap_avg": 0.34,
                "heatmap_peak": 0.12,
                "importance_score": 0.12,
                "speech_density_score": 0.10,
                "emotion_score": 0.10,
                "punchiness_score": 0.08,
                "hook_score": 0.08,
                "payoff_score": 0.08,
                "speaker_turn_score": 0.05,
                "duration_fit_score": 0.05,
                "chaos_score": 0.04,
                "repetition_penalty": 0.06,
            },
            candidate_preferences={
                "prefer_complete_story": True,
                "prefer_clear_delivery": True,
            },
            render_hints={
                "crop_mode": "balanced",
                "preserve_screen_content": True,
            },
        )
