"""Whole-scene Monday experiment; no provider calls or production writes (#8383)."""

import random
import hashlib
import json
from argparse import Namespace
from pathlib import Path

import pytest

from backend.admin import cron_routes
from scripts import conversation_lab as lab
from scripts import simulate_dialogue_week as sim


def run(module=sim, **kwargs):
    return module.run_simulation(
        concept='Custard Cups', default_model='stub', run_index=1,
        stage_only=kwargs.pop('stage_only', 'monday'), injected_event='late delivery',
        ticks_per_day=0, mode=kwargs.pop('mode', 'openai'), prompt_style='scene', character_models=None,
        recipe_context='Lemon custard in pastry', recipe_facts='Method: Bake at 500 F.',
        **kwargs,
    )


def test_author_one_call_owns_scene_and_keeps_result_contract(monkeypatch):
    prompts = []
    scene = '\n'.join(['Margaret: "Too wet."', 'Steph: "More flour?"', 'Margaret: "No, hotter oven."'] * 2)

    def generate(**kwargs):
        prompts.append(kwargs)
        return scene

    monkeypatch.setattr(sim, 'AUTHOR_MODE', True)
    monkeypatch.setattr(sim, 'generate_response', generate)
    monkeypatch.setattr(sim, '_select_next_speaker', lambda *a: pytest.fail('scheduler called'))
    monkeypatch.setattr(sim, 'generate_turn', lambda **k: pytest.fail('per-line path called'))
    result = run(initial_highlights=['Earlier: flour delivery still unresolved.'])
    assert len(prompts) == 1
    prompt = prompts[0]['prompt']
    assert 'Method: Bake at 500 F.' in prompt
    assert 'late delivery' in prompt and 'flour delivery still unresolved' in prompt
    assert 'tendencies, not rules' in prompt and 'may stay silent' in prompt
    assert 'MAXIMUM 15 words' not in prompt and 'Your goal today' not in prompt
    assert result['messages'][1]['character'] == "Stephanie 'Steph' Whitmore"
    assert [m['message'] for m in result['messages']] == result['name_strip_transcript']
    assert result['metrics']['message_count'] == 6
    assert result['qa']['score'] >= 0
    assert sim.REWRITE_LOG == sim.STOP_CHECK_LOG == sim.DIRECTOR_LOG == []


def test_author_parser_fails_loudly_for_bad_scene_without_fallback(monkeypatch):
    names = sim.participants_for_day('monday')
    valid = ['Margaret: "Too wet."'] * 6
    for bad in ('Devon: Okay.', 'Narrator: Everyone sits.', 'They sit down.',
                'Margaret: *sighs* Okay.', 'Margaret: [sighs] Okay.',
                'Margaret: Fine. (sighs)', 'Margaret: (dryly) Fine.',
                'Margaret: [takes a sip] Fine.', 'Margaret: (beat) Fine.',
                'Margaret: "Fine." [takes a sip]', 'Margaret:'):
        with pytest.raises(ValueError, match='Author scene line'):
            sim._parse_author_scene('\n'.join([bad] + valid), names)
    with pytest.raises(ValueError, match='empty'):
        sim._parse_author_scene('', names)
    spoken = 'Margaret: "Bake at 350 F (175 C), but use *less* flour."'
    assert sim._parse_author_scene(spoken, names) == [
        ('Margaret Chen', 'Bake at 350 F (175 C), but use *less* flour.')
    ]
    assert sim._parse_author_scene('Margaret: "(175 C) and [optional] vanilla."', names) == [
        ('Margaret Chen', '(175 C) and [optional] vanilla.')
    ]
    assert sim._parse_author_scene('Margaret: "Bake until set (looks firm at the edge)."', names) == [
        ('Margaret Chen', 'Bake until set (looks firm at the edge).')
    ]
    assert sim._parse_author_scene('Margaret: "Macro—then overhead. It’s final."', names) == [
        ('Margaret Chen', "Macro - then overhead. It's final.")
    ]
    monkeypatch.setattr(sim, 'AUTHOR_MODE', True)
    monkeypatch.setattr(sim, 'generate_response', lambda **k: 'Devon: Off roster.')
    monkeypatch.setattr(sim, 'generate_turn', lambda **k: pytest.fail('fallback called'))
    with pytest.raises(ValueError, match='off-roster') as failed:
        run()
    assert failed.value.author_invalid_scene == 'Devon: Off roster.'

