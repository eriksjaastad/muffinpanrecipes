"""Every colour class an admin template uses has a rule in the compiled CSS (#8604).

backend/admin/static/tailwind.css was built once on 2026-03-02 and never
again, so colours added to templates later had no rule and buttons rendered
white on light grey. Rebuild with `npm run build:css` after editing a template.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# A variant is anything up to a colon outside [...]; what is left is the utility.
VARIANTS = re.compile(r"^(?:(?:[^:\[\]]|\[[^\]]*\])+:)*")
COLOUR = re.compile(r"^-?(?:bg|text|border|ring)-")
# A CSS class selector: identifier characters, or a hex or single-character escape.
SELECTOR = re.compile(r"\.((?:\\[0-9a-fA-F]{1,6} ?|\\.|[A-Za-z0-9_-])+)")
ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6}) ?|\\(.)")


def _css_classes(css: str) -> set[str]:
    """Every class name the stylesheet defines a rule for, with CSS escapes undone."""
    unescape = lambda m: chr(int(m.group(1), 16)) if m.group(1) else m.group(2)  # noqa: E731
    return {ESCAPE.sub(unescape, name) for name in SELECTOR.findall(css)}


def _class_names(attr: str) -> list[str]:
    """Class names in a class attribute: plain text, branches of {% if %}, and
    quoted literals inside {{ }} (Tailwind compiles all of them)."""
    literals = [name for expr in re.findall(r"\{\{(.*?)\}\}", attr, re.S)
                for quoted in re.findall(r"'([^']*)'", expr) for name in quoted.split()]
    return literals + re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", attr, flags=re.S).split()


def test_class_names_include_template_branches():
    attr = "mt-1 {% if ok %}text-emerald-700{% else %}text-red-600{% endif %} {{ 'border-emerald-500' if c else 'border-slate-200' }}"
    assert sorted(_class_names(attr)) == sorted(
        ["mt-1", "text-emerald-700", "text-red-600", "border-emerald-500", "border-slate-200"])


def test_css_class_parsing_handles_tailwind_escapes():
    css = r".bg-green-500{}.hover\:bg-slate-300:hover{}.\32 xl\:bg-slate-800{}.bg-\[\#fff\]{}"
    assert _css_classes(css) == {"bg-green-500", "hover:bg-slate-300", "2xl:bg-slate-800", "bg-[#fff]"}
    assert "bg-green-50" not in _css_classes(css)


def test_every_template_colour_class_has_a_css_rule():
    defined = _css_classes((ROOT / "backend/admin/static/tailwind.css").read_text())
    missing = {
        f"{template.name}: {name}"
        for template in (ROOT / "backend/admin/templates").glob("*.html")
        for attr in re.findall(r'class="([^"]*)"', template.read_text())
        for name in _class_names(attr)
        if COLOUR.match(VARIANTS.sub("", name)) and name not in defined
    }
    assert not missing, f"run `npm run build:css`; no CSS rule for {sorted(missing)}"
