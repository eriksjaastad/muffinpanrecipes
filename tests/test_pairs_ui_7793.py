"""Tests for card #7793: the local blind-pair-review web UI (`pairs-ui`),
replacing the terminal `pairs --show`/`pairs --pick` flow for Erik's V6 blind
reads.

Every HTTP test starts the real stdlib `http.server` on port 0 (OS-assigned)
in a background thread and talks to it over a real loopback socket - no
mocking of the HTTP layer itself, since that IS the thing being tested. No
paid API call is possible here: `pairs-ui` never imports or calls
model_router/generate_response/generate_judge_response.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

import scripts.conversation_lab as cl

ALL_DIMS = cl.ALL_JUDGE_DIMENSIONS


def _pair(run_index: int, *, overall: str, control_text: str, variant_text: str, scenario_id: str | None = None) -> dict:
    dims = {d: "tie" for d in ALL_DIMS}
    pair = {
        "run_index": run_index,
        "control_messages": [{"character": "Margaret Chen", "message": control_text}],
        "variant_messages": [{"character": "Stephanie 'Steph' Whitmore", "message": variant_text}],
        "control_summary": {}, "variant_summary": {},
        "judge": {"overall": overall, **dims},
        "judge_orientations": [],
        "dry_run": False,
    }
    if scenario_id is not None:
        pair["scenario_id"] = scenario_id
    return pair


def _write_result(tmp_path, pairs, *, mode="single", scenarios=None, filename="result.json") -> "cl.Path":
    report = {
        "command": "ab", "mode": mode, "concept": "Test Muffins", "stage": "monday",
        "aborted": False, "dry_run": False, "partial_pairs": [], "pairs": pairs,
    }
    if scenarios is not None:
        report["scenarios"] = scenarios
    path = tmp_path / filename
    path.write_text(json.dumps(report))
    return path


def _write_sweep_result(tmp_path, pairs, *, variant_name="only") -> "cl.Path":
    report = {
        "command": "ab", "mode": "sweep", "concept": None, "stage": "monday",
        "aborted": False, "dry_run": False,
        "variants": {variant_name: {"pairs": pairs}},
    }
    path = tmp_path / "sweep.json"
    path.write_text(json.dumps(report))
    return path


class _RunningServer:
    def __init__(self, path, variant_name=None):
        self.state = cl.PairsReviewState(path, variant_name)
        self.server = cl._PairsUIServer(0, self.state)
        self.host, self.port = self.server.server_address[:2]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def get(self, path):
        with urllib.request.urlopen(f"{self.base}{path}", timeout=5) as resp:
            return resp.status, json.loads(resp.read())

    def post(self, path, payload):
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())

    def get_status(self, path):
        try:
            status, _ = self.get(path)
            return status
        except urllib.error.HTTPError as exc:
            return exc.code

    def post_status(self, path, payload):
        try:
            status, _ = self.post(path, payload)
            return status
        except urllib.error.HTTPError as exc:
            return exc.code

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


@pytest.fixture
def running_server(tmp_path):
    servers: list[_RunningServer] = []

    def _start(pairs, **kwargs):
        variant_name = kwargs.pop("variant_name", None)
        sweep = kwargs.pop("sweep", False)
        if sweep:
            path = _write_sweep_result(tmp_path, pairs, variant_name=variant_name or "only")
        else:
            path = _write_result(tmp_path, pairs, **kwargs)
        rs = _RunningServer(path, variant_name)
        rs.path = path
        servers.append(rs)
        return rs

    yield _start
    for rs in servers:
        rs.close()


# ---------------------------------------------------------------------------
# Blindness: no arm names, no A/B mapping, no judge fields in the wire format
# ---------------------------------------------------------------------------

def test_pair_payload_has_no_arm_names_or_judge_fields(running_server):
    pairs = [_pair(1, overall="control", control_text="Control says X.", variant_text="Variant says Y.")]
    rs = running_server(pairs)
    status, payload = rs.get("/api/pair/1")
    assert status == 200
    assert set(payload.keys()) == {"position", "total", "scenario", "concept", "picked", "transcript_a", "transcript_b"}
    serialized = json.dumps(payload)
    assert "judge" not in serialized
    assert '"control"' not in serialized
    assert '"variant"' not in serialized
    # the A/B order matches the seeded _blind_order for position 1
    first_arm, second_arm = cl._blind_order(1)
    expected_a_text = "Control says X." if first_arm == "control" else "Variant says Y."
    assert payload["transcript_a"][0]["message"] == expected_a_text


def test_state_does_not_reveal_stats_before_finished(running_server):
    pairs = [
        _pair(1, overall="control", control_text="A1", variant_text="B1"),
        _pair(2, overall="variant", control_text="A2", variant_text="B2"),
    ]
    rs = running_server(pairs)
    status, state = rs.get("/api/state")
    assert status == 200
    assert state == {"total": 2, "picked_count": 0, "finished": False, "first_unpicked": 1}
    assert "stats" not in state


def test_finish_screen_state_shows_stats_once_all_picked(running_server):
    pairs = [
        _pair(1, overall="control", control_text="A1", variant_text="B1"),
        _pair(2, overall="tie", control_text="A2", variant_text="B2"),
    ]
    rs = running_server(pairs)
    first_arm1, second_arm1 = cl._blind_order(1)
    label1 = "A" if first_arm1 == "control" else "B"  # pick the arm the judge called "control"
    rs.post("/api/pick", {"position": 1, "label": label1})
    rs.post("/api/pick", {"position": 2, "label": "tie"})
    status, state = rs.get("/api/state")
    assert state["finished"] is True
    assert state["first_unpicked"] is None
    assert state["stats"]["judge_tie"] == 1
    assert state["stats"]["agreed"] + state["stats"]["disagreed"] == 1


# ---------------------------------------------------------------------------
# Persistence + resume
# ---------------------------------------------------------------------------

def test_pick_persists_to_disk_and_reload_resumes_at_first_unpicked(running_server):
    pairs = [
        _pair(1, overall="tie", control_text="A1", variant_text="B1"),
        _pair(2, overall="tie", control_text="A2", variant_text="B2"),
        _pair(3, overall="tie", control_text="A3", variant_text="B3"),
    ]
    rs = running_server(pairs)
    rs.post("/api/pick", {"position": 1, "label": "A"})

    on_disk = json.loads(rs.path.read_text())
    assert on_disk["human_picks"]["1"] in ("control", "variant")
    assert "2" not in on_disk["human_picks"]

    # A brand new PairsReviewState (simulating a page reload / process restart)
    # must resume at the first unpicked position.
    reloaded = cl.PairsReviewState(rs.path, None)
    assert reloaded.first_unpicked() == 2
    assert reloaded.state()["picked_count"] == 1


def test_repicking_an_already_picked_pair_overwrites_it(running_server):
    pairs = [_pair(1, overall="control", control_text="A1", variant_text="B1")]
    rs = running_server(pairs)
    rs.post("/api/pick", {"position": 1, "label": "A"})
    _, first = rs.get("/api/pair/1")
    assert first["picked"] == "A"
    rs.post("/api/pick", {"position": 1, "label": "B"})
    _, second = rs.get("/api/pair/1")
    assert second["picked"] == "B"
    on_disk = json.loads(rs.path.read_text())
    assert len(on_disk["human_picks"]) == 1  # overwritten, not appended


# ---------------------------------------------------------------------------
# Validation: invalid position/label -> 400
# ---------------------------------------------------------------------------

def test_get_pair_out_of_range_is_400(running_server):
    rs = running_server([_pair(1, overall="tie", control_text="A1", variant_text="B1")])
    assert rs.get_status("/api/pair/0") == 400
    assert rs.get_status("/api/pair/99") == 400
    assert rs.get_status("/api/pair/not-a-number") == 400


def test_pick_out_of_range_position_is_400(running_server):
    rs = running_server([_pair(1, overall="tie", control_text="A1", variant_text="B1")])
    assert rs.post_status("/api/pick", {"position": 99, "label": "A"}) == 400
    assert rs.post_status("/api/pick", {"position": 0, "label": "A"}) == 400


def test_pick_invalid_label_is_400(running_server):
    rs = running_server([_pair(1, overall="tie", control_text="A1", variant_text="B1")])
    assert rs.post_status("/api/pick", {"position": 1, "label": "nope"}) == 400


def test_pick_malformed_body_is_400(running_server):
    rs = running_server([_pair(1, overall="tie", control_text="A1", variant_text="B1")])
    assert rs.post_status("/api/pick", {"position": "one", "label": "A"}) == 400
    assert rs.post_status("/api/pick", {"label": "A"}) == 400  # missing position


def test_unknown_route_is_404(running_server):
    rs = running_server([_pair(1, overall="tie", control_text="A1", variant_text="B1")])
    assert rs.get_status("/api/nope") == 404


# ---------------------------------------------------------------------------
# --sweep container support
# ---------------------------------------------------------------------------

def test_sweep_result_is_supported_via_variant_name(running_server):
    pairs = [_pair(1, overall="control", control_text="A1", variant_text="B1")]
    rs = running_server(pairs, sweep=True, variant_name="only")
    status, payload = rs.get("/api/pair/1")
    assert status == 200
    rs.post("/api/pick", {"position": 1, "label": "A"})
    on_disk = json.loads(rs.path.read_text())
    assert on_disk["variants"]["only"]["human_picks"]["1"] in ("control", "variant")
    # the sweep report's own top level must NOT gain human_picks - it belongs
    # to the variant's own sub-dict.
    assert "human_picks" not in on_disk


def test_sweep_without_variant_name_refuses_to_start():
    import tempfile
    tmp_dir = tempfile.mkdtemp()
    from pathlib import Path
    path = Path(tmp_dir) / "sweep.json"
    report = {
        "command": "ab", "mode": "sweep",
        "variants": {"only": {"pairs": [_pair(1, overall="tie", control_text="A", variant_text="B")]}},
    }
    path.write_text(json.dumps(report))
    with pytest.raises(SystemExit, match="sweep"):
        cl.PairsReviewState(path, None)


# ---------------------------------------------------------------------------
# Shared recording function: --pick and the UI must produce identical state
# ---------------------------------------------------------------------------

def test_pick_cli_and_ui_produce_identical_container_state(tmp_path):
    pairs_a = [
        _pair(1, overall="control", control_text="A1", variant_text="B1"),
        _pair(2, overall="variant", control_text="A2", variant_text="B2"),
    ]
    pairs_b = [
        _pair(1, overall="control", control_text="A1", variant_text="B1"),
        _pair(2, overall="variant", control_text="A2", variant_text="B2"),
    ]
    path_cli = _write_result(tmp_path, pairs_a, filename="cli.json")
    path_ui = _write_result(tmp_path, pairs_b, filename="ui.json")

    # CLI path: the actual argparse-driven command.
    cl.main(["pairs", "--from", str(path_cli), "--pick", "1:A,2:B"])

    # UI path: same two picks, one HTTP POST each.
    state = cl.PairsReviewState(path_ui, None)
    order1 = cl._blind_order(1)
    order2 = cl._blind_order(2)
    state.apply_pick(1, "A")
    state.apply_pick(2, "B")

    report_cli = json.loads(path_cli.read_text())
    report_ui = json.loads(path_ui.read_text())

    for key in (
        "human_picks", "human_review_order", "human_pick_meta",
        "human_judge_agreement_rate", "human_judge_agreed_count",
        "human_judge_disagreed_count", "human_judge_tie_count",
    ):
        assert report_cli[key] == report_ui[key], key


def test_apply_human_picks_records_longer_arm_metadata():
    pairs = [{
        "control_messages": [{"character": "Margaret Chen", "message": "Short."}],
        "variant_messages": [{"character": "Steph", "message": "A much longer line with many more words in it."}],
        "judge": {"overall": "tie", **{d: "tie" for d in ALL_DIMS}},
    }]
    container: dict = {}
    order_by_position = {1: cl._blind_order(1)}
    first_arm, _second_arm = order_by_position[1]
    # Pick whichever label corresponds to "variant" (the longer arm).
    label = "A" if first_arm == "variant" else "B"
    cl._apply_human_picks(container, pairs, order_by_position, {1: label})
    meta = container["human_pick_meta"]["1"]
    assert meta["longer_arm"] == "variant"
    assert meta["picked_longer"] is True


def test_longer_arm_hand_checked():
    pair_control_longer = {
        "control_messages": [{"character": "X", "message": "one two three four five"}],
        "variant_messages": [{"character": "X", "message": "one two"}],
    }
    pair_tie = {
        "control_messages": [{"character": "X", "message": "one two three"}],
        "variant_messages": [{"character": "X", "message": "a b c"}],
    }
    assert cl._longer_arm(pair_control_longer) == "control"
    assert cl._longer_arm(pair_tie) == "tie"


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def test_write_pairs_report_atomic_leaves_no_temp_file(tmp_path):
    path = tmp_path / "atomic.json"
    cl._write_pairs_report_atomic(path, {"a": 1})
    assert json.loads(path.read_text()) == {"a": 1}
    leftovers = [p for p in tmp_path.iterdir() if p.name != "atomic.json"]
    assert leftovers == []


def test_write_pairs_report_atomic_overwrites_existing_file(tmp_path):
    path = tmp_path / "atomic.json"
    path.write_text(json.dumps({"a": 1}))
    cl._write_pairs_report_atomic(path, {"a": 2})
    assert json.loads(path.read_text()) == {"a": 2}
