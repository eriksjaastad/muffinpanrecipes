"""Chain-of-thought leak guard tests (#5919 / #5920).

Haiku occasionally ignores 'write only the message' and spills interiority
like 'What Steph actually feels: ...'. These tests lock in the regex
detector and the retry-once-then-fail behavior.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestHasCotLeak:
    def test_steph_actually_feels(self):
        from scripts.simulate_dialogue_week import _has_cot_leak

        assert _has_cot_leak(
            "What Steph actually feels: Margaret's right but she won't say it."
        )

    def test_margaret_thinks(self):
        from scripts.simulate_dialogue_week import _has_cot_leak

        assert _has_cot_leak("What Margaret thinks: this ratio is off")

    def test_julian_wants(self):
        from scripts.simulate_dialogue_week import _has_cot_leak

        assert _has_cot_leak("What Julian actually wants: a five-minute break.")

    def test_how_devon_feels(self):
        from scripts.simulate_dialogue_week import _has_cot_leak

        assert _has_cot_leak("How Devon feels: tired and overextended.")

    def test_would_say(self):
        from scripts.simulate_dialogue_week import _has_cot_leak

        assert _has_cot_leak("What Marcus would say: something long and literary.")

    def test_normal_dialogue_not_flagged(self):
        from scripts.simulate_dialogue_week import _has_cot_leak

        assert not _has_cot_leak("I think the flour ratio is off.")
        assert not _has_cot_leak("Actually, that might work.")
        assert not _has_cot_leak("What about a different approach?")
        assert not _has_cot_leak("")

    def test_leak_only_matches_line_start(self):
        from scripts.simulate_dialogue_week import _has_cot_leak

        # A leak buried in the middle of a message is still text the
        # model intended; we only guard against leading-interiority patterns.
        assert not _has_cot_leak(
            "I mean, I know what Steph actually feels here, but I disagree."
        )


class TestGuardCotLeak:
    def test_clean_msg_passes_through(self):
        from scripts.simulate_dialogue_week import _guard_cot_leak

        persona = {"name": "Steph Whitmore"}
        result = _guard_cot_leak(
            "Margaret, I think the ratio works.",
            prompt="...", persona=persona, model="fake-model",
        )
        assert result == "Margaret, I think the ratio works."

    def test_retry_on_first_leak(self):
        from scripts.simulate_dialogue_week import _guard_cot_leak

        persona = {"name": "Steph Whitmore"}
        with patch("scripts.simulate_dialogue_week.generate_response") as mock_gen, \
             patch("scripts.simulate_dialogue_week.build_system_prompt", return_value="sys"):
            mock_gen.return_value = "I disagree about the ratio."
            result = _guard_cot_leak(
                "What Steph actually feels: Margaret is wrong.",
                prompt="...", persona=persona, model="fake-model",
            )
        assert result == "I disagree about the ratio."
        mock_gen.assert_called_once()
        # Retry prompt should contain the correction
        assert "CRITICAL CORRECTION" in mock_gen.call_args.kwargs["prompt"]

    def test_double_leak_raises(self):
        from scripts.simulate_dialogue_week import _guard_cot_leak

        persona = {"name": "Steph Whitmore"}
        with patch("scripts.simulate_dialogue_week.generate_response") as mock_gen, \
             patch("scripts.simulate_dialogue_week.build_system_prompt", return_value="sys"):
            mock_gen.return_value = "What Steph actually feels: this is still broken"
            with pytest.raises(RuntimeError, match="CoT leak after retry"):
                _guard_cot_leak(
                    "What Steph actually feels: Margaret is wrong.",
                    prompt="...", persona=persona, model="fake-model",
                )
