# W41 pan test (2026-10-07, card #8068)

Erik: "the hero image looks great but the three from wednesday look bad". The food read
fine; the pan was wrong in all three live shots (mesh discs instead of wells, 30+ uneven
wells with liners, a 20-well riveted grid). The production image prompt describes the
food and never the pan.

Recipe: Black Sesame Popover Cups (W41), standard 12-cup pan. Pan library:
`docs/image-lab/pan_library.json` (panV1-draft). The photographer (claude-haiku-4-5)
picked `dark_nonstick`. B and C share that pan clause byte for byte; `plan.json` has
every prompt sent.

| Arm | Model | Prompt | Pan result |
|---|---|---|---|
| A | Stability Core | production, unchanged | 16 wells in the overhead shot (asked 12), uneven well sizes in the hero shot |
| B | Stability Core | A + pan clause | no better: merged wells in the overhead shot, liners, tart shells |
| C | Gemini 3.1 Flash Image | same as B | rigid, real-looking pan in all three; overhead has 16 wells (asked 12); macro and hero followed their composition briefs (single item; 2-3 on a board, one broken open) |
| D/E | Gemini 3.1 Flash Image, edit | live hero + "replace only the pan", then "remove the mesh discs" | the popovers Erik liked are kept and sit in a real pan |

Findings, one run, so treat them as direction rather than proof:

- Describing the pan in text does not fix Stability Core. The model is the limit.
- Gemini follows the pan description and the shot briefs. The shot brief result matters
  for #6964's "same three shots" problem, where macro and hero rendered alike on Stability.
- Exact well count is still unreliable (16 for 12 in C's overhead shot).
- C rendered dense muffins, not popovers; A rendered tart shells. Neither looks like a popover.
- The photographer's reason for picking dark nonstick ("contrast with dark popovers") is
  weak. Charcoal on black is low contrast.

Cost: 6 Stability Core images at $0.03, 5 Gemini calls at about $0.067 (third-party
price; Google's page was not reachable), 1 Haiku call. About $0.55 total.

The images (about 4MB each) are not committed. The contact sheet is attached to card #8068 notes by path only.

`run_pan_test.py` is copied from the session scratchpad for the record. It expects
`pan_library.json` beside it.
