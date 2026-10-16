"""Browser test for the pairs-ui why box (#8342): the page's own JavaScript,
which the HTTP tests in tests/test_pairs_ui_7793.py cannot reach.

How to run (tests/e2e is skipped by the default pytest run):
    uv run --extra dev pytest tests/e2e/test_pairs_ui.py
"""

import json
import threading

from playwright.sync_api import Page, expect

import scripts.conversation_lab as cl


def _pair(n: int) -> dict:
    return {
        "run_index": n,
        "control_messages": [{"character": "Margaret Chen", "message": f"Control line {n}."}],
        "variant_messages": [{"character": "Julian Torres", "message": f"Variant line {n}."}],
        "judge": {"overall": "tie"},
    }


def test_why_box_never_fires_hotkeys_and_keeps_a_draft_across_pairs(page: Page, tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"command": "ab", "mode": "single", "pairs": [_pair(1), _pair(2)]}))
    server = cl._PairsUIServer(0, cl.PairsReviewState(path, None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
        title, box = page.locator("#pair-title"), page.locator("#reason")
        expect(title).to_have_text("Pair 1 of 2")

        box.press_sequentially("a b t k")  # every one of these is a hotkey outside the box
        page.get_by_role("button", name="next (K)").click()
        expect(title).to_have_text("Pair 2 of 2")
        expect(box).to_have_value("")
        page.get_by_role("button", name="prev (J)").click()
        expect(title).to_have_text("Pair 1 of 2")
        expect(box).to_have_value("a b t k")

        page.get_by_role("button", name="A - pick left (A)").click()
        expect(title).to_have_text("Pair 2 of 2")
        expect(box).to_have_value("")
        result = json.loads(path.read_text())
        assert list(result["human_picks"]) == ["1"]
        assert result["human_pick_reasons"] == {"1": "a b t k"}
    finally:
        server.shutdown()
        server.server_close()
