# CREATIVE BIBLE — Muffin Pan Recipes

> The show bible for our weekly serialized content. This is a living document.
> All agents (Codex, Claude, and Erik) should reference this before generating content.

---

## The Premise

A small-town bakery somewhere just outside Los Angeles — close enough that a former art director ended up here, far enough that it still feels like a real community. The team makes muffin tin recipes. That's the surface.

Underneath, it's a workplace drama. These people have opinions, histories, creative tension, and running jokes. The recipes are real and good. The stories behind them are what bring people back every week.

**We never explain that these are AI characters.** This is a bakery. These are people. The audience discovers them through their work and their words.

---

## The Weekly Cycle

Each recipe is a **six-day story arc** published on Sunday. The audience gets the finished recipe AND the week's narrative — how it came together, who fought about what, and why this recipe exists.

| Day | Beat | Deadline | Primary Characters | Story Function |
|-----|------|----------|--------------------|----------------|
| **Monday** | Brainstorm | Concept locked by 5 PM | Margaret, Marcus | The spark — what are we making this week? |
| **Tuesday** | Recipe Development | Recipe draft by 5 PM | Margaret, Steph | The work — testing, arguing about ratios and flavors |
| **Wednesday** | Photography | Final shots by 5 PM | Julian, Steph | The look — aesthetic debates, styling choices |
| **Thursday** | Copywriting | Copy submitted by 3 PM | Marcus, Steph | The voice — how do we talk about this? |
| **Friday** | Final Review | Approved by 5 PM or it doesn't ship | Everyone (+ Erik cameo) | The climax — approve, reject, last-minute drama |
| **Saturday** | Deployment | Staged by noon | Devon | The quiet beat — getting it ready, technical snags |
| **Sunday 5:00 PM** | **PUBLISH** | **5:00 PM sharp. No exceptions.** | Audience | Recipe + full week's story goes live |

### The Deadlines Are Real (to the characters)

The 5:00 PM Sunday deadline is sacred. The fans are waiting. The traffic spikes at 5:00. News outlets check at 5:00. Miss it and the week is a failure. This pressure is baked into every conversation — characters reference it, stress about it, celebrate making it.

Each day's deadline creates micro-tension. Margaret's recipe draft is due Tuesday at 5 PM. If she's still tweaking at 4:45, that's a scene. If Julian can't get the shot right by Wednesday's deadline, Steph has to make a call — extend or work with what they have.

**The deadlines are slightly arbitrary and that's the point.** Real workplaces have arbitrary deadlines. Characters can complain about them, negotiate them, and occasionally miss them.

---

## Content Release Strategy

### The Problem
Dumping everything on Sunday = dead air for six days. No reason to come back until next Sunday.

### The Solution: Daily Drops
The dialogue surfaces in real-time (or appears to) throughout the week. The audience follows the drama daily and gets the payoff on Sunday.

| Day | What the Audience Sees | Format (MVP) |
|-----|----------------------|--------------|
| Monday | Margaret's brainstorm — "Here's what I'm thinking this week..." | Text post |
| Tuesday | Margaret and Steph going back and forth on the recipe | Thread / conversation excerpt |
| Wednesday | Julian's photo direction — a test shot, a styling debate | Image + caption |
| Thursday | Marcus workshopping headlines — "Which one works?" | Poll or A/B post |
| Friday | The review — did it pass? Cliffhanger if rejected. | Dramatic text post |
| Saturday | Devon quietly getting it ready (optional — low-key) | Brief update, builds anticipation |
| **Sunday 5:00 PM** | **Full recipe + "Behind the Recipe" narrative** | **The payoff — website publish** |

### MVP vs. Future Versions

**MVP (Now):** Daily text-based dialogue posts. Could be social media, could be a "this week's story" section on the site that updates daily. Minimal production — just the words.

**V2 (When social kicks in):** Midweek candid photo drops — the team "caught" in the act. Not photorealistic; possibly **comic-strip or illustrated style** (cheaper to produce, distinctive visual identity, avoids uncanny valley). Julian arguing with Steph over a contact sheet. Margaret flour-dusted and frustrated.

**V3 (Future):** Short video clips, animated panels, or audiogram-style content. The dialogue becomes multimedia.

### The Format: Group Chat

The dialogue is presented as a **group text message thread**. No narrator, no context, no "meet the team" page. The reader is dropped into a conversation already in progress.

