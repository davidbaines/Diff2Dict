"""Unicode helpers: normalisation, grapheme splitting and character classes."""

import csv
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import regex
import unicodedataplus as udp

WORD = "word"
PUNCT = "punctuation"
BOTH = "both"
CLASSES = (WORD, PUNCT, BOTH)

# Characters that are often apostrophes or hyphens inside words but punctuation
# elsewhere. U+02BC MODIFIER LETTER APOSTROPHE is a letter (Lm), so it is not here.
BOTH_SUGGESTED = frozenset({"'", "’", "-", "‐", "‑"})

CHARS_HEADER = ["char", "codepoints", "unicode_name", "category", "count",
                "suggested_class", "class"]

_GRAPHEME = regex.compile(r"\X")
_MARKER = regex.compile(r"^<[^<>]+>$")


def nfc(text: str) -> str:
    return udp.normalize("NFC", text)


def graphemes(text: str) -> list[str]:
    return _GRAPHEME.findall(text)


def lower_graphemes(word: str) -> tuple[str, ...]:
    """Lower-case a word and split it into graphemes."""
    return tuple(graphemes(nfc(word.lower())))


def is_space(g: str) -> bool:
    return g.isspace() or udp.category(g[0]).startswith("Z")


def is_marker(line: str) -> bool:
    """True for vref placeholder lines such as <range>."""
    return bool(_MARKER.match(line.strip()))


def category(g: str) -> str:
    return udp.category(g[0])


def codepoints(g: str) -> str:
    return " ".join(f"U+{ord(c):04X}" for c in g)


def parse_codepoints(text: str) -> str:
    return "".join(chr(int(cp.strip()[2:], 16)) for cp in text.split())


def char_name(g: str) -> str:
    return " + ".join(udp.name(c, f"U+{ord(c):04X}") for c in g)


def is_mark(c: str) -> bool:
    return udp.category(c).startswith("M")


def suggest_class(g: str) -> str:
    if g in BOTH_SUGGESTED:
        return BOTH
    # Letters, marks and digits form words; everything else is punctuation.
    return WORD if category(g)[0] in "LMN" else PUNCT


def read_lines(path: str | Path) -> list[str]:
    """Read a text file as NFC lines. Only LF (or CRLF) ends a line, so line N
    stays verse N even when the text contains other Unicode line separators."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        text = f.read()
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [nfc(line.removesuffix("\r")) for line in lines]


def count_graphemes(lines: Iterable[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for line in lines:
        counts.update(g for g in graphemes(line) if not is_space(g))
    return counts


def read_chars_csv(path: str | Path) -> dict[str, str]:
    """Return grapheme -> class. The codepoints column is the key, because a
    spreadsheet may mangle the char column (for example a leading apostrophe)."""
    classes: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            cps = (row.get("codepoints") or "").strip()
            if not cps:
                continue
            value = (row.get("class") or "").strip().lower()
            cls = next((c for c in CLASSES if value and c.startswith(value)), None)
            if cls is None:
                raise ValueError(f"{path}, line {n}: class must be one of "
                                 f"{', '.join(CLASSES)}, not {value!r}")
            classes[parse_codepoints(cps)] = cls
    return classes


def write_chars_csv(path: str | Path, counts: Counter[str],
                    existing: dict[str, str] | None = None) -> None:
    """Write the editable character table. Classes already chosen in
    `existing` are kept, so re-running scan-chars never loses user edits."""
    existing = existing or {}
    rows = sorted(counts, key=lambda g: (category(g)[0] not in "LMN", category(g), g))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(CHARS_HEADER)
        for g in rows:
            suggested = suggest_class(g)
            w.writerow([g, codepoints(g), char_name(g), category(g), counts[g],
                        suggested, existing.get(g, suggested)])
