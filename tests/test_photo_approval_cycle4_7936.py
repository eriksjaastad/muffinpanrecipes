"""Targeted offline regressions for the fourth #7936 review."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from backend.admin import cron_routes
from backend.utils import photo_review
from scripts import cleanup_image_backlog as sweep
from scripts.simulate_dialogue_week import _build_dynamic_arc
from tests.photo_review_helpers import decide, register, wednesday_stage
from tests.test_photo_approval_7936 import EP_ID, _Store, _episode, _request


@pytest.mark.parametrize("state", ["awaiting", "rejected"])
def test_cleanup_preserves_unmirrored_request_candidates(tmp_path, monkeypatch, state):
    images = tmp_path / "images"
    image = images / "r1" / "round_1" / "macro.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"photo")
    (images / "r1.png").write_bytes(b"old winner")
    controls = tmp_path / "controls"
    controls.mkdir()
    (controls / "v000001.json").write_text(json.dumps({
        "state": state, "request": {"candidates": [
            {"path": "src/assets/images/r1/round_1/macro.png"},
        ]}, "decision": None if state == "awaiting" else {"status": "rejected"},
    }))
    monkeypatch.setattr(sweep, "IMAGES_DIR", images)
    monkeypatch.setattr(sweep, "EPISODES_DIR", tmp_path / "missing-episodes")
    monkeypatch.setattr(sweep, "PHOTO_CONTROL_DIR", controls)
    trash = MagicMock()
    monkeypatch.setattr("send2trash.send2trash", trash)
    monkeypatch.setattr("sys.argv", ["cleanup_image_backlog.py", "--execute"])
    sweep.main()
    trash.assert_not_called()
    assert image.read_bytes() == b"photo"


@pytest.mark.parametrize("body", ["{broken", "null", '{"request": {"candidates": [null]}}'])
def test_cleanup_refuses_unreadable_control_before_trashing(tmp_path, monkeypatch, body):
    controls = tmp_path / "controls"
    controls.mkdir()
    (controls / "v000001.json").write_text(body)
    monkeypatch.setattr(sweep, "PHOTO_CONTROL_DIR", controls)
    monkeypatch.setattr(sweep, "EPISODES_DIR", tmp_path / "missing-episodes")
    with pytest.raises(ValueError):
        sweep.protected_image_paths()


@pytest.mark.parametrize("state", ["awaiting", "rejected", "approved"])
@pytest.mark.parametrize("mirror", ["absent", "no_photography", "present"])
def test_friday_passes_authoritative_decision_to_actual_prompt(state, mirror):
    wed = wednesday_stage()
    ep = _episode(wed)
    store = _Store()
    register(EP_ID, wed, store)
    if state != "awaiting":
        decide(EP_ID, ep, action="reject" if state == "rejected" else "select", store=store)
    if mirror == "absent":
        ep["stages"].pop("wednesday")
    elif mirror == "no_photography":
        ep["stages"]["wednesday"].pop("photography_data")
    store.put(EP_ID, ep)
    orchestrator = MagicMock()
    orchestrator._execute_stage_review.return_value = (True, {})
    dialogue = MagicMock(return_value=([], "PASS"))
    body = cron_routes.StageRequest(episode_id=EP_ID, force=True)
    with patch.object(cron_routes, "storage", store), \
         patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes, "_parse_body", return_value=body), \
         patch.object(cron_routes, "_get_orchestrator", return_value=MagicMock(return_value=orchestrator)), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", dialogue), \
         patch.object(cron_routes, "regenerate_and_upload"):
        asyncio.run(cron_routes.cron_friday(_request(path="/api/cron/friday")))
    context = dialogue.call_args.kwargs["photography_context"]
    expected = photo_review.dialogue_context(photo_review.read_view(store, EP_ID, ep))
    assert context["human_review"] == expected
    if mirror == "present":
        assert context["rounds"] == wed["photography_data"]["rounds"]
    arc = _build_dynamic_arc("friday", ep["concept"], context)
    assert "Photos were selected Wednesday" not in arc
    assert "approved Wednesday" not in arc
    if state == "awaiting":
        assert "waiting on the site editor" in arc
    elif state == "rejected":
        assert "rejected" in arc.lower()
    else:
        assert "macro_closeup" in arc
