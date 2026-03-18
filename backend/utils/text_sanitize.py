"""Text sanitization for recipe content before HTML rendering.

Normalizes Unicode smart punctuation to ASCII equivalents and detects
double-encoded (mojibake) text. Applied as a safety net before html.escape()
in the renderer — prevents encoding issues from ever reaching the browser.
"""

from __future__ import annotations

import re

# Unicode smart punctuation → ASCII equivalents
_SMART_PUNCT_MAP = {
    "\u2018": "'",   # left single quote
    "\u2019": "'",   # right single quote (apostrophe)
    "\u201c": '"',   # left double quote
    "\u201d": '"',   # right double quote
    "\u2013": "-",   # en dash
    "\u2014": " - ", # em dash (with spaces for readability)
    "\u2026": "...", # ellipsis
    "\u00a0": " ",   # non-breaking space
    "\u200b": "",    # zero-width space
    "\u00b0": "°",   # degree symbol (keep as-is, it's valid UTF-8)
}

# Mojibake signatures: double-encoded UTF-8 patterns
# These appear when UTF-8 bytes are decoded as Latin-1 then re-encoded as UTF-8
_MOJIBAKE_PATTERNS = [
    (re.compile(r"\xc3\xa2\xc2\x80\xc2\x99"), "'"),      # smart apostrophe
    (re.compile(r"\xc3\xa2\xc2\x80\xc2\x9c"), '"'),      # left smart quote
    (re.compile(r"\xc3\xa2\xc2\x80\xc2\x9d"), '"'),      # right smart quote
    (re.compile(r"\xc3\xa2\xc2\x80\xc2\x93"), "-"),      # en dash
    (re.compile(r"\xc3\xa2\xc2\x80\xc2\x94"), " - "),    # em dash
    (re.compile(r"\xc3\x82\xc2\xb0"), "°"),               # degree symbol
    (re.compile(r"\xc3\xa2\xc2\x80\xc2\xa6"), "..."),     # ellipsis
    (re.compile(r"\xc3\x83\xc2\xa9"), "e"),               # é double-encoded
]


def sanitize_text(text: str) -> str:
    """Normalize smart punctuation and repair mojibake in recipe text.

    Safe to call on any string — returns ASCII-safe punctuation while
    preserving legitimate Unicode like the degree symbol (°) and accented
    characters used in cooking (sauté, crème).
    """
    if not text:
        return text

    # First pass: fix any mojibake (double-encoded UTF-8)
    for pattern, replacement in _MOJIBAKE_PATTERNS:
        text = pattern.sub(replacement, text)

    # Second pass: normalize smart punctuation to ASCII
    for char, replacement in _SMART_PUNCT_MAP.items():
        if char in text:
            text = text.replace(char, replacement)

    return text


def has_encoding_issues(text: str) -> bool:
    """Check if text contains mojibake or suspicious character sequences.

    Used by the editorial QA gate to flag content before publish.
    """
    if not text:
        return False

    # Check for mojibake patterns
    for pattern, _ in _MOJIBAKE_PATTERNS:
        if pattern.search(text):
            return True

    # Check for other suspicious sequences (common double-encoding artifacts)
    suspicious = [
        "\xc3\xa2",  # â from double-encoded UTF-8
        "\xc3\x83",  # Ã from double-encoded UTF-8
        "\xc2\x80",  # control char from double-encoding
    ]
    for seq in suspicious:
        if seq in text:
            return True

    return False
