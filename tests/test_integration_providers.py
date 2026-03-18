import os

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_PROVIDER_TESTS", "").lower() != "true",
    reason="Set RUN_LIVE_PROVIDER_TESTS=true to run live provider checks.",
)


def test_google_genai_text_smoke():
    from backend.utils.model_router import generate_response

    model = os.getenv("GOOGLE_TEST_MODEL", "google/gemini-3.1-pro-preview")
    text = generate_response(prompt="Say hello in one short sentence.", model=model, temperature=0.2)
    assert isinstance(text, str) and text.strip()


def test_nano_banana_image_smoke():
    from backend.utils.image_generation import generate_nano_banana_image

    api_key = os.getenv("NANOBANANA_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("No Nano Banana/Gemini API key set")

    model = os.getenv("NANOBANANA_MODEL", "gemini-2.5-flash-image")
    img = generate_nano_banana_image(
        "A single muffin tin blueberry bite on a plain plate, studio lighting.",
        api_key,
        model=model,
        aspect_ratio="1:1",
    )
    assert isinstance(img, (bytes, bytearray)) and len(img) > 100