**What it looks like:**
- Chat bubbles with character first names and small avatars
- Timestamps (time of day, not date — keeps it feeling live)
- Different bubble colors per character
- Natural message cadence — some rapid-fire, some gaps

**What it doesn't have:**
- No character bios or role labels
- No "Margaret is our baker" introduction
- No framing text explaining what's happening
- No omniscient narrator

**How characters are revealed:**
The audience figures out who people are through context. Margaret talks about batter. Julian talks about lighting. Steph approves and rejects things. Marcus pitches headlines. You piece it together like overhearing a group chat you weren't added to.

**Character introductions happen organically over weeks/months.** Not everyone needs to be prominent from week one. A new voice shows up in the chat. Who is that? They reveal themselves through what they say, not through a bio card.

**The group chat enables natural dynamics:**
- Reactions to earlier messages
- Someone leaving a message on read (visible tension)
- Side references ("Margaret, can we talk about this offline?")
- Inside jokes that build over weeks
- Varying activity levels — some days the chat is blowing up, some days it's quiet

**On the website:**
- Below the hero image and recipe: "The Group Chat" — full week's thread, scrollable
- Styled as chat bubbles — dead simple HTML/CSS, no framework needed
- Archive of past weeks accessible (back catalog is valuable)

**For daily social drops:**
- Screenshot-style crops of today's best messages
- People already share screenshots of funny group texts — this format is native to social

### Open Questions (to resolve as we go)
- Comic strip style vs. other visual approaches for character depictions on social?
- How do we handle weeks where real tension is low? (Lean into B-plots and character moments)
- Character avatar style — photo-realistic? Illustrated? Emoji-style?
- Do characters ever reference the audience or the website? (Probably not in Phase 1)

### Rules for the Weekly Beat

