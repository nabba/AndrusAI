"""subia.introspection.topics — Phase 18 per-topic gatherers + formatters.

Each topic module exposes two pure-ish functions:

  gather() -> dict
      Pulls live data from the relevant SubIA store/kernel attribute.
      Wrapped defensively — failure returns an empty dict, never raises.

  format(data: dict) -> str
      Renders the gathered data as a system-prompt section. Returns
      empty string if data is empty so the composer can omit the
      section entirely.

The pipeline detects which topics are relevant from the user message
and runs only the corresponding handlers, keeping the system-prompt
prefix focused (not dumping all 8 topics every time).
"""
from . import (
    beliefs, technical, chronicle, scene,
    wonder_shadow, scorecard, predictions, social,
)

# Topic → (gather_fn, format_fn) registry.
# The pipeline imports this and routes per detected topic.
def _import_topic_handlers():
    """Lazy registry — avoids circular imports at module load."""
    from app.subia.introspection.detector import IntrospectionTopic as T
    return {
        T.BELIEFS:      (beliefs.gather,       beliefs.format_section),
        T.TECHNICAL:    (technical.gather,     technical.format_section),
        T.HISTORY:      (chronicle.gather,     chronicle.format_section),
        T.SCENE:        (scene.gather,         scene.format_section),
        T.WONDER:       (wonder_shadow.gather_wonder, wonder_shadow.format_wonder_section),
        T.SHADOW:       (wonder_shadow.gather_shadow, wonder_shadow.format_shadow_section),
        T.SCORECARD:    (scorecard.gather,     scorecard.format_section),
        T.PREDICTIONS:  (predictions.gather,   predictions.format_section),
        T.SOCIAL_MODEL: (social.gather,        social.format_section),
    }


__all__ = [
    "beliefs", "technical", "chronicle", "scene",
    "wonder_shadow", "scorecard", "predictions", "social",
    "_import_topic_handlers",
]
