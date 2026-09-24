from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest
from anthropic.resources.messages import Messages

from scripts import memory_paid_experiment as paid
from backend.utils import model_router


def _artifact() -> dict:
    records = []
    for name in ("Devon", "Julian", "Margaret", "Marcus", "Ria", "Steph"):
        user = f"Character: {name}\nEpisode: 2026-W35\nprofile and evidence\n[msg_0123456789abcdefabcd] Marcus: fixture event"
        records.append({
            "character": name,
            "episode_id": "2026-W35",
            "source_ids": ["msg_0123456789abcdefabcd"],
            "planned_model": paid.MODEL,
            "hard_max_response_tokens": paid.HARD_MAX_TOKENS,
            "arms": {
                "A": {"system": "Policy A — recap bundle; 80–120 provider tokens", "user": user},
                "B": {"system": "Policy B — source-linked perspective-card bundle; 80–120 provider tokens", "user": user},
            },
        })
    return {
        "mode": "offline_prompt_render_only",
        "generation_performed": False,
        "paid_mode_available": False,
        "experiment": paid.EXPERIMENT,
        "episode_id": "2026-W35",
        "planned_model": paid.MODEL,
        "hard_max_response_tokens_per_prompt": paid.HARD_MAX_TOKENS,
        "character_count": 6,
        "prompts": records,
    }


