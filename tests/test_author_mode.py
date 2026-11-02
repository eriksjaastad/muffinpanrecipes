"""Hermetic tests for the authored dialogue path."""

import pytest

from backend.admin import cron_routes
from scripts import simulate_dialogue_week as sim


def run(**kwargs):
    return sim.run_simulation(
        concept="Custard Cups",
        default_model="stub",
        run_index=1,
        stage_only=kwargs.pop("stage_only", "monday"),
        injected_event="late delivery",
        mode=kwargs.pop("mode", "openai"),
        recipe_context="Lemon custard in pastry",
        recipe_facts="Method: Bake at 500 F.",
        **kwargs,
    )


def test_run_simulation_uses_authored_scene_and_preserves_prompt_context(monkeypatch):
    prompts = []
    scene = '\n'.join([
        'Margaret: "Too wet."',
        'Steph: "More flour?"',
        'Margaret: "No, hotter oven."',
    ] * 2)

    def generate(**kwargs):
        prompts.append(kwargs["prompt"])
        return scene

    monkeypatch.setattr(sim, "generate_response", generate)
    monkeypatch.setattr(
        sim,
        "_load_memories",
        lambda name: [{"concept": "Savory cups", "summary": f"Saved memory for {name}."}],
    )

    result = run(
        initial_highlights=["Earlier: flour delivery still unresolved."],
        retry_note="Nobody answered the skeptic.",
    )

    assert len(prompts) == 1
    assert "Method: Bake at 500 F." in prompts[0]
    assert "late delivery" in prompts[0]
    assert "flour delivery still unresolved" in prompts[0]
    assert "Nobody answered the skeptic." in prompts[0]
    assert "Saved memory for Margaret Chen." in prompts[0]
    assert result["messages"][1]["character"] == "Stephanie 'Steph' Whitmore"
    assert [m["message"] for m in result["messages"]] == result["name_strip_transcript"]
    assert result["metrics"]["message_count"] == 6
    assert result["qa"]["score"] >= 0


def test_authored_output_retries_malformed_scenes_and_fails_after_three(monkeypatch):
    names = sim.participants_for_day("monday")
    valid = '\n'.join(['Margaret: "Too wet."'] * 6)
    replies = iter(["Narrator: The kitchen is quiet.", valid])
    prompts = []
    monkeypatch.setattr(
        sim,
        "generate_response",
        lambda **kwargs: prompts.append(kwargs["prompt"]) or next(replies),
    )

    result = run()

    assert result["messages"]
    assert len(prompts) == 2
    assert "Your previous draft was rejected" in prompts[1]
    with pytest.raises(ValueError, match="off-roster") as failed:
        sim._parse_author_scene("Devon: \"Off roster.\"", names)
    assert failed.value.args

    prompts.clear()
    monkeypatch.setattr(
        sim,
        "generate_response",
        lambda **kwargs: prompts.append(kwargs["prompt"]) or "Narrator: x",
    )
    with pytest.raises(ValueError, match="off-roster") as exhausted:
        run()
    assert len(prompts) == sim._AUTHOR_PARSE_TRIES
    assert exhausted.value.author_invalid_scene == "Narrator: x"


