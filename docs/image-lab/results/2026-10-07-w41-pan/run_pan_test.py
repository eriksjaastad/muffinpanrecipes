"""Pan-description blind test for W41 (card #8068).

Arms (3 production shot variants each):
  A  Stability Core, production prompts byte-for-byte (control)
  B  Stability Core, production prompts + pan clause
  C  Gemini 3.1 Flash Image, the same prompts as B

The photographer (Haiku) picks the pan from the library once; B and C share it.
Hard cap: 10 paid image calls + 1 photographer call. --dry-run makes no calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(sys.argv[sys.argv.index("--repo") + 1]) if "--repo" in sys.argv else None
sys.path.insert(0, str(REPO))

from backend.agents.art_director import ArtDirectorAgent  # noqa: E402
from backend.utils.image_generation import generate_nano_banana_image  # noqa: E402

HERE = Path(__file__).parent
LIB = json.loads((HERE / "pan_library.json").read_text())
TITLE = "Black Sesame Popover Cups"
RECIPE_SIZE = "standard"  # the W41 recipe is written for a 12-cup pan
GEMINI_MODEL = "gemini-3.1-flash-image"
MAX_IMAGE_CALLS = 10


def pick_pan(dry_run: bool) -> tuple[str, str]:
    options = [m for m, spec in LIB["materials"].items() if RECIPE_SIZE in spec["sizes"]]
    if dry_run:
        return options[0], "dry run"
    import anthropic

    menu = "\n".join(f"- {m}: {LIB['materials'][m]['description']}" for m in options)
    msg = anthropic.Anthropic().messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                f"You are the food photographer for a muffin-pan recipe site. This week's recipe is "
                f"'{TITLE}', baked in a {LIB['sizes'][RECIPE_SIZE]['label']}. Choose the pan from our "
                f"prop library that will photograph best with this food (think colour contrast with "
                f"the food and a warm rustic look).\n{menu}\n\n"
                'Reply with JSON only: {"material": "<id>", "reason": "<one sentence>"}'
            ),
        }],
    )
    text = msg.content[0].text.strip()
    data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    if data["material"] not in options:
        raise SystemExit(f"photographer picked unknown pan {data['material']!r}")
    return data["material"], data["reason"]


def pan_clause(material: str) -> str:
    size = LIB["sizes"][RECIPE_SIZE]
    return LIB["pan_clause_template"].format(
        size_label=size["label"],
        material=LIB["materials"][material]["description"],
        geometry=size["geometry"],
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    ad = ArtDirectorAgent  # prompt/negative builders only read class constants
    material, reason = pick_pan(args.dry_run)
    clause = pan_clause(material)
    plan = []
    for variant in ad._VARIANTS:
        control = ad._build_prompt(ad, TITLE, variant)
        assert ad._MUFFIN_FORM_CLAUSE in control
        with_pan = control.replace(ad._MUFFIN_FORM_CLAUSE, ad._MUFFIN_FORM_CLAUSE + clause, 1)
        plan += [("A", variant, control), ("B", variant, with_pan), ("C", variant, with_pan)]
    assert len(plan) <= MAX_IMAGE_CALLS

    (out / "plan.json").write_text(json.dumps(
        {"material": material, "reason": reason, "pan_clause": clause,
         "gemini_model": GEMINI_MODEL,
         "calls": [{"arm": a, "variant": v, "prompt": p} for a, v, p in plan]}, indent=2))
    print(f"photographer: {material} ({reason})")
    if args.dry_run:
        print(f"dry run: {len(plan)} image calls planned, none made")
        return

    stability_key = os.environ["STABILITY_API_KEY"]
    google_key = os.environ["GOOGLE_API_KEY"]
    for arm, variant, prompt in plan:
        dest = out / f"{arm}_{variant}.png"
        if dest.exists():
            print(f"skip {dest.name} (exists)")
            continue
        try:
            if arm == "C":
                data = generate_nano_banana_image(prompt, google_key, model=GEMINI_MODEL)
            else:
                data = ad._call_stability(ad, stability_key, prompt, variant=variant)
        except Exception as exc:  # report and continue; no retries
            print(f"FAILED {arm} {variant}: {exc}")
            continue
        dest.write_bytes(data)
        print(f"wrote {dest.name} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
