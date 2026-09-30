"""Word alignment (weighted Needleman-Wunsch) and grapheme alignment
(weighted Levenshtein with backpointers).

Words are tuples of graphemes. A rule is (x, y, left, right): x and y are
grapheme tuples, one of which may be empty; left and right are None (any
context), BOUNDARY (word edge) or a single grapheme.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

BOUNDARY = "#"
RULE_COST = 0.1   # cost of applying a learned rule, whatever its length
INDEL = 0.7       # grapheme insertion or deletion. Two of them (1.4) beat two
                  # substitutions (2.0), so kale -> kaal aligns as +a and -e,
                  # not as l -> a and e -> l; one substitution (1.0) still
                  # beats an insertion plus a deletion
CLOSE = 0.5       # normalised distance at or below which two words are related
GAP = 0.55        # word insertion or deletion; a little over CLOSE, so an
                  # unrelated word in the same slot aligns as a substitution
MERGE_PENALTY = 0.15
MERGE_MAX = 0.15  # 1:2 and 1:3 links must be at least this close, so a
                  # deleted word is not glued onto its neighbour

Word = tuple[str, ...]
RuleKey = tuple[Word, Word, str | None, str | None]
Op = tuple[str, int, int, int, int]   # kind ('eq', 'edit', 'rule'), i0, i1, j0, j1


def _context_ok(want: str | None, have: str) -> bool:
    return want is None or want == have


class CostModel:
    """Edit costs, with learned rules acting as cheap transitions."""

    def __init__(self, rules: Iterable[RuleKey] = ()):
        self.by_x: dict[Word, list[tuple[Word, str | None, str | None]]] = defaultdict(list)
        for x, y, left, right in rules:
            self.by_x[x].append((y, left, right))
        self.max_x = max((len(x) for x in self.by_x), default=0)
        self._cache: dict[tuple[Word, Word], float] = {}

    def _rule_ends(self, s: Word) -> dict[int, list[tuple[int, Word]]]:
        """Map i_end to the rules whose source matches s[i_end - n:i_end]."""
        ends: dict[int, list[tuple[int, Word]]] = defaultdict(list)
        if not self.by_x:
            return ends
        n = len(s)
        for start in range(n + 1):
            left = s[start - 1] if start > 0 else BOUNDARY
            for nl in range(0, min(self.max_x, n - start) + 1):
                options = self.by_x.get(s[start:start + nl])
                if not options:
                    continue
                right = s[start + nl] if start + nl < n else BOUNDARY
                for y, want_l, want_r in options:
                    if _context_ok(want_l, left) and _context_ok(want_r, right):
                        ends[start + nl].append((nl, y))
        return ends

    def _table(self, s: Word, t: Word):
        n, m = len(s), len(t)
        ends = self._rule_ends(s)
        inf = float("inf")
        d = [[inf] * (m + 1) for _ in range(n + 1)]
        for i in range(n + 1):
            here = ends.get(i, ())
            row = d[i]
            up = d[i - 1] if i else None
            for j in range(m + 1):
                if i == 0 and j == 0:
                    best = 0.0
                else:
                    best = inf
                    if i and j:
                        best = up[j - 1] + (0.0 if s[i - 1] == t[j - 1] else 1.0)
                    if i and up[j] + INDEL < best:
                        best = up[j] + INDEL
                    if j and row[j - 1] + INDEL < best:
                        best = row[j - 1] + INDEL
                for nl, y in here:
                    ml = len(y)
                    if (nl or ml) and ml <= j and t[j - ml:j] == y:
                        v = d[i - nl][j - ml] + RULE_COST
                        if v < best:
                            best = v
                row[j] = best
        return d, ends

    def distance(self, s: Word, t: Word) -> float:
        """Normalised weighted edit distance in [0, 1]."""
        if s == t:
            return 0.0
        key = (s, t)
        cached = self._cache.get(key)
        if cached is None:
            d, _ = self._table(s, t)
            cached = min(1.0, d[len(s)][len(t)] / max(len(s), len(t)))
            self._cache[key] = cached
        return cached

    def align(self, s: Word, t: Word) -> list[Op]:
        """Grapheme alignment as a list of ops in left-to-right order."""
        d, ends = self._table(s, t)
        ops: list[Op] = []
        i, j = len(s), len(t)
        eps = 1e-9
        while i or j:
            here = d[i][j]
            step = None
            for nl, y in ends.get(i, ()):
                ml = len(y)
                if ((nl or ml) and ml <= j and t[j - ml:j] == y
                        and abs(d[i - nl][j - ml] + RULE_COST - here) < eps):
                    step = ("rule", i - nl, j - ml)
                    break
            if step is None and i and j:
                same = s[i - 1] == t[j - 1]
                if abs(d[i - 1][j - 1] + (0.0 if same else 1.0) - here) < eps:
                    step = ("eq" if same else "edit", i - 1, j - 1)
            if step is None and i and abs(d[i - 1][j] + INDEL - here) < eps:
                step = ("edit", i - 1, j)
            if step is None:
                step = ("edit", i, j - 1)
            kind, pi, pj = step
            ops.append((kind, pi, i, pj, j))
            i, j = pi, pj
        ops.reverse()
        return ops


@dataclass(frozen=True)
class Link:
    """An aligned group of words. kind is 'sub' (1:1), 'split' (1:2 or 1:3),
    'merge' (2:1 or 3:1), 'del' (source word only) or 'ins' (target word only)."""
    kind: str
    src: tuple[int, ...]
    tgt: tuple[int, ...]
    cost: float

    @property
    def close(self) -> bool:
        if self.kind in ("ins", "del"):
            return False
        extra = MERGE_PENALTY if self.kind in ("split", "merge") else 0.0
        return self.cost - extra <= CLOSE


def _concat(words: list[Word], a: int, b: int) -> Word:
    return tuple(g for w in words[a:b] for g in w)


def _nw(src: list[Word], tgt: list[Word], a0: int, a1: int, b0: int, b1: int,
        model: CostModel) -> list[Link]:
    n, m = a1 - a0, b1 - b0
    inf = float("inf")
    d = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    d[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best, choice = inf, None
            if i and j:
                c = d[i - 1][j - 1] + model.distance(src[a0 + i - 1], tgt[b0 + j - 1])
                if c < best:
                    best, choice = c, ("sub", 1, 1)
            for di, dj, kind in ((1, 2, "split"), (1, 3, "split"),
                                 (2, 1, "merge"), (3, 1, "merge")):
                if i >= di and j >= dj and d[i - di][j - dj] < inf:
                    s = _concat(src, a0 + i - di, a0 + i)
                    t = _concat(tgt, b0 + j - dj, b0 + j)
                    dist = model.distance(s, t)
                    if dist <= MERGE_MAX:
                        c = d[i - di][j - dj] + dist + MERGE_PENALTY
                        if c < best:
                            best, choice = c, (kind, di, dj)
            if i and d[i - 1][j] + GAP < best:
                best, choice = d[i - 1][j] + GAP, ("del", 1, 0)
            if j and d[i][j - 1] + GAP < best:
                best, choice = d[i][j - 1] + GAP, ("ins", 0, 1)
            d[i][j], back[i][j] = best, choice
    links: list[Link] = []
    i, j = n, m
    while i or j:
        kind, di, dj = back[i][j]
        links.append(Link(kind, tuple(range(a0 + i - di, a0 + i)),
                          tuple(range(b0 + j - dj, b0 + j)), d[i][j] - d[i - di][j - dj]))
        i, j = i - di, j - dj
    links.reverse()
    return links


def align_words(src: list[Word], tgt: list[Word], model: CostModel) -> list[Link]:
    """Align two word sequences. Runs of identical words are anchors found with
    difflib; the gaps between them are aligned with weighted Needleman-Wunsch,
    so a line where every word differs is still aligned as a whole."""
    links: list[Link] = []
    a = b = 0
    matcher = SequenceMatcher(None, src, tgt, autojunk=False)
    for block in matcher.get_matching_blocks():
        links.extend(_nw(src, tgt, a, block.a, b, block.b, model))
        links.extend(Link("sub", (block.a + k,), (block.b + k,), 0.0)
                     for k in range(block.size))
        a, b = block.a + block.size, block.b + block.size
    return links


def similarity(links: list[Link], n_src: int, n_tgt: int) -> float:
    """Share of all words (both sides) that sit in close links."""
    total = n_src + n_tgt
    if not total:
        return 0.0
    covered = sum(len(k.src) + len(k.tgt) for k in links if k.close)
    return covered / total
