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
