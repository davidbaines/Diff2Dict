"""Split a line into word and punctuation tokens.

Named tokens.py, not tokenize.py, so it cannot shadow the stdlib module.
"""

from .unicodetools import BOTH, PUNCT, WORD, graphemes, is_space, suggest_class


def tokenize(line: str, classes: dict[str, str]) -> tuple[list[str], list[str]]:
    """Return (words, punctuation) for one line.

    Whitespace always separates. A 'both' character is word-forming only when
    the graphemes on each side of it are word-forming, otherwise punctuation.
    Graphemes missing from `classes` fall back to the suggested class.
    """
    gs = graphemes(line)
    base = [None if is_space(g) else classes.get(g) or suggest_class(g) for g in gs]
    words: list[str] = []
    puncts: list[str] = []
    current: list[str] = []
    for i, g in enumerate(gs):
        cls = base[i]
        if cls == BOTH:
            before = base[i - 1] if i > 0 else None
            after = base[i + 1] if i + 1 < len(gs) else None
            cls = WORD if before == WORD and after == WORD else PUNCT
        if cls == WORD:
            current.append(g)
            continue
        if current:
            words.append("".join(current))
            current = []
        if cls == PUNCT:
            puncts.append(g)
    if current:
        words.append("".join(current))
    return words, puncts
