"""The bootstrapping miner: character rules, word pairs and punctuation swaps."""

import csv
import random
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from . import teckit
from .align import BOUNDARY, CLOSE, CostModel, Link, RuleKey, Word, align_words, similarity
from .tokens import tokenize
from .unicodetools import (BOTH, WORD, graphemes, is_mark, is_marker, lower_graphemes, nfc,
                           suggest_class)

MAX_CHUNK = 3
MAX_EXAMPLES = 5
EMPTY = "∅"   # EMPTY SET, shown for an empty side of a rule


@dataclass
class Params:
    threshold: float = 0.7
    min_count: int = 2
    max_iter: int = 5
    reliability: float = 0.95
    holdout: float = 0.1
    seed: int = 1
    review: Path | None = None


@dataclass
class Line:
    number: int                 # 1-based line number in the files
    src: str
    tgt: str
    src_words: list[str]
    tgt_words: list[str]
    src_low: list[Word]
    tgt_low: list[Word]
    src_punct: list[str]
    tgt_punct: list[str]


def direction_of(p_fwd: float, p_bwd: float, reliability: float) -> str:
    fwd, bwd = p_fwd >= reliability, p_bwd >= reliability
    return "both" if fwd and bwd else "fwd" if fwd else "bwd" if bwd else ""


def _show_ctx(c: str | None) -> str:
    return "" if c is None else c


def rule_text(x: Word, y: Word, left: str | None, right: str | None) -> str:
    text = f"{''.join(x) or EMPTY} → {''.join(y) or EMPTY}"
    if left is not None or right is not None:
        text += f" / {_show_ctx(left)} _ {_show_ctx(right)}".replace("  ", " ")
    return text.rstrip()


@dataclass
class CharRule:
    x: Word
    y: Word
    left: str | None
    right: str | None
    count: int
    p_fwd: float
    p_bwd: float
    direction: str              # 'both', 'fwd', 'bwd', or '' when unreliable
    examples: list[str] = field(default_factory=list)
    rejected: bool = False
    forced: bool = False

    @property
    def key(self) -> RuleKey:
        return (self.x, self.y, self.left, self.right)

    @property
    def accepted(self) -> bool:
        return bool(self.direction) and not self.rejected

    def text(self) -> str:
        return rule_text(self.x, self.y, self.left, self.right)


@dataclass
class WordPair:
    source: str
    target: str
    kind: str                   # substitution, regular, split, merge, insertion, deletion
    count: int = 0
    n_source: int = 0
    n_target: int = 0
    p_fwd: float = 0.0
    p_bwd: float = 0.0
    direction: str = ""
    lines: list[int] = field(default_factory=list)
    casings: Counter = field(default_factory=Counter)
    rules: list[str] = field(default_factory=list)

    @property
    def genuine(self) -> bool:
        return self.kind != "regular"


@dataclass
class PunctPair:
    source: str
    target: str
    count: int
    p_fwd: float
    p_bwd: float
    direction: str
    lines: list[int]


@dataclass
class Result:
    params: Params
    n_lines: int
    n_blank: int
    n_train: int
    n_held: int
    iterations: int
    converged: bool
    skipped: list[tuple[Line, float]]
    char_rules: list[CharRule]
    word_pairs: list[WordPair]
    punct_pairs: list[PunctPair]
    case_only: Counter
    map_rules: list["teckit.MapRule"]
    unmapped: list[tuple[str, str]]          # (rule text, reason)
    evaluation: dict[str, float | int]
    held: list[Line]
    word_chars: set[str]
    mark_chars: set[str]
    both_chars: set[str]


# ---------------------------------------------------------------- preparation

def prepare(src_lines: list[str], tgt_lines: list[str],
            classes: dict[str, str]) -> tuple[list[Line], int]:
    """Tokenise line pairs. Pairs where either side is blank or a vref marker
    such as <range> are dropped; the number dropped is returned too."""
    n = max(len(src_lines), len(tgt_lines))
    src_lines = src_lines + [""] * (n - len(src_lines))
    tgt_lines = tgt_lines + [""] * (n - len(tgt_lines))
    lines: list[Line] = []
    blank = 0
    for number, (s, t) in enumerate(zip(src_lines, tgt_lines), start=1):
        if not s.strip() or not t.strip() or is_marker(s) or is_marker(t):
            blank += 1
            continue
        sw, sp = tokenize(s, classes)
        tw, tp = tokenize(t, classes)
        if not sw and not tw:
            blank += 1
            continue
        lines.append(Line(number, s, t, sw, tw, [lower_graphemes(w) for w in sw],
                          [lower_graphemes(w) for w in tw], sp, tp))
    return lines, blank