def _write_artifact(path: Path, artifact: dict | None = None) -> str:
    path.write_text(json.dumps(artifact or _artifact()), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fake_sdk(
    monkeypatch,
    *,
    fail_on: int | None = None,
    token_count: int | None = None,
    fail_count_on: int | None = None,
):
    calls = {"create": [], "count": []}
    def count_tokens(self, **kwargs):
        calls["count"].append(kwargs)
        if fail_count_on == len(calls["count"]):
            raise RuntimeError("synthetic descriptive count_tokens failure")
        return SimpleNamespace(input_tokens=token_count or max(0, len(kwargs["messages"][0]["content"].split()) + 10))
    def create(self, **kwargs):
        calls["create"].append(kwargs)
        if fail_on == len(calls["create"]):
            raise RuntimeError("synthetic transport error")
        body = (
            "The room agreed to pause the launch. Marcus noticed the concern remained unresolved.\n"
            "Evidence map:\nSentence 1: msg_0123456789abcdefabcd\n"
            "Sentence 2: msg_0123456789abcdefabcd"
            if kwargs["system"].find("recap bundle") >= 0 else
            "Observed: Launch was paused [msg_0123456789abcdefabcd]\n"
            "Inference: The delay may protect the team [msg_0123456789abcdefabcd]\n"
            "Stance: no change evidenced\nOpen thread: Whether to relaunch remains unresolved [msg_0123456789abcdefabcd]"
        )
        usage = {"input_tokens": 100, "output_tokens": 48, "service_tier": "standard", "inference_geo": None}
        return SimpleNamespace(
            usage=SimpleNamespace(model_dump=lambda exclude_none=True: usage),
            content=[SimpleNamespace(type="text", text=body)],
            stop_reason="end_turn",
            _request_id="req_synthetic",
        )
    def make_client(*, api_key=None, max_retries=2, **kwargs):
        calls["max_retries"] = max_retries
        return SimpleNamespace(messages=Messages.__new__(Messages))
    monkeypatch.setattr(Messages, "count_tokens", count_tokens)
    monkeypatch.setattr(Messages, "create", create)
    monkeypatch.setattr(anthropic, "Anthropic", make_client)
    monkeypatch.setattr(model_router, "_central_track", lambda *args, **kwargs: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-test-key")
    return calls


def test_artifact_checks_hash_mode_model_and_shared_inputs(tmp_path):
    path = tmp_path / "prompts.json"
    digest = _write_artifact(path)
    artifact, loaded_digest = paid._read_artifact(path, digest)
    assert loaded_digest == digest
    assert len(artifact["prompts"]) == 6

    with pytest.raises(paid.ExperimentError, match="SHA-256"):
        paid._read_artifact(path, "0" * 64)
    wrong = _artifact()
    wrong["prompts"][0]["planned_model"] = "other-model"
    with pytest.raises(paid.ExperimentError, match="model or response cap"):
        paid._validate_artifact(wrong)
    different_source = _artifact()
    different_source["prompts"][0]["arms"]["B"]["user"] += " future week"
    with pytest.raises(paid.ExperimentError, match="source and persona"):
        paid._validate_artifact(different_source)
    incomplete = _artifact()
    incomplete["generation_performed"] = True
    with pytest.raises(paid.ExperimentError, match="generation_performed"):
        paid._validate_artifact(incomplete)


def test_offline_cli_validation_never_constructs_sdk(tmp_path, monkeypatch, capsys):
    path = tmp_path / "prompts.json"
    digest = _write_artifact(path)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: pytest.fail("SDK must not be constructed"))
    assert paid.main([str(path), "--artifact-sha256", digest]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["paid_call_count"] == 0
    with pytest.raises(SystemExit):
        paid.main([str(path), "--artifact-sha256", digest, "--ledger", str(tmp_path / "x")])


def test_execute_stores_12_raw_responses_usage_prose_counts_and_ledger(tmp_path, monkeypatch):
    calls = _fake_sdk(monkeypatch)
    artifact = _artifact()
    digest = "a" * 64
    ledger, checkpoint = tmp_path / "unique-ledger.json", tmp_path / "result.json"
    state = paid.execute(artifact, digest, ledger, checkpoint)
    assert state["status"] == "complete"
    assert len(state["responses"]) == 12
    assert len(calls["create"]) == 12
    assert calls["max_retries"] == 0
    assert all(record["raw_response_text"] and record["usage"]["output_tokens"] == 48 for record in state["responses"])
    assert all(record["stop_reason"] == "end_turn" for record in state["responses"])
    assert all(record["prose_parse_status"] == "parsed" for record in state["responses"])
    assert all("memory_prose_tokens" in record["normalized_text_lengths"] for record in state["responses"])
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["prompt_artifact_sha256"] == digest
    guard = json.loads(ledger.read_text(encoding="utf-8"))
    assert guard["budget_microusd"] == 5_000_000
    assert guard["totals"]["generation_attempts"] == 12


def test_transport_failure_preserves_partial_and_refuses_ambiguous_resume(tmp_path, monkeypatch):
    _fake_sdk(monkeypatch, fail_on=3)
    ledger, checkpoint = tmp_path / "ledger.json", tmp_path / "result.json"
    with pytest.raises(RuntimeError):
        paid.execute(_artifact(), "b" * 64, ledger, checkpoint)
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert state["status"] == "stopped"
    assert len(state["responses"]) == 2
    assert state["in_flight_call_id"] == "Julian:A"
    with pytest.raises(paid.ExperimentError, match="ambiguous in-flight"):
        paid.execute(_artifact(), "b" * 64, ledger, checkpoint, resume=True)


def test_resume_rejects_artifact_or_ledger_mismatch(tmp_path):
    artifact = _artifact()
    state = paid._base_state(artifact, "c" * 64, tmp_path / "ledger.json")
    state["in_flight_call_id"] = None
    checkpoint = tmp_path / "result.json"
    ledger = tmp_path / "ledger.json"
    checkpoint.write_text(json.dumps(state), encoding="utf-8")
    ledger.write_text(json.dumps({"status": "active", "budget_microusd": 5_000_000, "calls": []}), encoding="utf-8")
    with pytest.raises(paid.ExperimentError, match="different prompt artifact"):
        paid.execute(artifact, "d" * 64, ledger, checkpoint, resume=True)


def test_unparseable_response_remains_preserved_and_unscored(tmp_path, monkeypatch):
    calls = _fake_sdk(monkeypatch)
    original = Messages.create
    def malformed(self, **kwargs):
        response = original(self, **kwargs)
        response.content = [SimpleNamespace(type="text", text="unsupported prose without citations")]
        return response
    monkeypatch.setattr(Messages, "create", malformed)
    state = paid.execute(_artifact(), "e" * 64, tmp_path / "ledger.json", tmp_path / "result.json")
    assert len(calls["create"]) == 12
    assert all(item["prose_parse_status"] == "unscored" for item in state["responses"])
    assert all("normalized_text_lengths" not in item for item in state["responses"])


def test_budget_guard_denial_is_checkpointed_without_generation(tmp_path, monkeypatch):
    calls = _fake_sdk(monkeypatch, token_count=10_000_000)
    ledger, checkpoint = tmp_path / "ledger.json", tmp_path / "result.json"
    with pytest.raises(RuntimeError):
        paid.execute(_artifact(), "f" * 64, ledger, checkpoint)
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    budget = json.loads(ledger.read_text(encoding="utf-8"))
    assert state["status"] == "stopped"
    assert state["responses"] == []
    assert state["in_flight_call_id"] == "Devon:A"
    assert budget["status"] == "stopped"
    assert budget["stop_reason"] == "budget_exhausted"
    assert calls["create"] == []


def test_b_prose_length_excludes_field_labels_and_citation_wrappers():
    source_id = "msg_0123456789abcdefabcd"
    first = (
        f"Observed: Launch paused [{source_id}]\nInference: Team needs time [{source_id}]\n"
        "Stance: no change evidenced\nOpen thread: Relaunch date unknown ["
        f"{source_id}]"
    )
    second_id = "msg_abcdef0123456789abcd"
    second = (
        f"Observed: Launch paused [{second_id}]\nInference: Team needs time [{second_id}]\n"
        "Stance: no change evidenced\nOpen thread: Relaunch date unknown ["
        f"{second_id}]"
    )
    prose_a, error_a, structure_a = paid._extract_prose("B", first, {source_id})
    prose_b, error_b, structure_b = paid._extract_prose("B", second, {second_id})
    assert error_a is None and error_b is None
    assert prose_a == prose_b == "Launch paused\nTeam needs time\nno change evidenced\nRelaunch date unknown"
    assert "Observed:" not in prose_a and source_id not in prose_a
    assert structure_a["format"] == structure_b["format"] == "perspective_card"
    assert structure_a["fields"] == structure_b["fields"]


def test_b_requires_declared_order_and_no_prefix_but_accepts_whitespace():
    source_id = "msg_0123456789abcdefabcd"
    allowed = {source_id}
    valid = (
        " \n  Observed : Launch paused [" + source_id + "]  \n"
        "  Inference: The delay may help [" + source_id + "]\n"
        "Stance: no change evidenced. \n  Open thread: none  \n"
    )
    prose, error, structure = paid._extract_prose("B", valid, allowed)
    assert error is None
    assert structure["fields"]["Observed"] == "Launch paused"
    assert structure["fields"]["Inference"] == "The delay may help"

    out_of_order = (
        "Observed: Launch paused [" + source_id + "]\n"
        "Stance: no change evidenced\n"
        "Inference: The delay may help [" + source_id + "]\nOpen thread: none"
    )
    parsed, error, structure = paid._extract_prose("B", out_of_order, allowed)
    assert parsed is None and structure is None
    assert error == "perspective card fields are out of declared order"

    prefixed = (
        "Unlabeled introduction [" + source_id + "]\n"
        "Observed: Launch paused [" + source_id + "]\n"
        "Inference: none supported\nStance: no change evidenced\nOpen thread: none"
    )
    parsed, error, structure = paid._extract_prose("B", prefixed, allowed)
    assert parsed is None and structure is None
    assert error == "perspective card has content before Observed"


def test_b_uncited_no_change_fallback_is_exact_and_citation_groups_strip_cleanly():
    source_id = "msg_0123456789abcdefabcd"
    source_id_2 = "msg_abcdef0123456789abcd"
    allowed = {source_id, source_id_2}

    def response(stance: str, observed: str) -> str:
        return (
            f"Observed: {observed}\nInference: none supported\nStance: {stance}\n"
            "Open thread: none"
        )

    for fallback in ("no change evidenced", "No change evidenced.", "no change evidenced!!!"):
        parsed, error, structure = paid._extract_prose(
            "B", response(fallback, f"Launch paused [{source_id}, {source_id_2}]"), allowed
        )
        assert error is None
        assert structure["fields"]["Observed"] == "Launch paused"
        assert ", ]" not in parsed and "[" not in parsed

    malformed, error, _ = paid._extract_prose(
        "B",
        response("no change evidenced because the team was exhausted", f"Launch paused [{source_id}]"),
        allowed,
    )
    assert malformed is None
    assert error == "changed Stance field lacks a source ID"

    cited, error, _ = paid._extract_prose(
        "B",
        response(
            f"no change evidenced because the team was exhausted [{source_id}]",
            f"Launch paused [{source_id}]",
        ),
        allowed,
    )
    assert error is None
    assert "because the team was exhausted" in cited


def test_pairwise_length_matching_has_fixed_band_and_tolerance():
    assert paid.matched_prose_lengths(80, 95)
    assert paid.matched_prose_lengths(105, 120)
    assert not paid.matched_prose_lengths(79, 90)
    assert not paid.matched_prose_lengths(80, 96)
    assert not paid.matched_prose_lengths(121, 120)
    assert not paid.matched_prose_lengths(None, 100)
    assert not paid.matched_prose_lengths(True, 100)


def test_a_evidence_map_requires_one_ordered_allowed_entry_per_sentence():
    first_id = "msg_0123456789abcdefabcd"
    second_id = "msg_abcdef0123456789abcd"
    allowed = {first_id, second_id}
    valid = (
        "Devon shared the schedule. Marcus raised a concern.\n"
        "Evidence map:\nSentence 1: [" + first_id + "]\nSentence 2: [" + second_id + "]"
    )
    prose, error, structure = paid._extract_prose("A", valid, allowed)
    assert error is None
    assert prose == "Devon shared the schedule. Marcus raised a concern."
    assert structure["evidence_map"] == {
        "sentence_1": [first_id], "sentence_2": [second_id],
    }

    for label_1, label_2 in (("1", "2"), ("S1", "S2"), ("Sentence 1", "Sentence 2")):
        variant = (
            "Devon shared the schedule. Marcus raised a concern.\n"
            f"Evidence map:\n{label_1}: [{first_id}]\n{label_2}: [{second_id}]"
        )
        variant_prose, variant_error, variant_structure = paid._extract_prose("A", variant, allowed)
        assert variant_error is None
        assert variant_prose == prose
        assert variant_structure["evidence_map"] == structure["evidence_map"]

    malformed_maps = [
        # The old parser accepted both IDs despite lacking sentence 2 mapping.
        f"Evidence map:\nSentence 1: [{first_id}] [{second_id}]",
        f"Evidence map:\nSentence 1: [{first_id}]\nSentence 1: [{second_id}]",
        f"Evidence map:\nSentence 2: [{first_id}]\nSentence 1: [{second_id}]",
        f"Evidence map:\nSentence 1: [{first_id}]\nSentence 3: [{second_id}]",
        f"Evidence map:\nSentence 1: [{first_id}]\nSentence 2: [{second_id}]\nExtra: [{first_id}]",
        "Evidence map:\nSentence 1: [msg_aaaaaaaaaaaaaaaaaaaa]\nSentence 2: [" + second_id + "]",
        f"Evidence map:\nSentence 1: [{first_id}] unsupported prose\nSentence 2: [{second_id}]",
    ]
    for evidence in malformed_maps:
        raw = "Devon shared the schedule. Marcus raised a concern.\n" + evidence
        prose, error, structure = paid._extract_prose("A", raw, allowed)
        assert prose is None
        assert error
        assert structure is None


def test_malformed_a_map_keeps_raw_response_and_marks_unscored(tmp_path, monkeypatch):
    _fake_sdk(monkeypatch)
    original = Messages.create
    malformed = (
        "The room agreed to pause the launch. Marcus noticed the concern remained unresolved.\n"
        "Evidence map:\nSentence 1: msg_0123456789abcdefabcd msg_0123456789abcdefabcd"
    )
    def malformed_a_only(self, **kwargs):
        response = original(self, **kwargs)
        if "Policy A — recap bundle" in kwargs["system"]:
            response.content = [SimpleNamespace(type="text", text=malformed)]
        return response
    monkeypatch.setattr(Messages, "create", malformed_a_only)
    state = paid.execute(_artifact(), "8" * 64, tmp_path / "ledger.json", tmp_path / "result.json")
    a_records = [item for item in state["responses"] if item["arm"] == "A"]
    assert all(item["raw_response_text"] == malformed for item in a_records)
    assert all(item["prose_parse_status"] == "unscored" for item in a_records)
    assert all("two sentence entries" in item["prose_parse_error"] for item in a_records)


def test_fabricated_citation_is_preserved_but_unscored(tmp_path, monkeypatch):
    _fake_sdk(monkeypatch)
    # The fake response uses a syntactically valid ID absent from this record.
    artifact = _artifact()
    artifact["prompts"][0]["source_ids"] = ["msg_aaaaaaaaaaaaaaaaaaaa"]
    state = paid.execute(artifact, "9" * 64, tmp_path / "ledger.json", tmp_path / "result.json")
    item = state["responses"][0]
    assert item["raw_response_text"]
    assert item["prose_parse_status"] == "unscored"
    assert "outside this character's evidence" in item["prose_parse_error"]
    assert "normalized_text_lengths" not in item


def test_resume_skips_only_valid_saved_response_and_continues(tmp_path, monkeypatch):
    calls = _fake_sdk(monkeypatch)
    artifact, digest = _artifact(), "7" * 64
    ledger, checkpoint = tmp_path / "ledger.json", tmp_path / "result.json"
    first_prompt = artifact["prompts"][0]["arms"]["A"]
    with paid.AnthropicBudgetGuard(ledger, create=True) as guard:
        client = anthropic.Anthropic()
        with guard.phase("ab"):
            first = client.messages.create(
                model=paid.MODEL,
                max_tokens=paid.HARD_MAX_TOKENS,
                temperature=0.4,
                system=first_prompt["system"],
                messages=[{"role": "user", "content": first_prompt["user"]}],
            )
    raw = first.content[0].text
    usage = {"input_tokens": 100, "output_tokens": 48, "service_tier": "standard", "inference_geo": None}
    state = paid._base_state(artifact, digest, ledger)
    state["ledger_created_at"] = json.loads(ledger.read_text(encoding="utf-8"))["created_at"]
    state["responses"] = [{
        "call_id": "Devon:A", "character": "Devon", "arm": "A",
        "raw_response_text": raw, "usage": usage, "stop_reason": "end_turn",
        "actual_cost_microusd": 340, "measurement_status": "complete",
        "prose_parse_status": "parsed", "prose_parse_error": None,
        "memory_prose": "The room agreed to pause the launch. Marcus noticed the concern remained unresolved.",
        "memory_structure": {"format": "two_sentence_recap"},
        "normalized_text_lengths": {"memory_prose_tokens": 18},
    }]
    state["next_call_index"] = 1
    state["status"] = "partial"
    paid._atomic_json(checkpoint, state, create=True)

    resumed = paid.execute(artifact, digest, ledger, checkpoint, resume=True)
    assert len(resumed["responses"]) == 12
    assert resumed["responses"][0]["raw_response_text"] == raw
    assert len(calls["create"]) == 12  # one preexisting plus eleven new calls


def test_resume_finishes_durable_raw_response_measurement_before_next_generation(tmp_path, monkeypatch):
    calls = _fake_sdk(monkeypatch)
    artifact, digest = _artifact(), "6" * 64
    ledger, checkpoint = tmp_path / "ledger.json", tmp_path / "result.json"
    measure = paid._finish_response_measurement

    def crash_before_measurement(*args, **kwargs):
        raise SystemExit("simulated process interruption after raw checkpoint")

    monkeypatch.setattr(paid, "_finish_response_measurement", crash_before_measurement)
    with pytest.raises(SystemExit, match="simulated process interruption"):
        paid.execute(artifact, digest, ledger, checkpoint)

    raw_checkpoint = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert raw_checkpoint["next_call_index"] == 1
    assert raw_checkpoint["pending_measurement_call_id"] == "Devon:A"
    first = raw_checkpoint["responses"][0]
    assert first["measurement_status"] == "pending"
    assert first["raw_response_text"]
    assert "memory_prose" not in first
    assert len(calls["create"]) == 1
    assert paid._validate_state(raw_checkpoint, digest, ledger)["responses"][0]["measurement_status"] == "pending"
    inconsistent = json.loads(json.dumps(raw_checkpoint))
    inconsistent["pending_measurement_call_id"] = None
    with pytest.raises(paid.ExperimentError, match="pending measurement checkpoint identity"):
        paid._validate_state(inconsistent, digest, ledger)

    events = []

    def record_measurement(*args, **kwargs):
        events.append("measure")
        return measure(*args, **kwargs)

    original_create = Messages.create

    def record_generation(self, **kwargs):
        events.append("generate")
        return original_create(self, **kwargs)

    monkeypatch.setattr(paid, "_finish_response_measurement", record_measurement)
    monkeypatch.setattr(Messages, "create", record_generation)
    resumed = paid.execute(artifact, digest, ledger, checkpoint, resume=True)
    assert len(calls["create"]) == 12  # pending response resumed; only eleven new generations
    assert events[:2] == ["measure", "generate"]
    completed_first = resumed["responses"][0]
    assert completed_first["measurement_status"] == "complete"
    assert completed_first["prose_parse_status"] == "parsed"
    assert completed_first["memory_prose"]
    assert completed_first["memory_structure"]
    assert completed_first["normalized_text_lengths"]["memory_prose_tokens"] > 0
    assert resumed["pending_measurement_call_id"] is None


def test_descriptive_count_failure_keeps_ledger_active_and_resumes_without_regeneration(tmp_path, monkeypatch):
    calls = _fake_sdk(monkeypatch, fail_count_on=2)
    artifact, digest = _artifact(), "5" * 64
    ledger, checkpoint = tmp_path / "ledger.json", tmp_path / "result.json"
    with pytest.raises(RuntimeError, match="descriptive count_tokens failure"):
        paid.execute(artifact, digest, ledger, checkpoint)

    partial = json.loads(checkpoint.read_text(encoding="utf-8"))
    ledger_after_failure = json.loads(ledger.read_text(encoding="utf-8"))
    assert partial["pending_measurement_call_id"] == "Devon:A"
    assert partial["responses"][0]["raw_response_text"]
    assert partial["responses"][0]["measurement_status"] == "pending"
    assert ledger_after_failure["status"] == "active"
    assert ledger_after_failure["totals"]["generation_attempts"] == 1
    assert ledger_after_failure["totals"]["actual_microusd"] == 340
    assert ledger_after_failure["totals"]["reserved_microusd"] == 0
    assert ledger_after_failure["totals"]["uncertain_microusd"] == 0
    assert len(calls["create"]) == 1

    resumed = paid.execute(artifact, digest, ledger, checkpoint, resume=True)
    final_ledger = json.loads(ledger.read_text(encoding="utf-8"))
    assert len(calls["create"]) == 12  # original response plus eleven new generations
    assert len(resumed["responses"]) == 12
    assert resumed["responses"][0]["measurement_status"] == "complete"
    assert resumed["responses"][0]["normalized_text_lengths"]["memory_prose_tokens"] > 0
    assert final_ledger["status"] == "active"
    assert final_ledger["totals"]["generation_attempts"] == 12
    assert final_ledger["totals"]["actual_microusd"] == 12 * 340
    assert final_ledger["totals"]["reserved_microusd"] == 0
    assert final_ledger["totals"]["uncertain_microusd"] == 0
