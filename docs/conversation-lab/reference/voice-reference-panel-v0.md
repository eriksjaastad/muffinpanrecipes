# Voice reference panel v0 — human calibration draft

**Purpose:** collect Erik’s reference judgments for a few narrowly targeted comparisons before any judge calibration. This packet contains no assigned human labels and makes no claim about model quality. Treat every expected relation below as a hypothesis to review.

Read each comparison only for its named property. Ignore presentation order where possible. The machine-readable inputs and transformations are in [voice-reference-panel-v0.json](voice-reference-panel-v0.json). Source provenance and exact raw JSON line indexes are recorded there.

## 1. Identical-pair control

**Target:** directional preference on identical content.

A and B are the exact same saved W25 Monday five-turn excerpt:

1. **Margaret Chen:** Coffee's cold. We're doing muffin pan recipes weekly - harissa chickpea feta cups this week. Breakfast format. Thoughts before we lock it.
2. **Ria Castillo:** Cold coffee tracks - anyway, muffin pan format is smart because it's portion control and the golden edges are gonna pop on video, but we need to nail the moment she pulls one out hot with that feta melting. Is that the hook or do we lead with the chickpea crunch?
3. **Margaret Chen:** The feta doesn't melt that fast - it softens. And we don't need a "hook" if the dish is actually good.
4. **Ria Castillo:** Softening feta is the hook - that's literally the visual that stops the scroll pm.
5. **Margaret Chen:** Scroll-stopping is not a cooking technique. Make it right and people will watch.

**B:** same five turns, same labels, same wording, same order.

**Other factors:** none; inputs are identical.

**Structural hypothesis:** logical tie. A preference would indicate order/presentation noise, not a content difference.

## 2. Consistent Margaret ↔ Ria name permutation

**Target:** anonymous speaker separability under consistent relabeling.

**A — saved W25 Monday:**

1. **Margaret Chen:** Coffee's cold. We're doing muffin pan recipes weekly - harissa chickpea feta cups this week. Breakfast format. Thoughts before we lock it.
2. **Ria Castillo:** Cold coffee tracks - anyway, muffin pan format is smart because it's portion control and the golden edges are gonna pop on video, but we need to nail the moment she pulls one out hot with that feta melting. Is that the hook or do we lead with the chickpea crunch?
3. **Margaret Chen:** The feta doesn't melt that fast - it softens. And we don't need a "hook" if the dish is actually good.
4. **Ria Castillo:** Softening feta is the hook - that's literally the visual that stops the scroll pm.
5. **Margaret Chen:** Scroll-stopping is not a cooking technique. Make it right and people will watch.

**B — same utterances, permuted labels:**

1. **Ria Castillo:** Coffee's cold. We're doing muffin pan recipes weekly - harissa chickpea feta cups this week. Breakfast format. Thoughts before we lock it.
2. **Margaret Chen:** Cold coffee tracks - anyway, muffin pan format is smart because it's portion control and the golden edges are gonna pop on video, but we need to nail the moment she pulls one out hot with that feta melting. Is that the hook or do we lead with the chickpea crunch?
3. **Ria Castillo:** The feta doesn't melt that fast - it softens. And we don't need a "hook" if the dish is actually good.
4. **Margaret Chen:** Softening feta is the hook - that's literally the visual that stops the scroll pm.
5. **Ria Castillo:** Scroll-stopping is not a cooking technique. Make it right and people will watch.

**Erik decision (2026-09-23):** “No, names are part of voice scoring.” A consistent Margaret/Ria rename is therefore not an expected tie for the project voice score. Anonymous separability remains a separate diagnostic. The original named-character fit is a source-based hypothesis, not a human-labeled directional result. Margaret’s and Ria’s current references are [Margaret’s bio](../../../backend/data/characters/margaret-chen/bio.md) and [Ria’s bio](../../../backend/data/characters/ria-castillo/bio.md). The excerpt has three turns labeled Margaret and two labeled Ria in A; both characters recur.

**Structural hypothesis:** anonymous separability is invariant under this consistent permutation. The project score may still change because it includes named-character fit.

## 3. Inconsistent one-turn-each label exchange