def split_holdout(lines: list[Line], fraction: float, seed: int) -> tuple[list[Line], list[Line]]:
    k = round(len(lines) * fraction)
    if k <= 0:
        return lines, []
    held = set(random.Random(seed).sample(range(len(lines)), k))
    return ([ln for i, ln in enumerate(lines) if i not in held],
            [ln for i, ln in enumerate(lines) if i in held])


def align_lines(lines: list[Line], model: CostModel, threshold: float
                ) -> tuple[list[tuple[Line, list[Link]]], list[tuple[Line, float]]]:
    aligned, skipped = [], []
    for line in lines:
        links = align_words(line.src_low, line.tgt_low, model)
        sim = similarity(links, len(line.src_low), len(line.tgt_low))
        if sim >= threshold:
            aligned.append((line, links))
        else:
            skipped.append((line, sim))
    return aligned, skipped


# ---------------------------------------------------------------- char rules

def ngram_counts(words: Counter) -> Counter:
    """Counts of every grapheme n-gram (n <= 5) of the words padded with
    boundaries, weighted by word frequency."""
    counts: Counter = Counter()
    for w, c in words.items():
        p = (BOUNDARY,) + w + (BOUNDARY,)
        for i in range(len(p)):
            for n in range(1, min(MAX_CHUNK + 2, len(p) - i) + 1):
                counts[p[i:i + n]] += c
    return counts


def chunks(s: Word, t: Word, ops) -> list[tuple]:
    """Group the edits of one grapheme alignment into rules with one grapheme
    of context: (x, y, left, right, target_left, target_right). The target
    contexts are the graphemes around y in t, which differ from left and right
    when a neighbour was changed too. An insertion or deletion that doubles or
    undoubles a neighbouring grapheme absorbs it, so doubling reads as a -> aa."""
    spans: list[list[int]] = []
    current: list[int] | None = None
    for kind, i0, i1, j0, j1 in ops:
        if kind == "edit" and current and current[1] == i0 and current[3] == j0:
            current[1], current[3] = i1, j1
            continue
        if current:
            spans.append(current)
            current = None
        if kind == "edit":
            current = [i0, i1, j0, j1]
        elif kind == "rule":
            spans.append([i0, i1, j0, j1])
    if current:
        spans.append(current)

    out: list[RuleKey] = []
    for i0, i1, j0, j1 in spans:
        x, y = s[i0:i1], t[j0:j1]
        if not x or not y:
            moved = x or y
            if i0 > 0 and j0 > 0 and moved[0] == s[i0 - 1] == t[j0 - 1]:
                g = s[i0 - 1]
                x, y, i0, j0 = (g,) + x, (g,) + y, i0 - 1, j0 - 1
            elif i1 < len(s) and j1 < len(t) and moved[-1] == s[i1] == t[j1]:
                g = s[i1]
                x, y, i1, j1 = x + (g,), y + (g,), i1 + 1, j1 + 1
        if len(x) > MAX_CHUNK or len(y) > MAX_CHUNK or x == y:
            continue
        out.append((x, y, s[i0 - 1] if i0 > 0 else BOUNDARY, s[i1] if i1 < len(s) else BOUNDARY,
                    t[j0 - 1] if j0 > 0 else BOUNDARY, t[j1] if j1 < len(t) else BOUNDARY))
    return out


def _key(ctx: str | None, seq: Word, ctx2: str | None) -> tuple:
    return ((ctx,) if ctx is not None else ()) + seq + ((ctx2,) if ctx2 is not None else ())


def _probabilities(x, y, left, right, count, count_bwd, src_ng, tgt_ng
                   ) -> tuple[float, float]:
    """p_fwd: share of source x in this context that became y. p_bwd: share of
    target y in this context that came from x, counting only occurrences whose
    target neighbours match the context, since the reverse rule reads those."""
    occ_src = src_ng.get(_key(left, x, right), 0)
    occ_tgt = tgt_ng.get(_key(left, y, right), 0)
    return count / max(occ_src, count), min(1.0, count_bwd / occ_tgt) if occ_tgt else 0.0


