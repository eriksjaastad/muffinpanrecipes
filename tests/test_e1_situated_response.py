import hashlib
import json

import pytest

from scripts import e1_situated_response as e1


def _messages(*texts: str) -> list[dict[str, str]]:
    return [
        {"character": f"Speaker {index}", "message": text}
        for index, text in enumerate(texts)
    ]


def test_score_arm_reports_room_reset_and_two_kinds_of_adjacent_uptake():
    result = e1.score_arm(
        _messages(
            "Everyone here?",
            "Yes, the filling is ready.",
            "That filled shell holds together.",
        )
    )

    assert result["room_continuity_reset_count"] == 1
    assert result["adjacent_turn_uptake_rate"] == 1.0
    assert result["adjacent_turn_evidence"][0]["answer_cue"] == "yes"
    assert result["adjacent_turn_evidence"][1]["shared_terms"] == ["fill"]


def test_compare_arms_uses_reset_count_before_uptake_then_abstains_on_tie():
    reset = e1.score_arm(_messages("Everyone here?", "Yes."))
    connected = e1.score_arm(_messages("Which shot?", "Yes, the shot with steam."))
    disconnected = e1.score_arm(_messages("Which shot?", "The recipe is done."))

    assert e1.compare_arms(reset, disconnected) == "variant"
    assert e1.compare_arms(connected, disconnected) == "control"
    assert e1.compare_arms(disconnected, disconnected) == "tie"


def test_content_terms_freezes_stopwords_and_aliases():
    assert e1.content_terms("The photos are shooting filled cups") == {"shot", "fill", "cups"}


def test_score_evidence_verifies_sources_split_and_structured_flags(tmp_path, monkeypatch):
    evidence_path = tmp_path / "erik-review-completed.json"
    human_result_path = tmp_path / "human-result.md"
    pairs = []
    for pair in range(1, 10):
        pairs.append(
            {
                "run_index": pair,
                "scenario_id": f"scenario-{pair}",
                "control_messages": _messages("Which shot?", "Yes, this shot."),
                "variant_messages": _messages("Which shot?", "The recipe is done."),
            }
        )
    evidence_path.write_text(
        json.dumps(
            {
                "human_picks": {str(pair): "control" for pair in range(1, 10)},
                "pairs": pairs,
            }
        )
    )
    human_result_path.write_text("frozen labels\n")
    monkeypatch.setattr(
        e1, "EVIDENCE_SHA256", hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    )
    monkeypatch.setattr(
        e1,
        "HUMAN_RESULT_SHA256",
        hashlib.sha256(human_result_path.read_bytes()).hexdigest(),
    )

    result = e1.score_evidence(evidence_path, human_result_path, "holdout")

    assert [pair["pair"] for pair in result["pairs"]] == [3, 8, 9]
    assert result["pairs"][0]["evidence_flags"] == ["stock_language"]
    assert result["sources"] == {
        "evidence": {
            "name": "erik-review-completed.json",
            "sha256": e1.EVIDENCE_SHA256,
        },
        "human_result": {
            "name": "human-result.md",
            "sha256": e1.HUMAN_RESULT_SHA256,
        },
    }

    human_result_path.write_text("mutated labels\n")
    with pytest.raises(ValueError, match="human result hash mismatch"):
        e1.score_evidence(evidence_path, human_result_path, "holdout")
