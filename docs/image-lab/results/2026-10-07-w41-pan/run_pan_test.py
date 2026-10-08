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

# Stability Core as production called it until #8068 removed it, kept here so
# arms A and B stay reproducible.
_STABILITY_BASE_NEGATIVE = (
    "people, hands, text, watermark, clutter, stacked food, piled food, food on top of food, "
    "flat high-key white studio lighting"
)
_STABILITY_VARIANT_NEGATIVES = {
    "macro_closeup": "full tin visible, bird's eye view, overhead angle, multiple items, wide shot",
    "overhead_flatlay": "shallow depth of field, bokeh, single item, macro, close-up, low angle",
    "hero_threequarter": "extreme close-up, overhead, bird's eye, flat lay, 90 degree angle, macro",
}


def call_stability(api_key: str, prompt: str, variant: str) -> bytes:
    import requests

    response = requests.post(
        "https://api.stability.ai/v2beta/stable-image/generate/core",
        headers={"authorization": f"Bearer {api_key}", "accept": "image/*"},
        data={
            "prompt": prompt,
            "negative_prompt": f"{_STABILITY_BASE_NEGATIVE}, {_STABILITY_VARIANT_NEGATIVES[variant]}",
            "output_format": "png",
            "aspect_ratio": "1:1",
        },
        files={"none": "none"},
        timeout=90,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Stability API error {response.status_code}: {response.text[:200]}")
    return response.content
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
    # One run per directory: a resumed run could pick a different pan and
    # leave plan.json disagreeing with images already on disk.
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty; give each run a fresh output directory")
    out.mkdir(parents=True, exist_ok=True)
    if not args.dry_run:
        # Check every key before the paid photographer call and the plan write.
        missing = [k for k in ("STABILITY_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY") if not os.environ.get(k)]
        if missing:
            raise SystemExit(f"missing env: {', '.join(missing)}")

    ad = ArtDirectorAgent  # the prompt builder only reads class constants
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
    failures = []
    for arm, variant, prompt in plan:
        stem = out / f"{arm}_{variant}"
        try:
            if arm == "C":
                data = generate_nano_banana_image(prompt, google_key, model=GEMINI_MODEL, timeout_s=90)
            else:
                data = call_stability(stability_key, prompt, variant)
        except Exception as exc:  # attempt every arm, no retries, then exit nonzero
            print(f"FAILED {arm} {variant}: {exc}")
            failures.append(f"{arm} {variant}")
            continue
        if not data:
            print(f"FAILED {arm} {variant}: empty image body")
            failures.append(f"{arm} {variant}")
            continue
        # Gemini answers with JPEG and Stability with PNG; name the file by its bytes.
        dest = stem.with_suffix(".png" if data.startswith(b"\x89PNG\r\n\x1a\n") else ".jpg")
        dest.write_bytes(data)
        print(f"wrote {dest.name} ({len(data)} bytes)")
    if failures:
        raise SystemExit(f"incomplete comparison, {len(failures)} arm image(s) failed: {', '.join(failures)}")

if __name__ == "__main__":
    main()