def _agrees(key: tuple, left: str | None, right: str | None) -> bool:
    """Whether an occurrence's target neighbours match a generalised context."""
    return (left is None or key[2] == left) and (right is None or key[3] == right)


def _pick_examples(examples: dict, covered) -> list[str]:
    out: list[str] = []
    for ctx in covered:
        for ex in examples.get(ctx, ()):
            if ex not in out:
                out.append(ex)
            if len(out) == MAX_EXAMPLES:
                return out
    return out


def generalise(x: Word, y: Word, contexts: Counter, src_ng: Counter, tgt_ng: Counter,
               params: Params, examples: dict) -> list[CharRule]:
    """Cover the observed contexts of x -> y with the least specific rules
    that stay reliable. Rules with an empty side always need some context.
    `contexts` counts (left, right, target_left, target_right) keys and
    `examples` maps each (left, right) context to example word pairs."""
    remaining = Counter(contexts)
    levels = ([[(False, False)]] if x and y else []) + [
        [(True, False), (False, True)], [(True, True)]]
    rules: list[CharRule] = []
    for projections in levels:
        while remaining:
            groups: Counter = Counter()
            agreeing: Counter = Counter()
            for key, c in remaining.items():
                for use_l, use_r in projections:
                    g = (key[0] if use_l else None, key[1] if use_r else None)
                    groups[g] += c
                    if _agrees(key, *g):
                        agreeing[g] += c
            best = None
            for (gl, gr), c in groups.most_common():
                if c < params.min_count:
                    break
                pf, pb = _probabilities(x, y, gl, gr, c, agreeing[(gl, gr)], src_ng, tgt_ng)
                if max(pf, pb) >= params.reliability:
                    best = (gl, gr, c, pf, pb)
                    break
            if best is None:
                break
            gl, gr, c, pf, pb = best
            covered = [k for k in remaining
                       if (gl is None or k[0] == gl) and (gr is None or k[1] == gr)]
            rules.append(CharRule(x, y, gl, gr, c, pf, pb,
                                  direction_of(pf, pb, params.reliability),
                                  _pick_examples(examples, [k[:2] for k in covered])))
            for key in covered:
                del remaining[key]
    # Whatever is left is unreliable; report it so the user can see it.
    if x and y:
        leftover = sum(remaining.values())
        if leftover >= params.min_count:
            pf, pb = _probabilities(x, y, None, None, leftover, leftover, src_ng, tgt_ng)
            rules.append(CharRule(x, y, None, None, leftover, pf, pb, "",
                                  _pick_examples(examples, [k[:2] for k in remaining])))
    else:
        by_ctx: Counter = Counter()
        agreeing: Counter = Counter()
        for key, c in remaining.items():
            by_ctx[key[:2]] += c
            if _agrees(key, *key[:2]):
                agreeing[key[:2]] += c
        for (left, right), c in by_ctx.items():
            if c >= params.min_count:
                pf, pb = _probabilities(x, y, left, right, c, agreeing[(left, right)],
                                        src_ng, tgt_ng)
                rules.append(CharRule(x, y, left, right, c, pf, pb, "",
                                      _pick_examples(examples, [(left, right)])))
    return rules


def close_pairs(aligned) -> tuple[Counter, Counter, Counter]:
    """1:1 word pairs from aligned lines that are identical or close, plus the
    source and target word populations they come from."""
    pairs: Counter = Counter()
    src_words: Counter = Counter()
    tgt_words: Counter = Counter()
    for line, links in aligned:
        for link in links:
            if link.kind == "sub" and link.close:
                s, t = line.src_low[link.src[0]], line.tgt_low[link.tgt[0]]
                src_words[s] += 1
                tgt_words[t] += 1
                if s != t:
                    pairs[(s, t)] += 1
    return pairs, src_words, tgt_words