def test_full_authored_week_keeps_saved_memories_and_wednesday_images(monkeypatch, tmp_path):
    prompts = []
    image_inputs = []
    memory_writes = []
    photos = []
    for filename in ("one.png", "two.png", "three.png"):
        path = tmp_path / filename
        path.write_bytes(sim._PNG_SIGNATURE + filename.encode())
        photos.append(str(path))

    def generate(**kwargs):
        prompts.append(kwargs["prompt"])
        image_inputs.append(kwargs.get("images"))
        day = sim.DAY_ORDER[len(prompts) - 1]
        name = sim.participants_for_day(day)[0]
        speaker = "Steph" if name.startswith("Stephanie") else name.split()[0]
        return f'{speaker}: "{day} stays unresolved."'

    monkeypatch.setattr(sim, "ROOT", tmp_path)
    monkeypatch.setattr(sim, "generate_response", generate)
    monkeypatch.setattr(
        sim,
        "_load_memories",
        lambda name: [{"concept": "Earlier recipe", "summary": "Remembered saved work."}],
    )
    monkeypatch.setattr(
        sim,
        "_generate_episode_memories",
        lambda messages, concept, *args: memory_writes.append((messages, concept)),
    )

    result = run(
        stage_only=None,
        mode="llm",
        image_paths=photos,
        photography_context={"winner_variant": "crispy edge"},
        initial_highlights=["The custard ratio was settled."],
    )

    assert len(prompts) == len(sim.DAY_ORDER)
    assert "The custard ratio was settled." in prompts[0]
    assert "Remembered saved work." in prompts[0]
    assert "monday stays unresolved" in prompts[1].lower()
    assert "tuesday stays unresolved" in prompts[2].lower()
    assert "crispy edge" in prompts[2]
    assert "Attached candidate images: 3" in prompts[2]
    assert image_inputs[2] == [
        sim._PNG_SIGNATURE + b"one.png",
        sim._PNG_SIGNATURE + b"two.png",
        sim._PNG_SIGNATURE + b"three.png",
    ]
    assert all(images is None for i, images in enumerate(image_inputs) if i != 2)
    assert all(any(photo in message["attachments"] for message in result["messages"])
               for photo in photos)
    assert len(memory_writes) == 1
    saved_messages, saved_concept = memory_writes[0]
    assert saved_concept == "Custard Cups"
    assert {message.day for message in saved_messages} == set(sim.DAY_ORDER)


def test_author_scene_parser_rejects_narration_and_accepts_literal_parentheses():
    names = sim.participants_for_day("monday")
    for invalid in ("Narrator: Everyone sits.", "They sit down.", 'Devon: "Off roster."'):
        with pytest.raises(ValueError):
            sim._parse_author_scene(invalid, names)
    assert sim._parse_author_scene(
        'Margaret: "Bake until set (looks firm at the edge)."', names
    ) == [("Margaret Chen", "Bake until set (looks firm at the edge).")]
    assert sim._parse_author_scene('Margaret: "Macro—then overhead. It’s final."', names) == [
        ("Margaret Chen", "Macro - then overhead. It's final.")
    ]


def _scene_for(day):
    first = [n.split()[0] if not n.startswith('Stephanie') else 'Steph' for n in sim.participants_for_day(day)]
    return '\n'.join(f'{first[i % len(first)]}: "Line {i}."' for i in range(8))


def test_wednesday_author_fetches_exact_review_urls_without_generating_images(monkeypatch):
    requested = []
    payloads = {f'https://store.public.blob.vercel-storage.com/{n}.png': sim._PNG_SIGNATURE + n.encode()
                for n in ('hero', 'overhead', 'detail')}

    class Response:
        def __init__(self, body):
            self.body = body
            self.headers = {'content-type': 'image/png'}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield self.body

        def close(self):
            return None

    def fetch(url, **kwargs):
        requested.append((url, kwargs))
        return Response(payloads[url])

    calls = []
    monkeypatch.setattr(sim.requests, 'get', fetch)
    monkeypatch.setattr(
        sim,
        'generate_response',
        lambda **kwargs: calls.append(kwargs) or _scene_for('wednesday'),
    )
    urls = list(payloads)
    paths = ['images/current/hero.png', 'images/current/overhead.png', 'images/current/detail.png']
    run(stage_only='wednesday', image_paths=paths, image_urls=urls,
        photography_context={'human_review': {'state': 'awaiting'}})

    assert [url for url, _ in requested] == urls
    assert calls[0]['images'] == [
        sim._PNG_SIGNATURE + b'hero',
        sim._PNG_SIGNATURE + b'overhead',
        sim._PNG_SIGNATURE + b'detail',
    ]
    assert len(calls) == 1, 'seeing images must not add a separate vision/model call'


