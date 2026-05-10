from .base import SelectionStrategy


class TutorialStrategy(SelectionStrategy):
    def __init__(self) -> None:
        super().__init__(
            name="tutorial",
            content_type="tutorial",
            description="Favor clarity, complete explanations and readable pacing over raw emotional spikes.",
            score_weights={
                "heatmap_avg": 0.10,
                "heatmap_peak": 0.05,
                "importance_score": 0.08,
                "speech_density_score": 0.18,
                "emotion_score": 0.03,
                "punchiness_score": 0.03,
                "hook_score": 0.12,
                "payoff_score": 0.12,
                "speaker_turn_score": 0.04,
                "duration_fit_score": 0.10,
                "chaos_score": 0.13,
                "repetition_penalty": 0.12,
            },
            candidate_preferences={
                "prefer_clarity": True,
                "prefer_complete_sentences": True,
                "prefer_readable_screen_content": True,
            },
            render_hints={
                "crop_mode": "content_preserving",
                "preserve_screen_content": True,
                "facecam_priority": "secondary",
            },
        )