def mine_char_rules(aligned, model: CostModel, params: Params) -> list[CharRule]:
    pairs, src_words, tgt_words = close_pairs(aligned)
    occurrences: dict[tuple[Word, Word], Counter] = defaultdict(Counter)
    examples: dict[tuple[Word, Word], dict] = defaultdict(lambda: defaultdict(list))
    for (s, t), c in pairs.items():
        example = f"{''.join(s)}→{''.join(t)}"
        for x, y, left, right, t_left, t_right in chunks(s, t, model.align(s, t)):
            occurrences[(x, y)][(left, right, t_left, t_right)] += c
            ex = examples[(x, y)][(left, right)]
            if len(ex) < MAX_EXAMPLES and example not in ex:
                ex.append(example)
    src_ng, tgt_ng = ngram_counts(src_words), ngram_counts(tgt_words)
    rules: list[CharRule] = []
    for (x, y), contexts in occurrences.items():
        rules.extend(generalise(x, y, contexts, src_ng, tgt_ng, params, examples[(x, y)]))
    rules.sort(key=lambda r: (-r.count, r.text()))
    return rules


# ---------------------------------------------------------------- review

REVIEW_HEADER = ["source", "target", "left", "right", "count", "p_fwd", "p_bwd",
                 "direction", "examples", "keep"]


def _parse_ctx(text: str) -> str | None:
    text = nfc(text.strip())
    return text or None


def review(path: Path, rules: list[CharRule], ask: Callable[[str], str] = input
           ) -> tuple[set[RuleKey], set[RuleKey]]:
    """Write the first-pass rules for editing (unless the file already exists),
    wait for the user, then read back which to reject (keep 0) or force (keep 1)."""
    if not path.exists():
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(REVIEW_HEADER)
            for r in rules:
                w.writerow(["".join(r.x), "".join(r.y), _show_ctx(r.left), _show_ctx(r.right),
                            r.count, f"{r.p_fwd:.3f}", f"{r.p_bwd:.3f}", r.direction,
                            "; ".join(r.examples), ""])
        try:
            ask(f"Edit {path} (set keep to 0 to reject a rule, 1 to force one; "
                "leave it empty to let later passes decide), "
                "then press Enter to continue: ")
        except EOFError:
            pass
    rejected: set[RuleKey] = set()
    forced: set[RuleKey] = set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (tuple(graphemes(nfc(row["source"]))), tuple(graphemes(nfc(row["target"]))),
                   _parse_ctx(row["left"]), _parse_ctx(row["right"]))
            keep = (row.get("keep") or "").strip().lower()
            if keep in ("0", "n", "no", "false"):
                rejected.add(key)
            elif keep in ("1", "y", "yes", "true"):
                forced.add(key)
    return rejected, forced


def apply_review(rules: list[CharRule], rejected: set[RuleKey], forced: set[RuleKey]) -> None:
    for r in rules:
        r.rejected = r.key in rejected
        r.forced = r.key in forced and not r.direction
        if r.forced:
            r.direction = "fwd" if r.p_fwd >= r.p_bwd else "bwd"


# ---------------------------------------------------------------- stage D

def _src_text(line: Line, idx) -> str:
    return " ".join(line.src_words[i] for i in idx)


def _tgt_text(line: Line, idx) -> str:
    return " ".join(line.tgt_words[i] for i in idx)


def _low_text(words: list[Word], idx) -> str:
    return " ".join("".join(words[i]) for i in idx)


def ngrams_of_lines(aligned, side: str) -> Counter:
    counts: Counter = Counter()
    for line, _ in aligned:
        words = line.src_low if side == "src" else line.tgt_low
        for n in range(1, 4):
            for i in range(len(words) - n + 1):
                counts[" ".join("".join(w) for w in words[i:i + n])] += 1
    return counts


KIND_OF_LINK = {"sub": "substitution", "split": "split", "merge": "merge",
                "del": "deletion", "ins": "insertion"}