def test_author_full_week_carries_lines_and_finishes_wednesday_photography(monkeypatch, tmp_path):
    prompts, calls, image_inputs = [], [], []
    def generate(**kwargs):
        prompts.append(kwargs['prompt'])
        image_inputs.append(kwargs.get('images'))
        day = sim.DAY_ORDER[len(prompts) - 1]
        name = sim.participants_for_day(day)[0].split()[0]
        return '\n'.join([f'{name}: "{day} stays unresolved."'] * (3 if day == 'wednesday' else 2))

    monkeypatch.setattr(sim, 'AUTHOR_MODE', True)
    monkeypatch.setattr(sim, 'ROOT', tmp_path)
    monkeypatch.setattr(sim, 'generate_response', generate)
    monkeypatch.setattr(sim, '_generate_day_highlights', lambda **k: pytest.fail('unexpected highlight call'))
    monkeypatch.setattr(sim, '_generate_episode_memories', lambda *a, **k: calls.append(a[1]))
    photos = []
    for name in ('one.png', 'two.png', 'three.png'):
        path = tmp_path / name
        path.write_bytes(sim._PNG_SIGNATURE + name.encode())
        photos.append(str(path))
    result = run(stage_only=None, mode='llm', image_paths=photos,
                 photography_context={'winner_variant': 'crispy edge'})

    assert len(prompts) == len(sim.DAY_ORDER)
    assert 'monday stays unresolved' in prompts[1].lower()
    assert 'tuesday stays unresolved' in prompts[2].lower()
    assert 'crispy edge' in prompts[2]
    assert 'Attached candidate images: 3' in prompts[2]
    assert image_inputs[2] == [
        sim._PNG_SIGNATURE + b'one.png',
        sim._PNG_SIGNATURE + b'two.png',
        sim._PNG_SIGNATURE + b'three.png',
    ]
    assert all(images is None for i, images in enumerate(image_inputs) if i != 2)
    assert all(any(photo in m['attachments'] for m in result['messages']) for photo in photos)
    assert calls == ['Custard Cups']
    assert {m['day'] for m in result['messages']} == set(sim.DAY_ORDER)


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


def test_per_line_prompts_are_byte_identical_to_main(monkeypatch):
    # Immutable pre-change captures: no remote, Git history or moving ref needed.
    fixture = json.loads((Path(__file__).parent / 'fixtures/author-off-prompts.json').read_text())
    monkeypatch.setattr(sim, 'AUTHOR_MODE', False)
    monkeypatch.setattr(sim, '_load_memories', lambda name: [])
    monkeypatch.setattr(sim, '_load_memories_or_unavailable', lambda name: ([], False))
    for day in sim.DAY_ORDER:
        captured = []

        def fake(**kwargs):
            captured.append(kwargs)
            return 'Try hotter custard.'

        monkeypatch.setattr(sim, 'generate_response', fake)
        random.seed(8383)
        run(stage_only=day)
        digest = hashlib.sha256(json.dumps(captured, sort_keys=True).encode()).hexdigest()
        assert digest == fixture['day_sha256'][day]


def test_length_matching_regenerates_twice_and_reports_exceptions(monkeypatch):
    monkeypatch.setattr(sim, 'AUTHOR_MODE', False)  # the variant must restore it
    assert lab._arm_call_reservation('monday', {'AUTHOR_MODE': True}, dry_run=False) == 9
    reference = [{'character': 'Margaret Chen', 'message': 'one two three four'}] * 6
    oversized = [{'character': 'Margaret Chen', 'message': 'one two three four five six'}] * 6
    monkeypatch.setattr(lab, '_lab_cost_total', lambda: 0.0)
    monkeypatch.setattr(lab, '_would_exceed_cost', lambda *a: False)
    monkeypatch.setattr(lab, 'summarize', lambda *a, **k: {})
    for ends_matched in (True, False):
        calls = []

        def arm(*args, **kwargs):
            calls.append(kwargs)
            messages = reference if len(calls) == 1 or (ends_matched and len(calls) == 4) else oversized
            return {'messages': messages}, 1

        monkeypatch.setattr(lab, '_run_arm_and_count', arm)
        monkeypatch.setattr(lab, '_run_judge_orientation', lambda **k: lab._dry_run_combined())
        pairs = []
        budget = lab.CallBudget(max_calls=100)
        aborted = lab._generate_and_judge_pairs(
            concept='Cups', stage='monday', recipe_context='Custard', runs=1,
            variant={'AUTHOR_MODE': True}, mode='openai', default_model='stub',
            judge_model='stub', expected_cast=sim.participants_for_day('monday'),
            budget=budget, max_cost=3, dry_run=False, pairs=pairs,
        )
        assert not aborted and budget.used == 4
        assert len(pairs[0]['author_attempts']) == 3
        assert pairs[0]['length_match']['matched'] == ends_matched
        assert calls[-1]['author_length_target'] == {'lines': 6, 'words': 24}
        assert sim.AUTHOR_MODE is False
        report = {'pairs': pairs, 'meets_decision_rule': True}
        lab._annotate_author_report(report)
        assert report['meets_decision_rule'] is False
        assert report['judge_meets_numeric_rule'] is True
        assert report['length_matching']['unmatched_pair_positions'] == ([] if ends_matched else [1])
    # The second limit is independent: equal words alone cannot match a pair.
    assert not lab._pair_length_match(reference, reference + reference)['matched']
    assert not lab._pair_length_match(reference, [{'message': 'one two three four ' * 6}])['matched']
    eleven = [{'message': 'one two three four'}] * 11
    assert len(sim._parse_author_scene('\n'.join(['Margaret: "Okay."'] * 11), sim.participants_for_day('monday'))) == 11
    assert not lab._pair_length_match(eleven, eleven)['matched']


