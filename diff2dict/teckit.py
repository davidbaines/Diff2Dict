"""TECkit map generation, a simulator of how TECkit applies it, and optional
evaluation with the real teckit_compile and txtconv tools.

Syntax follows "The TECkit Language" (Jonathan Kew, SIL, rev 21, 2021) and
the compiler source (silnrsi/teckit, source/Compiler.cpp):
  * `#` in a context means start or end of the text, not a word boundary, so
    a word boundary is written with `^[W]` (any character that is not
    word-forming; a negated item also matches the end-of-text pseudo-character).
    When 'both' characters exist, the boundary also allows one of them
    followed (or preceded) by a non-word character, as the tokeniser does.
  * TECkit sorts rules by the maximum length of the match, in codepoints,
    then by the maximum length of the contexts; equal rules go in file order
    (Compiler::sortRules). The simulator uses the same key.
  * A rule line cannot begin with the operator, so the match side is never
    empty. Insertions are therefore folded into a neighbouring character. The
    replacement side may be empty, which is how an unfoldable deletion is
    written, but only one way.
"""

import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .align import BOUNDARY, Word
from .unicodetools import BOTH, graphemes, nfc

WORDCHAR = "@"   # context item: a word-forming character
NONWORD = "~"    # context item: not a word-forming character (or end of text)
NOTUPPER = "!"   # context item: not a capital or a combining mark
OPERATORS = {"both": "<>", "fwd": ">", "bwd": "<"}


@dataclass(frozen=True)
class MapRule:
    kind: str                  # 'word', 'punct' or 'char'
    lhs: Word
    rhs: Word
    pre: str | None            # context on the LHS side
    post: str | None
    rpre: str | None           # context on the RHS side
    rpost: str | None
    direction: str             # 'both', 'fwd' or 'bwd'
    count: int
    p_fwd: float
    p_bwd: float
    text: str                  # readable form of the mined rule
    note: str = ""
    casings: tuple[tuple[Word, Word], ...] = ()


@dataclass(frozen=True)
class Layout:
    """Which optional classes the map defines. They change how contexts are
    written and so how long TECkit counts them."""
    guard: bool = False   # combining marks exist: [M], and ^[M] after char rules
    both: bool = False    # 'both' characters exist: [B] and [WB]
    upper: bool = False   # capitals exist: [U]


def make_layout(word_chars: set[str], marks: set[str], both_chars: set[str]) -> Layout:
    return Layout(bool(marks), bool(both_chars), any(_is_upper(c) for c in word_chars))


def _is_upper(c: str) -> bool:
    return c.isupper() or c.istitle()


def _case_forms(c: str, fold_case: bool) -> list[str]:
    return [c, c.upper()] if fold_case and c.upper() != c else [c]


def _guarded(kind: str, post: str | None, layout: Layout) -> bool:
    return layout.guard and kind == "char" and post not in (BOUNDARY, WORDCHAR, NONWORD,
                                                            NOTUPPER)


def _item_len(c: str | None, fold_case: bool, layout: Layout) -> int:
    if c is None:
        return 0
    if c == BOUNDARY:
        return 2 if layout.both else 1
    if c in (WORDCHAR, NONWORD, NOTUPPER):
        return 1
    return max(len(v) for v in _case_forms(c, fold_case))


def sort_key(r: MapRule, forward: bool, layout: Layout) -> tuple[int, int]:
    """TECkit's precedence: match length first, then context length."""
    match, pre, post = ((r.lhs, r.pre, r.post) if forward else (r.rhs, r.rpre, r.rpost))
    fold = r.kind != "punct"
    ctx = (_item_len(pre, fold, layout) + _item_len(post, fold, layout)
           + _guarded(r.kind, post, layout))
    return sum(len(g) for g in match), ctx


def _rule(kind, lhs, rhs, pre, post, direction, count, p_fwd, p_bwd, text,
          note="", casings=(), rpre=..., rpost=...) -> MapRule:
    return MapRule(kind, lhs, rhs, pre, post, pre if rpre is ... else rpre,
                   post if rpost is ... else rpost, direction, count, p_fwd, p_bwd,
                   text, note, casings)


# ---------------------------------------------------------------- building rules