def classify_pairs(aligned, fwd: "teckit.Converter",
                   params: Params) -> tuple[list[WordPair], Counter]:
    """A differing pair is regular when the forward character rules turn the
    source into the target; otherwise it is genuine and may become a word rule.
    A pair that only the reverse rules explain is therefore genuine, so forward
    conversion still gets a rule for it."""
    pairs: dict[tuple[str, str], WordPair] = {}
    case_only: Counter = Counter()
    verdicts: dict[tuple[Word, Word], tuple[str, list[str]]] = {}
    for line, links in aligned:
        for link in links:
            kind = KIND_OF_LINK[link.kind]
            used: list[str] = []
            if kind == "substitution":
                s, t = line.src_low[link.src[0]], line.tgt_low[link.tgt[0]]
                if s == t:
                    so, to = line.src_words[link.src[0]], line.tgt_words[link.tgt[0]]
                    if so != to:
                        case_only[(so, to)] += 1
                    continue
                if (s, t) not in verdicts:
                    out, used = fwd.convert_word(s)
                    verdicts[(s, t)] = ("regular", used) if out == t else ("substitution", [])
                kind, used = verdicts[(s, t)]
            key = (_low_text(line.src_low, link.src), _low_text(line.tgt_low, link.tgt))
            wp = pairs.get(key)
            if wp is None:
                wp = pairs[key] = WordPair(key[0], key[1], kind, rules=used)
            wp.count += 1
            wp.casings[(_src_text(line, link.src), _tgt_text(line, link.tgt))] += 1
            if len(wp.lines) < MAX_EXAMPLES:
                wp.lines.append(line.number)
    src_ng, tgt_ng = ngrams_of_lines(aligned, "src"), ngrams_of_lines(aligned, "tgt")
    for wp in pairs.values():
        wp.n_source = src_ng.get(wp.source, 0) if wp.source else 0
        wp.n_target = tgt_ng.get(wp.target, 0) if wp.target else 0
        wp.p_fwd = wp.count / max(wp.n_source, wp.count) if wp.source else 0.0
        wp.p_bwd = wp.count / max(wp.n_target, wp.count) if wp.target else 0.0
        if wp.kind in ("substitution", "split", "merge") and wp.count >= params.min_count:
            wp.direction = direction_of(wp.p_fwd, wp.p_bwd, params.reliability)
    result = sorted(pairs.values(), key=lambda p: (-p.count, p.source, p.target))
    return result, case_only


def _punct_subs(aligned, model: CostModel
                ) -> tuple[Counter, dict[tuple[str, str], list[int]], Counter, Counter]:
    """Count 1:1 punctuation substitutions across aligned lines, with the source
    and target punctuation populations. A learned rule aligns a reordered mark as
    a substitution, so its op counts the same as a plain edit."""
    subs: Counter = Counter()
    lines: dict[tuple[str, str], list[int]] = defaultdict(list)
    occ_src: Counter = Counter()
    occ_tgt: Counter = Counter()
    for line, _ in aligned:
        occ_src.update(line.src_punct)
        occ_tgt.update(line.tgt_punct)
        if line.src_punct == line.tgt_punct:
            continue
        s, t = tuple(line.src_punct), tuple(line.tgt_punct)
        for kind, i0, i1, j0, j1 in model.align(s, t):
            if kind in ("edit", "rule") and i1 - i0 == 1 and j1 - j0 == 1 and s[i0] != t[j0]:
                pair = (s[i0], t[j0])
                subs[pair] += 1
                if len(lines[pair]) < MAX_EXAMPLES:
                    lines[pair].append(line.number)
    return subs, lines, occ_src, occ_tgt


def mine_punct(aligned, params: Params) -> list[PunctPair]:
    # Bootstrap the alignment the way the character rules do: feed each frequent
    # substitution back as a cheap rule and re-align. Without it, a line where
    # marks were reordered (nested quotes, for example) aligns the swapped marks
    # as an insertion plus a deletion, so they are never counted and the forward
    # probability is understated.
    rules: list[RuleKey] = []
    subs, lines, occ_src, occ_tgt = _punct_subs(aligned, CostModel())
    for _ in range(params.max_iter):
        new = [((x,), (y,), None, None) for (x, y), c in subs.items() if c >= params.min_count]
        if set(new) == set(rules):
            break
        rules = new
        subs, lines, occ_src, occ_tgt = _punct_subs(aligned, CostModel(rules))
    out = []
    for (x, y), c in subs.most_common():
        pf, pb = c / max(occ_src[x], c), c / max(occ_tgt[y], c)
        direction = direction_of(pf, pb, params.reliability) if c >= params.min_count else ""
        out.append(PunctPair(x, y, c, pf, pb, direction, lines[(x, y)]))
    return out


# ---------------------------------------------------------------- evaluation

def _match_score(a: list[Word], b: list[Word]) -> int:
    return sum(block.size for block in
               SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())


