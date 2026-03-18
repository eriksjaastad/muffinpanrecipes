"""Mutable compression templates for autoresearch-style optimization.

THIS FILE IS THE EXPERIMENT VARIABLE. The compression research loop modifies it,
compresses frozen conversations, generates Sunday dialogue with the compressed
context, scores the result, and keeps or discards changes.

The compress_day() function is called once per day (Mon-Sat). Its output is
accumulated into a week_highlights list and injected into Sunday's generation.

To reset to baseline: git checkout -- prompt-research/compression_prompts.py
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Compression system prompt — controls how the LLM summarizes a day
# ---------------------------------------------------------------------------

COMPRESSION_SYSTEM_PROMPT = (
    "You write concise meeting summaries with attribution. "
    "Use first names only. Focus on decisions and disagreements. "
    "No technical jargon about file formats, pixels, or deployment."
)


# ---------------------------------------------------------------------------
# Compression template — the prompt sent to the LLM for each day
# ---------------------------------------------------------------------------

COMPRESSION_TEMPLATE = (
    "Summarize this {day} conversation about '{concept}' in 2-3 sentences.\n\n"
    "Transcript:\n{transcript}\n\n"
    "Rules:\n"
    "- Include WHO said or decided what (use first names)\n"
    "- Focus on decisions, disagreements, and unresolved questions\n"
    "- Name specific ingredients, techniques, or ideas discussed\n"
    "- If something was left unresolved, end with: 'Still open: [question]'\n"
    "- Under 60 words total\n"
    "- Use plain hyphens and straight quotes only"
)


# ---------------------------------------------------------------------------
# Injection template — how the compressed highlights appear in Sunday's prompt
# ---------------------------------------------------------------------------

INJECTION_TEMPLATE = (
    "WHAT HAPPENED EARLIER THIS WEEK:\n"
    "{highlights}\n\n"
    "Use this history to make Sunday's conversation feel like a genuine continuation. "
    "Characters should naturally reference specific people, decisions, and unresolved tensions from earlier days. "
    "Callbacks should feel organic - woven into the conversation, not announced. "
    "Unresolved disagreements from earlier days are especially worth revisiting or resolving today.\n\n"
    "OPEN THREADS TO REVISIT:\n"
    "{open_threads}"
)


# ---------------------------------------------------------------------------
# Config — controls compression behavior
# ---------------------------------------------------------------------------

COMPRESSION_CONFIG: dict[str, Any] = {
    "temperature": 0.3,
    "max_words_per_day": 60,
}


# ---------------------------------------------------------------------------
# Main compression function — called by the runner for each day
# ---------------------------------------------------------------------------

def compress_day(
    day: str,
    messages: list[dict],
    concept: str,
    model: str,
    generate_fn: Any = None,
) -> str:
    """Compress a day's conversation into a summary string.

    Args:
        day: Day name (e.g. "monday")
        messages: List of message dicts from that day
        concept: Recipe concept being discussed
        model: LLM model to use for compression
        generate_fn: The generate_response function (injected to avoid import issues)

    Returns:
        A summary string with attribution, or empty string on failure.
    """
    if not messages or generate_fn is None:
        return ""

    # Build transcript
    transcript_lines = []
    for m in messages:
        first_name = m['character'].split()[0]
        clean_msg = ' '.join(m['message'].split())
        transcript_lines.append(f"{first_name}: {clean_msg}")

    transcript = "\n".join(transcript_lines)

    # Find the single most contentious or specific exchange in the transcript
    # Look for disagreement pairs or highly specific messages
    best_quote = None
    best_score = 0
    for m in messages:
        msg = m['message']
        score = 0
        # Score for specificity signals
        if any(char.isdigit() for char in msg):
            score += 2
        if any(word in msg.lower() for word in ['because', 'think', 'should', 'wrong', 'right', 'better', 'worse', 'prefer', 'love', 'hate', 'never', 'always']):
            score += 2
        if '?' in msg:
            score += 1
        if len(msg.split()) > 15:
            score += 1
        if score > best_score:
            best_score = score
            first_name = m['character'].split()[0]
            # Truncate to ~15 words for a clean quote fragment
            words = msg.split()
            quote_words = words[:15]
            best_quote = f'{first_name}: "{" ".join(quote_words)}{"..." if len(words) > 15 else ""}"'

    prompt = COMPRESSION_TEMPLATE.format(
        day=day.title(),
        concept=concept,
        transcript=transcript,
    )

    # Append verbatim quote instruction if we found a good one
    if best_quote and best_score >= 3:
        prompt += f"\n\nKey quote to include verbatim: {best_quote}"

    try:
        summary = generate_fn(
            prompt=prompt,
            system_prompt=COMPRESSION_SYSTEM_PROMPT,
            model=model,
            temperature=COMPRESSION_CONFIG["temperature"],
        ).strip()

        # Sanitize typographic tells
        for old, new in [
            ("\u2014", " - "), ("\u2013", " - "),
            ("\u2019", "'"), ("\u201c", '"'), ("\u201d", '"'),
        ]:
            summary = summary.replace(old, new)

        return summary
    except Exception:
        # Fallback: extract last 2 messages as bare-bones context
        fallback_msgs = messages[-2:] if len(messages) >= 2 else messages
        return "; ".join(
            f"{m['character'].split()[0]} said: {' '.join(m['message'].split())[:80]}"
            for m in fallback_msgs
        )


def format_highlights(highlights: list[str]) -> str:
    """Format accumulated highlights for injection into Sunday's prompt.

    Args:
        highlights: List of "DayName: summary..." strings

    Returns:
        Formatted block ready for prompt injection.
    """
    if not highlights:
        return ""

    # Format highlights as a clean numbered chronological list
    formatted_lines = []
    for i, h in enumerate(highlights, 1):
        formatted_lines.append(f"{i}. {h}")

    highlights_text = "\n".join(formatted_lines)

    # Extract open threads: look for "Still open:" markers first (from template),
    # then fall back to general unresolved markers
    open_thread_lines = []
    unresolved_markers = [
        "still open", "unresolved", "question", "?", "disagree", "debate",
        "uncertain", "undecided", "pushed back", "wasn't settled",
        "no consensus", "still unclear", "open question"
    ]

    for h in highlights:
        h_lower = h.lower()
        # Check for explicit "Still open:" marker from template
        if "still open:" in h_lower:
            # Extract just the "Still open: ..." portion
            idx = h_lower.index("still open:")
            still_open_text = h[idx:].strip()
            parts = h.split(":", 1)
            day_label = parts[0].strip() if len(parts) == 2 else ""
            open_thread_lines.append(f"- {day_label}: {still_open_text}")
        elif any(marker in h_lower for marker in unresolved_markers[1:]):
            parts = h.split(":", 1)
            if len(parts) == 2:
                day_label = parts[0].strip()
                detail = parts[1].strip()
                # Keep it brief - first sentence only
                first_sentence = detail.split(".")[0].strip()
                if first_sentence:
                    open_thread_lines.append(f"- {day_label}: {first_sentence}")

    # Build a "where we left off" anchor from the most recent day
    recent_anchor = None
    if highlights:
        last_highlight = highlights[-1]
        parts = last_highlight.split(":", 1)
        if len(parts) == 2:
            day_label = parts[0].strip()
            detail = parts[1].strip()
            recent_anchor = f"- Where we left off ({day_label}): {detail}"

    if open_thread_lines:
        open_threads_text = "\n".join(open_thread_lines)
        if recent_anchor:
            open_threads_text = recent_anchor + "\n" + open_threads_text
    else:
        open_threads_text = (
            recent_anchor + "\n"
            "- Sunday is the moment to resolve any lingering questions and finalize the recipe direction."
        ) if recent_anchor else "- No major unresolved disagreements - Sunday can focus on final decisions and reflection."

    # Build a character-centric summary: who championed what across the week
    # Track named characters and their key positions from the summaries
    character_positions: dict[str, list[str]] = {}
    for h in highlights:
        parts = h.split(":", 1)
        if len(parts) < 2:
            continue
        detail = parts[1].strip()
        # Split into sentences and look for name-anchored claims
        sentences = [s.strip() for s in detail.replace("Still open", "||Still open").split(".") if s.strip()]
        for sentence in sentences:
            if "||" in sentence:
                continue  # skip "Still open" fragments
            words = sentence.split()
            for i, word in enumerate(words):
                clean_word = word.rstrip(".,;:")
                if not clean_word or not clean_word[0].isupper() or len(clean_word) <= 2 or not clean_word.isalpha():
                    continue
                # Skip common non-name capitalized words
                skip_words = {
                    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
                    "Still", "The", "This", "That", "It", "We", "They", "He", "She",
                    "I", "A", "An", "In", "On", "At", "By", "For", "With", "From",
                    "No", "Yes", "But", "And", "Or", "So", "If", "After", "Before",
                }
                if clean_word in skip_words:
                    continue
                # The name appears to anchor the sentence - grab surrounding context
                end = min(len(words), i + 8)
                snippet = " ".join(words[i:end]).rstrip(".,;:")
                if clean_word not in character_positions:
                    character_positions[clean_word] = []
                if len(character_positions[clean_word]) < 2 and len(snippet.split()) >= 3:
                    character_positions[clean_word].append(snippet)

    char_lines = []
    for name, snippets in character_positions.items():
        if snippets:
            char_lines.append(f"- {name}: {snippets[0]}")

    # Build a concise "story so far" narrative from all highlights for easier model consumption
    story_parts = []
    for h in highlights:
        parts = h.split(":", 1)
        if len(parts) == 2:
            day_label = parts[0].strip()
            detail = parts[1].strip()
            # Get first two sentences as the story beat
            sentences = [s.strip() for s in detail.split(".") if s.strip()]
            beat = ". ".join(sentences[:2])
            if beat:
                story_parts.append(f"{day_label}: {beat}.")

    story_narrative = ""
    if story_parts:
        story_narrative = "\n\nSTORY SO FAR (for natural callbacks):\n" + "\n".join(story_parts)

    if char_lines:
        char_block = "\n\nCHARACTER POSITIONS THIS WEEK:\n" + "\n".join(char_lines[:6])
        return INJECTION_TEMPLATE.format(
            highlights=highlights_text,
            open_threads=open_threads_text,
        ) + story_narrative + char_block

    return INJECTION_TEMPLATE.format(
        highlights=highlights_text,
        open_threads=open_threads_text,
    ) + story_narrative