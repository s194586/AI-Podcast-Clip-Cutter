from .base import SelectionStrategy
from .podcast import PodcastStrategy


STRATEGY_REGISTRY = {
    "podcast": PodcastStrategy,
}


def get_strategy(name: str | None) -> SelectionStrategy:
    return PodcastStrategy()


__all__ = [
    "PodcastStrategy",
    "SelectionStrategy",
    "get_strategy",
]