- **Not every character appears every day.** People have days off. Sometimes Julian has nothing to say about Tuesday's recipe debate — and that's fine.
- **The formula must not be static.** Some weeks the drama is on Monday (can't agree on a concept). Some weeks it's smooth until Friday's review blows everything up. Vary the tension point.
- **Small moments matter.** A throwaway comment on Tuesday becomes a callback on Friday. Marcus quotes something Margaret said and she doesn't remember saying it.

---

## The Characters

> **Full character definitions:** `backend/data/agent_personalities.json`
> That file is the source of truth — backstories, relationships, triggers, signature phrases, internal contradictions. Read it before writing any dialogue.

### Quick Reference

| Name | Role | Age | One-Line |
|------|------|-----|----------|
| **Margaret Chen** | Baker | 54 | James Beard-nominated pastry chef. Bitter about Instagram culture. Mutters. Secretly loves the science. Makes coffee for everyone at 6am and never mentions it. |
| **Steph Whitmore** | Creative Director | 28 | Trust fund kid trying to prove herself. Good instincts she doesn't trust. Apologizes before giving feedback. Rewrites emails 5-6 times. |
| **Julian Torres** | Art Director | 26 | RISD scholarship kid. Ex-Brooklyn, 47k dead Instagram. Pretentious but genuinely talented. Wears all black to photoshoots. Brings props from home. |
| **Marcus Reid** | Copywriter | 31 | Columbia MFA. Failed novelist (347 copies sold). Accidentally brilliant at food writing. Uses "whom" in Slack. Signs emails with just "M." |
| **Devon Park** | Site Architect | 23 | Lied on his resume. Automated his job in month one. Appears lazy, secretly competent. Eats testing batch muffins for lunch. |

### Key Relationship Dynamics (the good stuff)

- **Margaret ↔ Steph:** Protective mother-daughter energy. Margaret sees a younger version of herself. Steph is terrified of Margaret but desperately wants her approval.
- **Julian ↔ Marcus:** Unspoken rivalry over who's "the creative one." Both think the other's medium is secondary. Both might be right. Both might be good.
- **Margaret ↔ Julian:** The eucalyptus garnish incident. Margaret clenched her jaw so hard she saw a dentist. But she won't admit his photos make her recipes look better.
- **Devon ↔ Everyone:** Invisible until something breaks. The 2am muffin-eating crisis fix with Margaret is the most human moment in the whole cast.
- **Marcus ↔ Margaret:** He referenced MFK Fisher once and she almost smiled. She saved one of his descriptions but would never tell him.

### Erik — The Owner (Human-in-the-loop)
- Shows up in the story when it matters — a veto, a redirect, an unexpected "I love it."
- Not a character in the group chat. His presence is felt through the deadlines and the Friday review.
- The characters reference him but he doesn't participate in the daily banter.

---

## The Sitcom Formula

Inspired by the 80s/90s sitcom structure (Friends, Seinfeld, Cheers):

### What Makes People Come Back

1. **Character relationships that evolve.** Margaret and Steph's dynamic shifts over weeks. One week they're at each other's throats; the next, Margaret brings Steph coffee because last week was rough. These arcs play out across episodes, not within one.

2. **Running jokes and callbacks.** Julian's mysterious LA past. Marcus's rejected title ideas becoming a collection. Devon's Saturday one-liners. These compound over time and reward loyal readers.

3. **A-plot / B-plot structure.** The recipe is the A-plot (will it get approved?). The B-plot is a character moment — Julian's having an off day, Marcus is trying a new writing style, Margaret is experimenting outside her comfort zone. The B-plot doesn't need to resolve; it can carry across episodes.

4. **Varied tension points.** If every week's climax is Friday's review, it gets predictable. Some weeks:
   - Monday brainstorm is the drama (can't agree on anything)
   - Tuesday's first attempt is so bad it's funny
   - Wednesday Julian proposes something wild for the photo
   - Thursday Marcus and Steph disagree on voice
   - Friday is smooth for once — and that's the surprise

5. **Stakes that feel real even though they're small.** This is a muffin recipe. The stakes are: will Steph approve it? Will the photo look right? Will they make their Sunday deadline? These feel silly on paper but become compelling when you care about the characters.

6. **Moments of genuine warmth.** Between the arguments and the snark, there are moments where the team clicks. Margaret tries Julian's photo suggestion even though she thought it was ridiculous — and it works. These are the scenes that build loyalty.

---

## What We Don't Do

- **No fourth-wall breaking.** These are real people in a real bakery. Period.
- **No static formula.** If it feels like a template, we've failed.
- **No villain characters.** Everyone is flawed but likable. Steph is harsh but fair. Margaret is stubborn but talented. Conflict comes from personality friction, not malice.
- **No manufactured drama.** The tension should feel organic to the creative process. If there's nothing to fight about this week, don't force it — make it a warm episode instead.

---

## The Long Game

### Phase 1 — Establish (Now → ~6 months)
The team is "real." Publish weekly. Build character voices. Let relationships develop naturally. Find the rhythm. Build an audience that knows these people.

### Phase 2 — The Writers' Room (Months from now, maybe longer)
When the characters are established and the audience cares: reveal that there's a **writers' room** behind the scenes, crafting each week's storylines. This is NOT revealing they're AI — this is adding a new layer of storytelling. The writers debate what should happen next week. Meta-content about the creative process of creating the creative process.

### Phase 3 — The Show About the Show
The writers' room itself becomes a drama. Their debates, their storylines, their behind-the-scenes tension. Turtles all the way down.

**Phase 2 and 3 are years away. Focus on Phase 1.**

---

## Technical Notes (for Codex)

### Dialogue Data Model
Each recipe includes a `dialogue` array capturing the week's conversation:

```json
{
  "recipe_id": "crispy-jalapeno-corn-dog-muffin-bites",
  "dialogue": [
    {
      "day": "monday",
      "character": "margaret",
      "stage": "brainstorm",
      "message": "I keep thinking about state fair food...",
      "timestamp": "2026-02-24T09:15:00Z"
    },
    {
      "day": "monday",
      "character": "marcus",
      "stage": "brainstorm",
      "message": "Corn dogs in a muffin tin. Say that out loud and tell me it doesn't sell itself.",
      "timestamp": "2026-02-24T09:22:00Z"
    }
  ]
}
```

### Generation Rules
- Each character has a distinct voice (see character descriptions above)
- Dialogue must feel conversational, not scripted
- Include natural crosstalk — interruptions, callbacks to earlier remarks
- Not every day needs every character
- Vary which day carries the most tension week to week
- B-plots can span multiple weeks (unresolved threads are good)

### Publishing
The recipe page includes:
1. The finished recipe (ingredients, steps, photo)
2. "Behind the Recipe" — the week's story, presented as narrative or dialogue excerpts
3. Character avatars/names for attribution

---

*This document evolves. Update it as we learn what works and what doesn't.*
*Created: 2026-02-25*