def test_balance_reserve_stops_next_paid_call(monkeypatch):
    monkeypatch.setattr(lab, '_OPENROUTER_MIN_BALANCE', 20.0)
    monkeypatch.setattr(lab, '_openrouter_fetch_account_balance', lambda: 20.01)
    monkeypatch.setattr(lab, '_openrouter_worst_case_call_cost', lambda *a: 0.02)
    monkeypatch.setattr(lab, '_total_cost_or_none', lambda: 0.0)
    with lab._installed_budget_guard(lab.CallBudget(max_calls=100), 3):
        with pytest.raises(lab.LabBudgetAbort, match='min-balance'):
            lab.model_router._fire_pre_call_hook('openrouter', 'stub', 10)
    monkeypatch.setattr(lab, '_OPENROUTER_KEY_BEFORE', None)
    monkeypatch.setattr(lab, '_require_openrouter_key', lambda: None)
    monkeypatch.setattr(lab, '_openrouter_fetch_key', lambda: {'limit': 100.0, 'limit_remaining': 100.0})
    with pytest.raises(SystemExit, match='min-balance reserve'):
        lab._openrouter_preflight(Namespace(max_cost=3))


def test_author_screen_reserves_the_per_line_control_budget(monkeypatch):
    monkeypatch.setattr(lab, '_lab_cost_total', lambda: 0.0)
    monkeypatch.setattr(lab, '_run_arm_and_count', lambda *a, **k: pytest.fail('under-reserved control started'))
    pairs = []
    assert lab._generate_and_judge_pairs(
        concept='Cups', stage='monday', recipe_context=None, runs=1,
        variant={'AUTHOR_MODE': True}, mode='openai', default_model='stub',
        judge_model='stub', expected_cast=sim.participants_for_day('monday'),
        budget=lab.CallBudget(max_calls=9), max_cost=3, dry_run=False, pairs=pairs,
    )
    assert pairs == []


def _scene_for(day):
    first = [n.split()[0] if not n.startswith('Stephanie') else 'Steph' for n in sim.participants_for_day(day)]
    return '\n'.join(f'{first[i % len(first)]}: "Line {i}."' for i in range(8))


def test_production_writes_each_day_with_the_author_and_feeds_it_the_judges_reason(monkeypatch):
    """#8384: the author is the default, and a retry tells it why the last draft failed."""
    prompts = []
    monkeypatch.setattr(sim, 'generate_turn', lambda **k: pytest.fail('per-line path called'))
    for day in sim.DAY_ORDER:
        monkeypatch.setattr(sim, 'generate_response',
                            lambda day=day, **k: prompts.append(k['prompt']) or _scene_for(day))
        run(stage_only=day, retry_note='Nobody answered the skeptic.',
            photography_context={'human_review': {'approved': 2}})
    assert len(prompts) == len(sim.DAY_ORDER)
    assert all('An earlier draft of this meeting was rejected. Nobody answered the skeptic.' in p
               for p in prompts)
    assert all('"approved": 2' in p for p in prompts)


@pytest.mark.parametrize('julian_lines', [1, 2, 3])
def test_scatter_delivery_never_drops_a_photo(monkeypatch, julian_lines):
    monkeypatch.setattr(sim.random, 'choice', lambda options: 'scatter')
    msgs = [sim.Message('wednesday', 'photo', 'Julian Torres' if i < julian_lines else 'Margaret Chen',
                        'line', '', 'stub') for i in range(4)]
    sim._distribute_images_wednesday(msgs, ['a.png', 'b.png', 'c.png'], 'wednesday')
    assert sorted(p for m in msgs for p in m.attachments) == ['a.png', 'b.png', 'c.png']


def test_a_rejected_author_reply_is_rewritten_and_three_rejections_raise(monkeypatch):
    replies = iter(['Narrator: The kitchen is quiet.', _scene_for('monday')])
    prompts = []
    monkeypatch.setattr(sim, 'generate_response', lambda **k: prompts.append(k['prompt']) or next(replies))
    result = run()
    assert len(prompts) == 2 and 'Your previous draft was rejected' in prompts[1]
    assert result['messages']

    prompts.clear()
    monkeypatch.setattr(sim, 'generate_response', lambda **k: prompts.append(k['prompt']) or 'Narrator: x')
    with pytest.raises(ValueError):
        run()
    assert len(prompts) == sim._AUTHOR_PARSE_TRIES


def test_the_author_sees_each_characters_saved_memories(monkeypatch):
    """Per-line speakers get their recent weeks from _load_memories; the author must too."""
    prompts = []
    monkeypatch.setattr(sim, '_load_memories', lambda name: [
        {'concept': 'Lemon Bars', 'summary': f'{name.split()[0]} burned the crust.'}])
    monkeypatch.setattr(sim, 'generate_response', lambda **k: prompts.append(k['prompt']) or _scene_for('monday'))
    run()
    assert all(f"- Lemon Bars: {n.split()[0]} burned the crust." in prompts[0]
               for n in sim.participants_for_day('monday'))