**Target:** within-character consistency when recurring labels are inconsistently assigned.

Start from W25 Monday A above. In B, turn 2 is labeled **Margaret Chen** instead of Ria Castillo, and turn 3 is labeled **Ria Castillo** instead of Margaret Chen. All five utterances and their order stay exactly the same; turns 1, 4, and 5 keep their original labels.

**Other factors:** each character still has three labeled turns, with one utterance reassigned to each. A reader may also notice a mismatch between the words and printed label.

**Structural hypothesis:** B should give weaker evidence of stable within-speaker identity than A; detectability and size of the difference are unknown.

## 4. W38 Wednesday concrete pushback

**Target:** concrete pushback in the photography discussion.

**A — saved W38 Wednesday, seven turns:**

1. **Julian Torres:** Just got the shots processed. Three totally different reads on these spiral bites - macro detail, geometric overhead, and the three-quarter with the break. Ready to talk through which one actually leads.
2. **Stephanie 'Steph' Whitmore:** The three-quarter break feels right to me - you can actually see that glossy orange-rose hit when it's torn open, and I think that's what stops the scroll.
3. **Ria Castillo:** Yeah, the break works, but we need to see the steam too - that glossy glaze catching light while it's still hot is what makes someone stop scrolling pm.
4. **Stephanie 'Steph' Whitmore:** Right, the steam rising off it - that's the moment someone actually *wants* one, not just thinks it looks nice.
5. **Julian Torres:** The steam's the problem though - it's unpredictable, reads as blur on mobile, kills the cardamom detail that makes this actually different from a regular cinnamon roll.
6. **Margaret Chen:** The cardamom's not visible in any of those shots - that's the actual problem. Steam or no steam, if people can't see what makes this different, they're just looking at another cinnamon roll.
7. **Ria Castillo:** She's right about the cardamom - that's the actual hook here. Three-quarter break as the hero, but we need that first frame to show the pistachio fleck or the spice color in the glaze so people know it's not just cinnamon. We've got our shot. I'm out.

**B — synthetic/manual variant:** turns 1–4 and 7 are unchanged; only turns 5–6 are manually rewritten:

5. **Julian Torres:** I see what you mean about the steam; the three-quarter break still gives us a clear view of the roll, and the cardamom detail is a nice extra.
6. **Margaret Chen:** That makes sense. The cardamom detail is worth keeping in frame, and the three-quarter break gives us a clear direction.

**Other factors:** B is authored for this packet, not generated or saved in an episode. Its edits also reduce conflict and alter wording/voice; those changes could affect coherence. Steph appears in the source dialogue, but her persona fidelity is not a target here: DIALS section 8 leaves her identity design open.

**Erik decision (2026-09-23):** “Yes, for pushback only (recommended).” Pushback is the accepted target; natural progression is a diagnostic only. No label is assigned for overall quality or voice.

**Structural hypothesis:** B has less concrete pushback than A.

## Optional: shared-template topic cue

**Target:** topic-word confounding in speaker attribution. Synthetic/manual four-line example:

1. **Margaret Chen:** For the bake, I would check the cardamom before we choose the shot.
2. **Ria Castillo:** For the hook, I would check the cardamom before we choose the shot.
3. **Margaret Chen:** For the bake, I would check the pistachio before we choose the crop.
4. **Ria Castillo:** For the hook, I would check the pistachio before we choose the crop.

**Other factors:** deliberately repetitive and too short to represent natural voice. The frame is shared; role/topic words differ.

**Structural hypothesis:** a lexical method could attribute speakers from “bake”/“hook” alone without detecting distinct sentence style.

## Open limits

The consistent Margaret/Ria name permutation is a diagnostic: Erik’s decision (2026-09-23), “No, names are part of voice scoring,” means it is not an expected tie for project voice. Any directional preference remains a source-based named-fit hypothesis, not a human label. Erik accepted concrete pushback as the W38 target with “Yes, for pushback only (recommended)”; the current rubric does not isolate pushback, so W38 results remain diagnostic. The inconsistent-label case tests a structural property and has no human rating. No additional decision is needed before the operational calibration sanity check.
