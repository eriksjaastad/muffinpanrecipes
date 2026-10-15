"""Dashboard "Remake this photo" (#8073): one new image in a new set, kept others.

Reuses the Reshoot fixtures: the real photo-control log backs every test; the
image model, the upload and the email are the only things replaced. No network.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from backend.admin import cron_routes
from backend.agents.factory import create_agent
from backend.utils import photo_review
from backend.utils.pan_library import pan_for_week
from tests.photo_review_helpers import BLOB
from tests.test_art_director_image_pipeline import _png_bytes
from tests.test_photo_approval_7936 import EP_ID, _SAME_ORIGIN, _client, _control
from tests.test_photo_reshoot_8073 import _finish, _start, _store_with_set

NEW_PATH = "src/assets/images/r1/g20261009T000000Z-cccccc/round_1/overhead_flatlay.png"


def _shot(tmp_path):
    local = tmp_path / "new.png"
    local.write_bytes(b"png")
    return {"variant": "overhead_flatlay", "path": NEW_PATH, "local_path": str(local), "prompt": "p"}


def _remake(client, wed, path):
    return client.post(
        f"/admin/episodes/{EP_ID}/photos/remake",
        json={"image_set_id": wed["photo_review"]["image_set_id"], "path": path}, headers=_SAME_ORIGIN,
    )


@contextmanager
def _remake_env(store, tmp_path):
    orch = MagicMock()
    orch.return_value._execute_remake_photo.return_value = _shot(tmp_path)
    saved = []
    store.save_image = lambda p, _b: saved.append(p) or f"{BLOB}/{p.split('images/')[1]}"
    dialogue = MagicMock()
    client, patcher = _client(store)
    with patch.object(cron_routes, "storage", store), \
         patch.object(cron_routes, "_get_orchestrator", return_value=orch), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", dialogue), \
         patch.object(cron_routes, "notify_photos_ready", return_value=True) as notify:
        try:
            yield client, orch.return_value._execute_remake_photo, saved, notify, dialogue
        finally:
            patcher.stop()


def test_remade_review_replaces_only_the_remade_image_with_a_valid_new_set(tmp_path):
    store, wed = _store_with_set()
    request = _control(store)["request"]
    old = request["candidates"][1]
    new = photo_review.remade_review(request, old["path"], _shot(tmp_path), f"{BLOB}/r1/new.png")

    assert photo_review._valid_request(new)
    assert new["image_set_id"] != request["image_set_id"]
    assert [c["path"] for c in new["candidates"]] == [
        request["candidates"][0]["path"], NEW_PATH, request["candidates"][2]["path"],
    ]
    assert [c["url"] for c in new["candidates"]][::2] == [c["url"] for c in request["candidates"]][::2]
    assert new["image_paths"][1] == NEW_PATH and new["image_urls"][1] == f"{BLOB}/r1/new.png"


def test_remaking_the_team_pick_leaves_no_winner_naming_the_removed_image(tmp_path):
    store, _wed = _store_with_set()
    request = _control(store)["request"]
    winner_path = request["wednesday_snapshot"]["confirmed_winner"]["path"]
    new = photo_review.remade_review(request, winner_path, _shot(tmp_path), f"{BLOB}/r1/new.png")
    assert new["wednesday_snapshot"]["confirmed_winner"] == {}
    kept = photo_review.remade_review(request, request["candidates"][1]["path"], _shot(tmp_path), f"{BLOB}/r1/new.png")
    assert kept["wednesday_snapshot"]["confirmed_winner"]["path"] == winner_path


def test_remake_variant_makes_one_image_call_with_the_weeks_pan_and_no_featured_image(tmp_path, monkeypatch):
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "ROOT", tmp_path)
    agent = create_agent("art_director")
    prompts = []
    monkeypatch.setattr(agent, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(agent, "_call_image_model", lambda _k, prompt, **_a: prompts.append(prompt) or _png_bytes())
    recipe = {"title": "Test Muffins"}

    shot = agent.remake_variant("test-recipe", recipe, "overhead_flatlay", episode_id="2026-W41",
                                previous_pan_material="aluminum")

    pan = pan_for_week("2026-W41", avoid="aluminum")
    assert len(prompts) == 1 and pan.clause in prompts[0]
    assert shot["path"].endswith("/round_1/overhead_flatlay.png") and "/g" in shot["path"]
    assert not (tmp_path / "src" / "assets" / "images" / "test-recipe.png").exists()


def test_remake_registers_a_new_unapproved_set_keeps_the_rest_and_counts_one_call(tmp_path):
    store, wed = _store_with_set()
    before = store.get(EP_ID)["stages"]["wednesday"]
    old_paths = _control(store)["request"]["image_paths"]
    with _remake_env(store, tmp_path) as (client, shoot, saved, notify, dialogue):
        resp = _remake(client, wed, old_paths[1])

    assert resp.status_code == 200, resp.text
    assert resp.json()["calls_left"] == 8
    shoot.assert_called_once()
    assert shoot.call_args.args[2] == "overhead_flatlay"
    assert saved == [NEW_PATH]
    ctrl = _control(store)
    assert ctrl["state"] == "awaiting" and ctrl["request"]["image_paths"] == [old_paths[0], NEW_PATH, old_paths[2]]
    assert ctrl["reshoots"]["calls_used"] == 1 and "in_flight" not in ctrl["reshoots"]
    notify.assert_called_once()
    dialogue.assert_not_called()
    after = store.get(EP_ID)["stages"]["wednesday"]
    for kept in ("status", "dialogue", "judge_verdict", "completed_at"):
        assert after[kept] == before[kept]


def test_remake_of_a_path_outside_the_set_is_a_400_with_no_call_and_no_count(tmp_path):
    store, wed = _store_with_set()
    with _remake_env(store, tmp_path) as (client, shoot, saved, _notify, _dialogue):
        resp = _remake(client, wed, "src/assets/images/r1/other/round_1/macro_closeup.png")
    assert resp.status_code == 400
    shoot.assert_not_called()
    assert not saved and "calls_used" not in _control(store).get("reshoots", {})


def test_remake_cap_allows_the_ninth_call_and_refuses_the_tenth(tmp_path):
    store, wed = _store_with_set()
    for calls in (3, 3, 2):
        _finish(store, _start(store, wed, calls))
    with _remake_env(store, tmp_path) as (client, shoot, _saved, _notify, _dialogue):
        ninth = _remake(client, wed, _control(store)["request"]["image_paths"][1])
        assert ninth.status_code == 200 and ninth.json()["calls_left"] == 0
        current = store.get(EP_ID)["stages"]["wednesday"]
        tenth = _remake(client, current, _control(store)["request"]["image_paths"][1])
    assert tenth.status_code == 409 and "9 of 9" in tenth.json()["detail"]
    assert shoot.call_count == 1


def test_remake_route_runs_on_the_event_loop_not_a_thread():
    from fastapi import FastAPI

    from backend.admin.routes import create_routes

    app = FastAPI()
    create_routes(app)
    route = next(r for r in app.routes if getattr(r, "path", "").endswith("/photos/remake"))
    assert inspect.iscoroutinefunction(route.endpoint)
