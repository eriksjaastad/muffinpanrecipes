# Voice distinctiveness: evidence and next measurements

Research date: 2026-09-23. Card **#7522** owns this investigation. **#7469**
owns the lexical attribution metric in PR #126; **#7521** records the future
Haiku 4.5 / Opus 5.5 / large DeepSeek comparison. This report starts from the
recorded March research, DIALS.md, PROTOCOL.md, and the September calibration.
It does not establish a dialogue improvement or authorize a production change.

## Three questions that need separate answers

| Question | Required evidence | What does not establish it |
|---|---|---|
| Can a reader distinguish speakers within a scene? | Repeated speech from each person, attribution with adequate coverage, blind reader judgments | Correct job nouns or different message lengths alone |
| Does each speaker match the intended character? | Explicit character references and judgments of behavior/style against those references | A name attached to a line; within-scene separability alone |
| Is the conversation worth reading? | Coherent responses, motivated disagreement, resolution, factual credibility, human preference | Either attribution accuracy or rubric compliance alone |

A consistent permutation of speaker identities preserves anonymous separability.
It can violate intended character identity. A test expecting the former to fall
just because names changed is testing the wrong property. Conversely, changing
labels independently across turns can damage within-speaker consistency. These
are different manipulations and must not share an unexplained expected answer.

## What the local evidence establishes

At source `2b426d40ca16ab0c603f023cb029afda2fc902ae`, the pairwise judge combines
separability and identity in one `voice_distinctiveness` description, but
`_build_pairwise_prompt` supplies names, roster, recipe facts and transcripts,
not character voice guides. This is enough to assess some differences between
voices, but supplies no explicit reference for whether the identities are right.

The [September 23 calibration](../conversation-lab/EXPERIMENTS.md) used W25
Thursday: five turns, four speakers, with only Marcus speaking twice.
`_rotate_speakers` shifts the sequence of turn labels, not a consistent mapping
of people; all three seeds perform the same transformation. Three repeated judge
trials therefore do not provide three different degraded conversations.

The original result contains three combined ties for that manipulation. The
original code discards completed pairs' orientation records, and a combined tie
can mean two explicit ties or disagreement between orders. Missing verdict fields
can also default to ties. The archived data cannot reveal which occurred. Do not
retroactively assign raw answers or claim the judge failed to recognize a proven
voice degradation. Keep the original artifacts unchanged.

The existing attribution report measures same-week content-word predictability.
Its reported 26.85% weighted accuracy versus 17.16% chance is a lexical signal,
not a grade for voice quality. Role-specific vocabulary can supply that signal.
PR #126 already documents this limitation and remains a separate held change.

An offline probe of its exact reviewed source
`e8af4ae928bb95b884ef2ef84b6e78e6717f1428` makes the limits concrete:

| Constructed probe | Attribution accuracy | Interpretation |
|---|---:|---|
| Three groups with deliberately different vocabularies | 100% | The classifier can detect lexical separation |
| Same messages, consistent anonymous renaming | 100% | It does not establish identity fidelity |
| Two speakers using identical sentence templates with different job nouns | 100% | Topic alone can produce a perfect score |
| Same content-word bags, different word order | 50%, equal to chance | Unigrams do not measure syntax |
| Actual five-line W25 Thursday | unavailable; 2/5 lines scored | Too little repeated-speaker evidence for the aggregate |

These are deliberately constructed diagnostic cases, not human-labeled quality
examples. [The complete inputs, outputs and source hashes](../conversation-lab/results/20260923-voice-attribution-audit.json)
are preserved. To reproduce the metric outputs, load `speaker_attribution` from
the recorded PR #126 source and call it on each scenario's `messages` (the W25
scenario uses `source_turns`); do not
replace the source with a later evaluator. The rotation section also preserves
the exact original/rotated transcripts and confirms identical outputs for seeds
1, 2 and 3.

`build_system_prompt` provides substantial character-specific material: biography,
contradictions, relationships, voice guide, examples, memories or a first-week
fallback, signature phrases, and triggers. The four numeric personality traits
are not rendered, but that does not mean the characters lack descriptive prose.
The known DIALS.md section 2(c) denominator/coverage issue remains card #7430;
this report does not use its ratio as evidence for a generator change.

Wednesday's source also gives the opener a predetermined photography winner and
later supplies a wind-down instruction saying the decision has been made. The
photography path floors the scene at seven turns without a reshoot, ten with one.
These are plausible constraints on disagreement and resolution, not proof that
longer dialogue or hidden information will improve the result. See #7161 and
#7352; preserve the existing authorization gate on automatic injected events.

## Research that fits parts of the problem

