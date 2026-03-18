from scripts.simulate_dialogue_week import Message, load_personas, score_quality


def test_score_quality_day_filter_matches_subset():
    personas = load_personas()
    messages = [
        Message(day="monday", stage="brainstorm", character="Margaret Chen", message="No.", timestamp="t1", model="openai/gpt-5-mini"),
        Message(day="monday", stage="brainstorm", character="Marcus Reid", message="Maybe.", timestamp="t2", model="openai/gpt-5-mini"),
        Message(day="tuesday", stage="recipe", character="Margaret Chen", message="Fine.", timestamp="t3", model="openai/gpt-5-mini"),
    ]

    subset = [m for m in messages if m.day == "monday"]
    full_day = score_quality(messages, personas, day="monday")
    subset_score = score_quality(subset, personas)
    assert full_day["score"] == subset_score["score"]