def _guard_both(seq: Word, pre: str | None, post: str | None, both: set[str]
                ) -> tuple[str | None, str | None] | None:
    """Rules were mined inside words, where a 'both' character sits between two
    word-forming characters. When one ends the match, require a word-forming
    character beyond it, so the rule leaves the same character alone where it is
    punctuation. Returns None when a context is itself a 'both' character,
    because one grapheme of context cannot then say what lies beyond it."""
    if pre in both or post in both:
        return None
    if seq and seq[0] in both and pre is None:
        pre = WORDCHAR
    if seq and seq[-1] in both and post is None:
        post = WORDCHAR
    return pre, post


def char_map_rules(accepted, both: set[str] = frozenset()
                   ) -> tuple[list[MapRule], list[tuple[str, str]]]:
    """Turn accepted CharRules into MapRules, folding empty sides into a
    neighbouring grapheme. Returns the rules and those that cannot be written."""
    rules: list[MapRule] = []
    unmapped: list[tuple[str, str]] = []
    for r in accepted:
        x, y, left, right = r.key
        direction, note = r.direction, ""
        if x and y:
            lhs, rhs, pre, post = x, y, left, right
        elif left not in (None, BOUNDARY):
            lhs, rhs, pre, post = (left,) + x, (left,) + y, None, right
        elif right not in (None, BOUNDARY):
            lhs, rhs, pre, post = x + (right,), y + (right,), left, None
        elif x:
            if direction == "bwd":
                unmapped.append((r.text(), "the reverse is an insertion with no neighbouring "
                                           "character, which TECkit cannot express"))
                continue
            lhs, rhs, pre, post = x, (), left, right
            if direction == "both":
                direction = "fwd"
                note = "reverse would be an insertion with no neighbouring character"
        else:
            unmapped.append((r.text(), "an insertion with no neighbouring character cannot "
                                       "be expressed in TECkit"))
            continue
        lctx = _guard_both(lhs, pre, post, both)
        rctx = _guard_both(rhs, pre, post, both)
        if lctx is None or rctx is None:
            unmapped.append((r.text(), "its context is a 'both' character, so TECkit could "
                                       "not tell a word-internal use from punctuation"))
            continue
        rules.append(_rule("char", lhs, rhs, *lctx, direction, r.count, r.p_fwd,
                           r.p_bwd, r.text(), note, rpre=rctx[0], rpost=rctx[1]))
    # More specific rules first, so where TECkit ties (as the Title variants
    # of a rule with and without context can), file order favours them.
    rules.sort(key=lambda m: (-sum(len(g) for g in m.lhs),
                              -((m.pre is not None) + (m.post is not None)), -m.count, m.text))
    return rules, unmapped


def word_map_rules(word_pairs, min_count: int = 2) -> list[MapRule]:
    rules = []
    for wp in word_pairs:
        if not wp.direction or wp.kind not in ("substitution", "split", "merge"):
            continue
        casings = tuple((tuple(graphemes(nfc(s))), tuple(graphemes(nfc(t))))
                        for (s, t), c in wp.casings.most_common() if c >= min_count)
        rules.append(_rule("word", tuple(graphemes(wp.source)), tuple(graphemes(wp.target)),
                           BOUNDARY, BOUNDARY, wp.direction, wp.count, wp.p_fwd, wp.p_bwd,
                           f"{wp.source} → {wp.target}", casings=casings))
    return rules


def _punct_contexts(g: str, classes: dict[str, str]) -> list[tuple[str | None, str | None]]:
    """A 'both' character is punctuation unless it sits between two word-forming
    characters: that is, when not preceded by one, or preceded by one and not
    followed by one."""
    if classes.get(g) == BOTH:
        return [(NONWORD, None), (WORDCHAR, NONWORD)]
    return [(None, None)]


