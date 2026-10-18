"""Whole-scene Monday experiment; no provider calls or production writes (#8383)."""

import random
import hashlib
import json
from argparse import Namespace
from pathlib import Path

import pytest

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
    monkeypatch.setattr(sim, 'AUTHOR_MODE', True)
    monkeypatch.setattr(sim, 'generate_response', lambda **k: 'Devon: Off roster.')
    monkeypatch.setattr(sim, 'generate_turn', lambda **k: pytest.fail('fallback called'))
    with pytest.raises(ValueError, match='off-roster') as failed:
        run()
    assert failed.value.author_invalid_scene == 'Devon: Off roster.'

def test_author_full_week_carries_lines_and_finishes_wednesday_photography(monkeypatch):
    prompts, calls = [], []
    def generate(**kwargs):
        prompts.append(kwargs['prompt'])
        day = sim.DAY_ORDER[len(prompts) - 1]
        name = sim.participants_for_day(day)[0].split()[0]
        return '\n'.join([f'{name}: "{day} stays unresolved."'] * (3 if day == 'wednesday' else 2))

    monkeypatch.setattr(sim, 'AUTHOR_MODE', True)
    monkeypatch.setattr(sim, 'generate_response', generate)
    monkeypatch.setattr(sim, '_generate_day_highlights', lambda **k: pytest.fail('unexpected highlight call'))
    monkeypatch.setattr(sim, '_generate_episode_memories', lambda *a, **k: calls.append(a[1]))
    photos = ['one.png', 'two.png', 'three.png']
    result = run(stage_only=None, mode='llm', image_paths=photos,
                 photography_context={'winner_variant': 'crispy edge'})

    assert len(prompts) == len(sim.DAY_ORDER)
    assert 'monday stays unresolved' in prompts[1].lower()
    assert 'tuesday stays unresolved' in prompts[2].lower()
    assert 'crispy edge' in prompts[2]
    assert all(any(photo in m['attachments'] for m in result['messages']) for photo in photos)
    assert calls == ['Custard Cups']
    assert {m['day'] for m in result['messages']} == set(sim.DAY_ORDER)


def test_default_off_prompts_are_byte_identical_to_main(monkeypatch):
    # Immutable pre-change captures: no remote, Git history or moving ref needed.
    fixture = json.loads((Path(__file__).parent / 'fixtures/author-off-prompts.json').read_text())
    assert sim.AUTHOR_MODE is False
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
