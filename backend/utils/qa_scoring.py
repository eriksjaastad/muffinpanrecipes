"""QA scoring re-exports for backend use.

Provides a clean import path so production code (cron_routes) doesn't
import directly from scripts/.  The actual scoring logic lives in
scripts/simulate_dialogue_week.py — a full extraction is tracked
separately.
"""

from scripts.simulate_dialogue_week import Message, load_personas, score_quality

__all__ = ["Message", "load_personas", "score_quality"]