def punct_map_rules(punct_pairs, classes: dict[str, str]) -> list[MapRule]:
    rules = []
    for p in punct_pairs:
        if not p.direction:
            continue
        lctx, rctx = _punct_contexts(p.source, classes), _punct_contexts(p.target, classes)
        common = (p.count, p.p_fwd, p.p_bwd, f"{p.source} → {p.target}")
        lhs, rhs = (p.source,), (p.target,)
        if p.direction == "both" and len(lctx) == len(rctx):
            for (a, b), (c, d) in zip(lctx, rctx):
                rules.append(_rule("punct", lhs, rhs, a, b, "both", *common, rpre=c, rpost=d))
            continue
        if p.direction in ("both", "fwd"):
            for a, b in lctx:
                rules.append(_rule("punct", lhs, rhs, a, b, "fwd", *common, rpre=None, rpost=None))
        if p.direction in ("both", "bwd"):
            for c, d in rctx:
                rules.append(_rule("punct", lhs, rhs, None, None, "bwd", *common, rpre=c, rpost=d))
    return rules


# ---------------------------------------------------------------- simulator

def _split_words(seq: Word) -> tuple[Word, ...]:
    words, current = [], []
    for g in seq:
        if g == " ":
            words.append(tuple(current))
            current = []
        else:
            current.append(g)
    words.append(tuple(current))
    return tuple(words)


def _ctx_ok(want: str | None, word: Word, pos: int) -> bool:
    if want is None:
        return True
    inside = 0 <= pos < len(word)
    if want in (BOUNDARY, NONWORD):
        return not inside
    if want == WORDCHAR:
        return inside
    return inside and word[pos] == want


class Converter:
    """Apply map rules to lower-case words the way TECkit applies them to text:
    left to right, the longest match (then longest context) first, file order
    on ties, output never rescanned. Word rules are tried before character
    rules; a word rule's match is the whole word, so it is never shorter than
    a character rule matching at the same place."""

    def __init__(self, rules: Iterable[MapRule], forward: bool, layout: Layout = Layout()):
        allowed = ("both", "fwd") if forward else ("both", "bwd")
        self.words: dict[tuple[Word, ...], tuple[Word, ...]] = {}
        chars = []
        for index, r in enumerate(rules):
            if r.direction not in allowed or r.kind == "punct":
                continue
            match, repl = (r.lhs, r.rhs) if forward else (r.rhs, r.lhs)
            if r.kind == "word":
                self.words.setdefault(_split_words(match), _split_words(repl))
            else:
                pre, post = (r.pre, r.post) if forward else (r.rpre, r.rpost)
                length, ctx = sort_key(r, forward, layout)
                chars.append((-length, -ctx, index, match, repl, pre, post, r.text))
        chars.sort(key=lambda c: c[:3])
        self.by_first: dict[str, list] = {}
        for c in chars:
            self.by_first.setdefault(c[3][0], []).append(c[3:])
        self.max_words = max((len(k) for k in self.words), default=1)

    def convert_word(self, word: Word) -> tuple[Word, list[str]]:
        out: list[str] = []
        used: list[str] = []
        i = 0
        while i < len(word):
            for match, repl, pre, post, text in self.by_first.get(word[i], ()):
                k = len(match)
                if (word[i:i + k] == match and _ctx_ok(pre, word, i - 1)
                        and _ctx_ok(post, word, i + k)):
                    out.extend(repl)
                    used.append(text)
                    i += k
                    break
            else:
                out.append(word[i])
                i += 1
        return tuple(out), used

    def convert_tokens(self, tokens: list[Word]) -> list[Word]:
        out: list[Word] = []
        i = 0
        while i < len(tokens):
            for n in range(min(self.max_words, len(tokens) - i), 0, -1):
                repl = self.words.get(tuple(tokens[i:i + n]))
                if repl is not None:
                    out.extend(repl)
                    i += n
                    break
            else:
                out.append(self.convert_word(tokens[i])[0])
                i += 1
        return out


# ---------------------------------------------------------------- writing

def _cp(s: str) -> str:
    return " ".join(f"U+{ord(c):04X}" for c in s)


def _seq(seq: Word) -> str:
    return " ".join(_cp(g) for g in seq)


def _item(c: str | None, fold_case: bool, side: str, layout: Layout) -> str:
    """Render one context item. `side` is 'pre' or 'post'."""
    if c is None:
        return ""
    if c == BOUNDARY:
        if not layout.both:
            return "^[W]"
        # A 'both' character next to the match is still a boundary when the
        # character beyond it is not word-forming (for example a closing quote).
        return "(^[WB] | ^[W] [B])" if side == "pre" else "(^[WB] | [B] ^[W])"
    if c == WORDCHAR:
        return "[W]"
    if c == NONWORD:
        return "^[W]"
    if c == NOTUPPER:
        return "^[U]"
    forms = _case_forms(c, fold_case)
    if len(forms) == 1:
        return _cp(c) if len(c) == 1 else f"({_cp(c)})"
    return "(" + " | ".join(_cp(v) for v in forms) + ")"


