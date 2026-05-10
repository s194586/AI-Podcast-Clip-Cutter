from .base import SelectionStrategy


class GameplayStrategy(SelectionStrategy):
    def __init__(self) -> None:
        super().__init__(
            name="gameplay",
            content_type="gameplay",
            description="Bias toward reactive, high-energy gameplay moments without ignoring basic story completeness.",
            score_weights={
                "heatmap_avg": 0.20,
                "heatmap_peak": 0.14,
                "importance_score": 0.14,
                "speech_density_score": 0.06,
                "emotion_score": 0.16,
                "punchiness_score": 0.14,
                "hook_score": 0.05,
                "payoff_score": 0.08,
                "speaker_turn_score": 0.06,
                "duration_fit_score": 0.03,
                "chaos_score": 0.08,
                "repetition_penalty": 0.06,
            },
            candidate_preferences={
                "prefer_reactions": True,
                "prefer_voice_comms": True,
                "prefer_visual_activity": True,
            },
            render_hints={
                "crop_mode": "gameplay_balanced",
                "preserve_gameplay_frame": True,
                "facecam_priority": "secondary",
            },
        )
