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
    ("I nearly wrote VERDICT: APPROVED, but the crust is raw.\nVERDICT: REVISE", False),
    ("Lovely crumb.\n**VERDICT: APPROVED.**", True),
    ("VERDICT: APPROVED\nOn reflection, the crust is raw.\nVERDICT: REVISE", False),
    ("Fine either way.\nVERDICT: APPROVED / REVISE", False),
    ("Lovely crumb.\n### **VERDICT:** APPROVED", True),
    ("VERDICT: APPROVED\nActually the crust is raw.\nVERDICT: REVISE - fix the crust", False),
])
def test_the_review_verdict_decides_approval(reply, approved):
    with tempfile.TemporaryDirectory() as tmpdir, \
         patch("backend.utils.model_router.generate_response", return_value=reply):
        result = _orchestrator(Path(tmpdir))._execute_stage_review("rid", {"title": "Egg Cups"}, "An intro.")
    assert result[0] is approved
    assert result[1]["verdict"] == ("APPROVED" if approved else "REVISE")


def test_a_failed_review_call_fails_the_stage_instead_of_approving():
    with tempfile.TemporaryDirectory() as tmpdir, \
         patch("backend.utils.model_router.generate_response", side_effect=TimeoutError("upstream")), \
         pytest.raises(RuntimeError, match="review call failed: TimeoutError"):
        _orchestrator(Path(tmpdir))._execute_stage_review("rid", {"title": "Egg Cups"}, "An intro.")


def test_a_package_without_copy_is_not_approved_and_not_sent_to_steph():
    with tempfile.TemporaryDirectory() as tmpdir, \
         patch("backend.utils.model_router.generate_response") as model:
        approved, output = _orchestrator(Path(tmpdir))._execute_stage_review("rid", {"title": "Egg Cups"}, None)
    assert approved is False and "no copy" in output["feedback"][0]
    model.assert_not_called()


def test_a_rejected_recipe_stops_before_deployment():
    with tempfile.TemporaryDirectory() as tmpdir:
        orch = _orchestrator(Path(tmpdir))
        with patch.object(orch, "_execute_stage_baker", return_value={"title": "Egg Cups"}), \
             patch.object(orch, "_execute_stage_photography", return_value={}), \
             patch.object(orch, "_execute_stage_copywriting", return_value={"body": "An intro."}), \
             patch.object(orch, "_execute_stage_review", return_value=(False, {"feedback": ["raw"]})), \
             patch.object(orch, "_execute_stage_deployment") as deploy, \
             patch("backend.orchestrator.notify_pipeline_failure"), \
             pytest.raises(RuntimeError, match="asked for revisions"):
            orch.produce_recipe("Egg Cups")
    deploy.assert_not_called()