def _context(kind: str, pre: str | None, post: str | None, fold_case: bool,
             layout: Layout) -> str:
    p = _item(pre, fold_case, "pre", layout)
    q = _item(post, fold_case, "post", layout)
    if _guarded(kind, post, layout):
        q = f"{q} ^[M]".strip()
    if not p and not q:
        return ""
    parts = ["/"] + ([p] if p else []) + ["_"] + ([q] if q else [])
    return " " + " ".join(parts)


def _class_items(chars: set[str]) -> list[str]:
    cps = sorted({ord(c) for c in chars})
    items, i = [], 0
    while i < len(cps):
        j = i
        while j + 1 < len(cps) and cps[j + 1] == cps[j] + 1:
            j += 1
        items.append(f"U+{cps[i]:04X}" if i == j else f"U+{cps[i]:04X}..U+{cps[j]:04X}")
        i = j + 1
    return items


def _class_line(name: str, chars: set[str]) -> str:
    items = _class_items(chars)
    rows = [" ".join(items[k:k + 8]) for k in range(0, len(items), 8)]
    return f"Class [{name}] = ( " + " \\\n    ".join(rows) + " )"


def _cap(seq: Word) -> Word:
    return (seq[0].upper(),) + seq[1:] if seq else seq


def _upper(seq: Word) -> Word:
    return tuple(g.upper() for g in seq)


BOTH_WAYS = frozenset({"fwd", "bwd"})


def _word_variants(r: MapRule, lctx: str, rctx: str) -> list[tuple]:
    """Each match form, in each direction, gets its most frequent observed
    casing (casings rarer than min_count were dropped earlier), else the
    generated lower, Title or UPPER form. So LORD -> Yahweh can be learned,
    while one odd dog -> Hound cannot hijack the Title variant."""
    generated = [(r.lhs, r.rhs), (_cap(r.lhs), _cap(r.rhs)), (_upper(r.lhs), _upper(r.rhs))]
    candidates = list(r.casings) + generated
    fwd: dict[Word, Word] = {}
    bwd: dict[Word, Word] = {}
    for lhs, rhs in candidates:
        fwd.setdefault(lhs, rhs)
        bwd.setdefault(rhs, lhs)
    out = []
    for lhs, rhs in candidates:
        dirs = frozenset(d for d, ok in (("fwd", fwd[lhs] == rhs), ("bwd", bwd[rhs] == lhs))
                         if ok)
        if dirs:
            out.append((lhs, rhs, lctx, rctx, dirs))
    return out


def _variants(r: MapRule, layout: Layout) -> list[tuple]:
    """Case variants as (lhs, rhs, lhs context, rhs context, directions):
    lower, Title and UPPER, and for word rules the observed casings.

    A Title variant of a character rule applies only at the start of a word
    and only when the next character is not a capital, so it cannot fire
    inside an all-caps word, where the UPPER variant applies instead."""
    fold = r.kind != "punct"
    lctx = _context(r.kind, r.pre, r.post, fold, layout)
    rctx = _context(r.kind, r.rpre, r.rpost, fold, layout)
    if r.kind == "word":
        out = _word_variants(r, lctx, rctx)
    else:
        out = [(r.lhs, r.rhs, lctx, rctx, BOTH_WAYS)]
    if (r.kind == "char" and r.pre in (None, BOUNDARY) and r.rpre in (None, BOUNDARY)
          and _cap(r.lhs) != r.lhs and (not r.rhs or _cap(r.rhs) != r.rhs)):
        # Only where both sides really start a word with a cased letter, so a
        # [W] guard is never replaced and 't < 't never becomes T < 't.
        def title(post):
            if post is None and layout.upper:
                post = NOTUPPER
            return _context(r.kind, BOUNDARY, post, False, layout)
        out.append((_cap(r.lhs), _cap(r.rhs), title(r.post), title(r.rpost), BOTH_WAYS))
    if r.kind == "char":
        out.append((_upper(r.lhs), _upper(r.rhs), lctx, rctx, BOTH_WAYS))
    seen, unique = set(), []
    for v in out:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    return unique


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode().replace('"', "'")