def test_wednesday_author_prefers_recorded_url_over_existing_local_path(monkeypatch, tmp_path):
    local = tmp_path / 'candidate.png'
    local.write_bytes(sim._PNG_SIGNATURE + b'local-stale-pixels')
    remote = sim._PNG_SIGNATURE + b'recorded-review-pixels'

    class Response:
        headers = {'content-type': 'image/png'}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield remote

        def close(self):
            return None

    requested = []
    monkeypatch.setattr(
        sim.requests,
        'get',
        lambda url, **kwargs: requested.append(url) or Response(),
    )
    calls = []
    monkeypatch.setattr(
        sim,
        'generate_response',
        lambda **kwargs: calls.append(kwargs) or _scene_for('wednesday'),
    )
    url = 'https://store.public.blob.vercel-storage.com/images/candidate.png'

    run(stage_only='wednesday', image_paths=[str(local)], image_urls=[url])

    assert requested == [url]
    assert calls[0]['images'] == [remote]


def test_wednesday_author_uses_matching_filesystem_url_without_network(monkeypatch, tmp_path):
    relative = 'assets/images/current/candidate.png'
    local = tmp_path / relative
    local.parent.mkdir(parents=True)
    payload = sim._PNG_SIGNATURE + b'filesystem-pixels'
    local.write_bytes(payload)
    monkeypatch.setattr(sim, 'ROOT', tmp_path)
    monkeypatch.setattr(sim.requests, 'get', lambda *a, **k: pytest.fail('network fetch'))
    calls = []
    monkeypatch.setattr(
        sim,
        'generate_response',
        lambda **kwargs: calls.append(kwargs) or _scene_for('wednesday'),
    )

    run(stage_only='wednesday', image_paths=[relative], image_urls=['/' + relative])

    assert calls[0]['images'] == [payload]


def test_wednesday_author_refuses_untrusted_or_misaligned_candidate_sources(monkeypatch):
    monkeypatch.setattr(sim, 'generate_response', lambda **kwargs: pytest.fail('model called'))
    with pytest.raises(RuntimeError, match='not aligned'):
        run(stage_only='wednesday', image_paths=['a', 'b'],
            image_urls=['https://store.public.blob.vercel-storage.com/a.png'])
    with pytest.raises(RuntimeError, match='no readable trusted source'):
        run(stage_only='wednesday', image_paths=['missing.png'],
            image_urls=['https://example.com/missing.png'])


def test_template_wednesday_fetches_no_pixels_and_calls_no_provider(monkeypatch):
    monkeypatch.setattr(sim.requests, 'get', lambda *a, **k: pytest.fail('network fetch'))
    monkeypatch.setattr(sim, 'generate_response', lambda **k: pytest.fail('provider call'))
    result = run(
        stage_only='wednesday',
        mode='template',
        image_paths=['images/current/hero.png'],
        image_urls=['https://store.public.blob.vercel-storage.com/hero.png'],
    )
    assert result['messages']


def test_cron_dialogue_threads_the_current_candidate_urls_to_the_author(monkeypatch):
    captured = []
    monkeypatch.setattr(
        cron_routes,
        '_get_run_simulation',
        lambda: lambda **kwargs: captured.append(kwargs) or {'messages': [{'message': 'ok'}]},
    )
    urls = [f'https://store.public.blob.vercel-storage.com/{name}.png'
            for name in ('hero', 'overhead', 'detail')]
    result = cron_routes._generate_dialogue(
        'wednesday',
        'Custard Cups',
        image_paths=['hero.png', 'overhead.png', 'detail.png'],
        image_urls=urls,
        model='stub',
    )
    assert result == [{'message': 'ok'}]
    assert captured[0]['image_urls'] == urls