**Character identity is broader than linguistic imitation.** InCharacter evaluates
personality fidelity through interviews rather than relying only on knowledge
and language patterns. It supports distinguishing behavioral fidelity from
surface recognizability. Its fictional-character personality scales are not a
validated rubric for this site's six-person food conversation.
[Wang et al., ACL 2024](https://arxiv.org/abs/2310.17976).

**Human reference labels matter.** CharacterEval evaluates multiple aspects of
role-playing and uses human quality control and annotations. Its Chinese-language
results cannot rank our English dialogue models. The applicable lesson is to
ground subjective scores in reviewed examples, not to import its leaderboard or
reward model as our production gate.
[Tu et al., ACL 2024](https://aclanthology.org/2024.acl-long.638/).

**Attribution can confuse topic with style.** The Topic Confusion Task changes
author/topic associations to expose this failure. For us, identifying Julian by
photography nouns is different from recognizing Julian's voice. A future metric
validation should include same-topic characters and topic-shift controls; the
paper's results do not establish performance on our short chat messages.
[Altakrori et al., Findings of EMNLP 2021](https://aclanthology.org/2021.findings-emnlp.359/).

**Order swapping exposes disagreement; it does not make it disappear.** A study
of multiple judges across many pairwise tasks distinguishes repetition stability,
position consistency and preference fairness. We should retain each orientation
and report agreement explicitly. Two orders yielding different winners are
uncertain evidence, even if our conservative shipping calculation calls them a
tie. Neither a fixed judge nor reproducible output alone proves validity.
[Shi et al., 2024](https://arxiv.org/abs/2406.07791).

**Behavioral variation is a promising generator lever.** PersonaWeaver separates
setting/biography from conversational reactions and reports greater behavioral
and stylistic variation than its comparison methods. That gives #7161 a research
lead: vary how a character responds for a concrete reason, rather than add more
biography or catchphrases. The study uses character populations and moral/
interaction probes; it does not prove that randomly making our cast disagree
will produce better scenes. Its coarse moral tasks are an acknowledged limit.
[Qraitem et al., 2026 preprint](https://arxiv.org/abs/2601.03396).

**Information distribution changes the task.** SOTOPIA studies goal-directed
social interaction, while follow-up work shows that omniscient simulations can
overstate performance relative to interactions with asymmetric information.
This makes character-specific knowledge worth investigating, but also warns that
it can make the task harder. It is not evidence that withholding recipe facts
would help; those facts are required for factual credibility.
[Zhou et al., SOTOPIA](https://arxiv.org/abs/2310.11667),
[Zhou et al., EMNLP 2024](https://arxiv.org/abs/2403.05020).

## Measurement work before another quality claim

1. Make judge records inspectable: require complete verdict fields; retain exact
   inputs, raw answers, arm mappings, and both orientations for successful and
   partial pairs. Report order disagreement separately from unanimous ties.
   Preserve the existing conservative decision rule while repairing the evidence.
2. Prepare a small reference set containing real scenes and explicitly synthetic
   controls. Include identical-pair ties, consistent renaming, inconsistent label
   mixing with repeated speakers, same-topic different styles, different-topic
   same styles, and weak/strong discourse examples. State the targeted property
   for every comparison. Synthetic expected outcomes are hypotheses until reviewed,
   not human ground truth.
3. Have Erik review the intended character references and ambiguous examples.
   Separate examples used to refine the rubric from held-out calibration cases.
   Judge calibration should report valid-response coverage, order consistency,
   repeatability and agreement with the reference labels per property. Choose
   acceptance thresholds before spending; do not tune them to obtain a PASS.
4. Freeze the selected panel, prompts, model IDs/settings, evaluator code and
   rubric. A changed evaluator starts a new scoring version; re-score a common
   saved set instead of comparing unlike historical numbers. The existing v3
   panel and production eight-dimension scores remain the experiment baseline.
5. Resume a single authorized prompt experiment only after interpreting the
   calibration evidence. Keep #7472's rules ablation separate from #7161's
   behavior hypothesis, #6966's unbound numeric traits, and #7521's model tests.
   Preserve the shared $5 ledger; research and offline tests here spend nothing.

## Historical correction and future model card

The March 5 generation comparison really happened: five GPT-5.1 runs and seven
Haiku 4.5 runs. The March 14 model comparison concerned compression. The
September 17 high-reasoning Astra probe measured structure without a quality
verdict. No isolated reasoning-effort experiment was found in the records read.
The new lab's empty completed-A/B table does not mean the project never tested
models or prompts. See [the March report](MODEL_COMPARISON_REPORT.md).

Opus 5.5 was released on September 22 according to
[Anthropic's announcement](https://www.anthropic.com/claude-opus-5-5).
[DeepSeek's current API documentation](https://api-docs.deepseek.com/) lists
`deepseek-v4-pro` and `deepseek-flash`; #7521 must identify the actual large-model
deployment Erik uses before a test. Provider aliases, coding success and external
benchmarks do not establish English character-dialogue quality. Model comparison
and reasoning-effort comparison will be distinct experiments, with one fixed
evaluator across candidate models and explicit budgets before paid calls.