def render_rules(rules: list[MapRule], layout: Layout) -> list[str]:
    """Rule lines with case variants. A variant whose match is already used in
    a direction is dropped from that direction, so every match is unambiguous."""
    used_f: set[tuple[Word, str]] = set()
    used_b: set[tuple[Word, str]] = set()
    lines: list[str] = []
    headings = {"word": "; Word rules", "punct": "; Punctuation rules",
                "char": "; Character rules"}
    current = None
    for r in rules:
        if r.kind != current:
            current = r.kind
            lines += ["", headings[r.kind]]
        for n, (lhs, rhs, lctx, rctx, dirs) in enumerate(_variants(r, layout)):
            fwd = r.direction in ("both", "fwd") and "fwd" in dirs and (lhs, lctx) not in used_f
            bwd = r.direction in ("both", "bwd") and "bwd" in dirs and (rhs, rctx) not in used_b
            if not fwd and not bwd:
                continue
            direction = "both" if fwd and bwd else "fwd" if fwd else "bwd"
            if fwd:
                used_f.add((lhs, lctx))
            if bwd:
                used_b.add((rhs, rctx))
            left = _seq(lhs) + (lctx if fwd else "")
            right = _seq(rhs) + (rctx if bwd else "")
            comment = r.text if n == 0 else f"{r.text} (case variant)"
            if r.note and n == 0:
                comment += f"; {r.note}"
            lines.append(f"{left} {OPERATORS[direction]} {right}".rstrip() + f"   ; {comment}")
    return lines


def write_map(path: str | Path, rules: list[MapRule], word_chars: set[str], marks: set[str],
              both_chars: set[str], lhs_name: str, rhs_name: str, lhs_desc: str,
              rhs_desc: str) -> None:
    if not word_chars:
        raise ValueError("no word-forming characters, so word boundaries cannot be written")
    out = [
        f"; Generated by diff2dict on {date.today().isoformat()}.",
        "; LHS is the source text, RHS the target. Rules marked > or < work in one",
        "; direction only; oneway_rules.csv explains why.",
        "",
        f'LHSName "{_ascii(lhs_name)}"',
        f'RHSName "{_ascii(rhs_name)}"',
        f'LHSDescription "{_ascii(lhs_desc)}"',
        f'RHSDescription "{_ascii(rhs_desc)}"',
        'Version "1"',
        "LHSFlags (ExpectsNFC GeneratesNFC)",
        "RHSFlags (ExpectsNFC GeneratesNFC)",
        "",
        "pass(Unicode)",
        "",
        "; [W] holds the word-forming characters; ^[W] is a word boundary.",
        _class_line("W", word_chars),
    ]
    if marks:
        out += ["; [M] holds combining marks; ^[M] stops a rule splitting a base from its mark.",
                _class_line("M", marks)]
    layout = make_layout(word_chars, marks, both_chars)
    if layout.upper:
        out += ["; [U] holds capitals and combining marks; ^[U] after a Title-case match",
                "; keeps it out of all-caps words.",
                _class_line("U", {c for c in word_chars if _is_upper(c)} | marks)]
    if layout.both:
        out += ["; [B] holds characters that are word-forming only between two word-forming",
                "; characters, such as an apostrophe; [WB] is [W] and [B] together.",
                _class_line("B", both_chars), _class_line("WB", word_chars | both_chars)]
    out += render_rules(rules, layout)
    # UTF-8 with a BOM, which the compiler recognises, so comments can show
    # the characters themselves. The rules use U+XXXX codes throughout.
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8-sig")


