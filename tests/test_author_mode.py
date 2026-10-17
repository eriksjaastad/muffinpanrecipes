"""Whole-scene Monday experiment; no provider calls or production writes (#8383)."""

import random
import subprocess
import sys
import types

import pytest

from scripts import conversation_lab as lab
from scripts import simulate_dialogue_week as sim


def run(module=sim, **kwargs):
    return module.run_simulation(
        concept='Custard Cups', default_model='stub', run_index=1,
        stage_only=kwargs.pop('stage_only', 'monday'), injected_event='late delivery',
        ticks_per_day=0, mode='openai', prompt_style='scene', character_models=None,
        recipe_context='Lemon custard in pastry', recipe_facts='Method: Bake at 500 F.',
        **kwargs,
    )


def test_author_one_call_owns_scene_and_keeps_result_contract(monkeypatch):
    prompts = []
    scene = '\n'.join(['Margaret: Too wet.', 'Steph: More flour?', 'Margaret: No, hotter oven.'] * 2)

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
    valid = ['Margaret: Too wet.'] * 6
    for bad in ('Devon: Okay.', 'Narrator: Everyone sits.', 'They sit down.',
                'Margaret: *sighs* Okay.', 'Margaret: [sighs] Okay.', 'Margaret:'):
        with pytest.raises(ValueError, match='Author scene line'):
            sim._parse_author_scene('\n'.join([bad] + valid), names, 'monday')
    with pytest.raises(ValueError, match='empty'):
        sim._parse_author_scene('', names, 'monday')
    monkeypatch.setattr(sim, 'AUTHOR_MODE', True)
    monkeypatch.setattr(sim, 'generate_response', lambda **k: 'Devon: Off roster.')
    monkeypatch.setattr(sim, 'generate_turn', lambda **k: pytest.fail('fallback called'))
    with pytest.raises(ValueError, match='off-roster') as failed:
        run()
    assert failed.value.author_invalid_scene == 'Devon: Off roster.'
    with pytest.raises(ValueError, match='Monday-only'):
        run(stage_only='tuesday')


def test_default_off_prompts_are_byte_identical_to_main(monkeypatch):
    source = subprocess.run(
        ['git', 'show', 'origin/main:scripts/simulate_dialogue_week.py'],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout
    main = types.ModuleType('_author_main_reference')
    main.__file__ = sim.__file__
    monkeypatch.setitem(sys.modules, main.__name__, main)
    exec(compile(source, sim.__file__, 'exec'), main.__dict__)
    assert sim.AUTHOR_MODE is False
    captures = []
    for module in (main, sim):
        captured = []
        monkeypatch.setattr(module, '_load_memories', lambda name: [])
        monkeypatch.setattr(module, '_load_memories_or_unavailable', lambda name: ([], False))

        def fake(**kwargs):
            captured.append(kwargs)
            return 'Try hotter custard.'

        monkeypatch.setattr(module, 'generate_response', fake)
        for day in sim.DAY_ORDER:
            random.seed(8383)
            run(module, stage_only=day)
        captures.append(captured)
    assert captures[0] == captures[1]
    assert len(captures[0]) > 40


def test_length_matching_regenerates_twice_and_reports_exceptions(monkeypatch):
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
    # The second limit is independent: equal words alone cannot match a pair.
    assert not lab._pair_length_match(reference, reference + reference)['matched']
    assert not lab._pair_length_match(reference, [{'message': 'one two three four ' * 6}])['matched']
    eleven = [{'message': 'one two three four'}] * 11
    assert len(sim._parse_author_scene('\n'.join(['Margaret: Okay.'] * 11), sim.participants_for_day('monday'), 'monday')) == 11
    assert not lab._pair_length_match(eleven, eleven)['matched']


def test_balance_reserve_stops_next_paid_call(monkeypatch):
    monkeypatch.setattr(lab, '_OPENROUTER_MIN_BALANCE', 20.0)
    monkeypatch.setattr(lab, '_openrouter_fetch_account_balance', lambda: 20.01)
    monkeypatch.setattr(lab, '_openrouter_worst_case_call_cost', lambda *a: 0.02)
    monkeypatch.setattr(lab, '_total_cost_or_none', lambda: 0.0)
    with lab._installed_budget_guard(lab.CallBudget(max_calls=100), 3):
        with pytest.raises(lab.LabBudgetAbort, match='min-balance'):
            lab.model_router._fire_pre_call_hook('openrouter', 'stub', 10)
