"""Friday's creative review is a real review that can fail (#8150).

It used to approve on every path: a failed review call returned
"APPROVED", and the orchestrator read a key the review never sets
(``approved``, default True), so even "VERDICT: REVISE" counted as approval.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.orchestrator import RecipeOrchestrator


def _orchestrator(tmp: Path) -> RecipeOrchestrator:
    return RecipeOrchestrator(
        data_dir=tmp / "output", message_storage=tmp / "messages", memory_storage=tmp / "memories",
    )


@pytest.mark.parametrize("reply, approved", [
    ("Lovely crumb.\nVERDICT: APPROVED", True),
    ("The title oversells it.\nVERDICT: REVISE", False),
])
def test_the_review_verdict_decides_approval(reply, approved):
    with tempfile.TemporaryDirectory() as tmpdir, \
         patch("backend.utils.model_router.generate_response", return_value=reply):
        result = _orchestrator(Path(tmpdir))._execute_stage_review("rid", {"recipe": {"title": "Egg Cups"}})
    assert result[0] is approved
    assert result[1]["verdict"] == ("APPROVED" if approved else "REVISE")


def test_a_failed_review_call_fails_the_stage_instead_of_approving():
    with tempfile.TemporaryDirectory() as tmpdir, \
         patch("backend.utils.model_router.generate_response", side_effect=TimeoutError("upstream")), \
         pytest.raises(RuntimeError, match="review call failed: TimeoutError"):
        _orchestrator(Path(tmpdir))._execute_stage_review("rid", {"recipe": {"title": "Egg Cups"}})