def evaluate(held: list[Line], fwd: "teckit.Converter", bwd: "teckit.Converter"
             ) -> dict[str, float | int]:
    """Word accuracy on held-out lines: 2 * matching words / all words, before
    conversion (baseline) and after converting in each direction."""
    totals = Counter()
    for line in held:
        n = len(line.src_low) + len(line.tgt_low)
        totals["words"] += n
        totals["baseline"] += 2 * _match_score(line.src_low, line.tgt_low)
        totals["forward"] += 2 * _match_score(fwd.convert_tokens(line.src_low), line.tgt_low)
        totals["backward"] += 2 * _match_score(bwd.convert_tokens(line.tgt_low), line.src_low)
    words = totals["words"]
    out: dict[str, float | int] = {"held_lines": len(held), "held_words": words}
    for k in ("baseline", "forward", "backward"):
        out[k] = totals[k] / words if words else 0.0
    return out


# ---------------------------------------------------------------- driver

def character_sets(lines: list[Line], classes: dict[str, str]
                   ) -> tuple[set[str], set[str], set[str]]:
    """Codepoints of word-forming characters (both cases), of combining marks
    that follow a base character inside a grapheme, and of 'both' characters."""
    word_chars: set[str] = set()
    marks: set[str] = set()
    both: set[str] = set()
    seen: set[str] = set(classes)
    for line in lines:
        for w in line.src_words + line.tgt_words:
            seen.update(graphemes(w))
    for g in seen:
        cls = classes.get(g) or suggest_class(g)
        if cls == WORD:
            word_chars.update(g, g.upper(), g.lower())
            marks.update(c for c in g[1:] if is_mark(c))
        elif cls == BOTH:
            both.update(g)
    return word_chars, marks, both - word_chars


def mine(src_lines: list[str], tgt_lines: list[str], classes: dict[str, str],
         params: Params, log: Callable[[str], None] = print,
         ask: Callable[[str], str] = input) -> Result:
    lines, n_blank = prepare(src_lines, tgt_lines, classes)
    train, held = split_holdout(lines, params.holdout, params.seed)
    log(f"{len(lines)} line pairs to compare, {n_blank} skipped as blank or markers, "
        f"{len(held)} held out for evaluation.")

    accepted: list[CharRule] = []
    rejected: set[RuleKey] = set()
    forced: set[RuleKey] = set()
    converged = False
    iteration = 0
    rules: list[CharRule] = []
    aligned, skipped = [], []
    for iteration in range(1, params.max_iter + 1):
        model = CostModel(r.key for r in accepted)
        aligned, skipped = align_lines(train, model, params.threshold)
        rules = mine_char_rules(aligned, model, params)
        if iteration == 1 and params.review:
            rejected, forced = review(params.review, rules, ask)
        apply_review(rules, rejected, forced)
        new = [r for r in rules if r.accepted]
        log(f"Iteration {iteration}: {len(aligned)} lines aligned, {len(skipped)} skipped, "
            f"{len(new)} character rules accepted.")
        converged = {r.key for r in new} == {r.key for r in accepted}
        accepted = new
        if converged:
            break

    word_chars, marks, both_chars = character_sets(lines, classes)
    layout = teckit.make_layout(word_chars, marks, both_chars)
    char_map, unmapped = teckit.char_map_rules(accepted, both_chars)
    word_pairs, case_only = classify_pairs(aligned, teckit.Converter(char_map, True, layout),
                                           params)
    punct_pairs = mine_punct(aligned, params)
    punct_classes = {g: classes.get(g) or suggest_class(g)
                     for p in punct_pairs for g in (p.source, p.target)}
    map_rules = (teckit.word_map_rules(word_pairs, params.min_count)
                 + teckit.punct_map_rules(punct_pairs, punct_classes)
                 + char_map)

    final_model = CostModel(r.key for r in accepted)
    held_aligned, _ = align_lines(held, final_model, params.threshold)
    held_ok = [line for line, _ in held_aligned]
    evaluation = evaluate(held_ok, teckit.Converter(map_rules, True, layout),
                          teckit.Converter(map_rules, False, layout))

    return Result(params, max(len(src_lines), len(tgt_lines)), n_blank, len(train), len(held), iteration,
                  converged, skipped, rules, word_pairs, punct_pairs, case_only,
                  map_rules, unmapped, evaluation, held_ok, word_chars, marks, both_chars)