def oneway_rows(rules: list[MapRule], unmapped: list[tuple[str, str]],
                reliability: float) -> list[list]:
    rows = []
    seen = set()
    for r in rules:
        # A punctuation swap that is reliable both ways is still written as two
        # one-way rules when only one side is a 'both' character; skip those.
        reliable_both = min(r.p_fwd, r.p_bwd) >= reliability and not r.note
        if r.direction == "both" or reliable_both or (r.text, r.direction) in seen:
            continue
        seen.add((r.text, r.direction))
        if r.note:
            reason = r.note
        elif r.direction == "fwd" and "→ ∅" in r.text:
            reason = (f"reverse unreliable (p_bwd {r.p_bwd:.2f} < {reliability}); "
                      "the context also occurs where nothing was deleted")
        elif r.direction == "fwd":
            reason = (f"reverse unreliable (p_bwd {r.p_bwd:.2f} < {reliability}); "
                      "the target also comes from other sources, so probably a merger")
        else:
            reason = (f"forward unreliable (p_fwd {r.p_fwd:.2f} < {reliability}); "
                      "this source has several targets")
        rows.append([r.text, r.kind, r.direction, round(r.p_fwd, 3), round(r.p_bwd, 3), reason])
    for text, reason in unmapped:
        rows.append([text, "char", "none", "", "", reason])
    return rows


# ---------------------------------------------------------------- real TECkit

def find_tools() -> tuple[str | None, str | None]:
    return shutil.which("teckit_compile"), shutil.which("txtconv")


def compile_map(compiler: str, map_path: Path, tec_path: Path) -> tuple[bool, str]:
    proc = subprocess.run([compiler, str(map_path), "-o", str(tec_path)],
                          capture_output=True, text=True, errors="replace")
    ok = proc.returncode == 0 and tec_path.exists()
    return ok, (proc.stdout + proc.stderr).strip()


TXTCONV_ATTEMPTS = 3   # txtconv 2.5.12 on Windows occasionally crashes on valid input


def run_txtconv(txtconv: str, tec_path: Path, lines: list[str], reverse: bool) -> list[str]:
    """Convert lines with txtconv. Raises RuntimeError if every attempt fails."""
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.txt", Path(tmp) / "out.txt"
        src.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        cmd = [txtconv, "-t", str(tec_path), "-i", str(src), "-o", str(dst),
               "-if", "utf8", "-of", "utf8", "-nobom"] + (["-r"] if reverse else [])
        for _ in range(TXTCONV_ATTEMPTS):
            proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
            if proc.returncode == 0 and dst.exists():
                text = dst.read_text(encoding="utf-8-sig")
                return [nfc(line.removesuffix("\r")) for line in text.split("\n")[:len(lines)]]
        raise RuntimeError(f"txtconv failed with exit code {proc.returncode}: "
                           f"{(proc.stdout + proc.stderr).strip()}")


def teckit_evaluate(map_path: Path, held, classes: dict[str, str]) -> dict[str, object]:
    """Compile the map and, when lines were held out, score txtconv's output
    in each direction. Returns a dict for the Summary sheet."""
    from .mine import _match_score
    from .tokens import tokenize
    from .unicodetools import lower_graphemes

    compiler, txtconv = find_tools()
    if not compiler:
        return {"teckit": "teckit_compile not found on PATH, so the map was not "
                          "compiled or evaluated with TECkit"}
    with tempfile.TemporaryDirectory() as tmp:
        tec = Path(tmp) / "rules.tec"
        ok, message = compile_map(compiler, map_path, tec)
        if not ok:
            return {"teckit": f"teckit_compile failed: {message}"}
        out: dict[str, object] = {"teckit": "compiled"}
        if not txtconv or not held:
            out["teckit"] += "; txtconv not found" if not txtconv else "; no held-out lines"
            return out

        def score(lines_in, reverse, targets):
            converted = run_txtconv(txtconv, tec, lines_in, reverse)
            matched = total = 0
            for text, target in zip(converted, targets):
                words = [lower_graphemes(w) for w in tokenize(text, classes)[0]]
                matched += 2 * _match_score(words, target)
                total += len(words) + len(target)
            return matched / total if total else 0.0

        try:
            out["teckit_forward"] = score([ln.src for ln in held], False,
                                          [ln.tgt_low for ln in held])
            out["teckit_backward"] = score([ln.tgt for ln in held], True,
                                           [ln.src_low for ln in held])
        except (RuntimeError, OSError) as e:
            out["teckit"] += f"; evaluation failed: {e}"
        return out
