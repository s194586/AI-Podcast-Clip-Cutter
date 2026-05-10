from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SelectionStrategy:
    name: str
    content_type: str
    description: str
    score_weights: dict[str, float]
    candidate_preferences: dict[str, Any] = field(default_factory=dict)
    render_hints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "content_type": self.content_type,
            "description": self.description,
            "score_weights": dict(self.score_weights),
            "candidate_preferences": dict(self.candidate_preferences),
            "render_hints": dict(self.render_hints),
        }
