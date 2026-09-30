from collections import Counter

from diff2dict.align import BOUNDARY
from diff2dict.mine import Params, generalise, mine, ngram_counts, prepare, split_holdout

QUIET = dict(log=lambda *_: None)
NO_PAUSE = dict(ask=lambda _: "")


def run(sample_lines, sample_classes, **kw):
    params = Params(holdout=0, **kw)
    return mine(*sample_lines, sample_classes, params, **QUIET)


def rule_keys(result):
    return {(("".join(r.x), "".join(r.y), r.left, r.right), r.direction)
            for r in result.char_rules if r.accepted}


def test_sample_char_rules(sample_lines, sample_classes):
    res = run(sample_lines, sample_classes)
    assert rule_keys(res) == {
        (("a", "aa", None, None), "both"),         # regular and reversible
        (("e", "", None, BOUNDARY), "fwd"),        # word-final deletion, one-way
        (("e", "", "o", None), "fwd"),             # merger oe -> o, one-way
    }
    assert res.converged


def test_sample_word_pairs(sample_lines, sample_classes):
    res = run(sample_lines, sample_classes)
    genuine = {(w.source, w.target, w.kind) for w in res.word_pairs if w.genuine}
    assert genuine == {("pin", "hunu", "substitution"), ("ropilo", "ropi lo", "split"),
                       ("ti", "", "deletion")}
    regular = {(w.source, w.target) for w in res.word_pairs if w.kind == "regular"}
    assert {("kala", "kaalaa"), ("tone", "ton"), ("woerd", "word"), ("kale", "kaal"),
            ("maʼa", "maaʼaa"), ("n̄ata", "n̄aataa")} <= regular
    # Identical pairs are never listed.
    assert not any(w.source == w.target for w in res.word_pairs)
    pin = next(w for w in res.word_pairs if w.source == "pin")
    assert (pin.count, pin.direction) == (5, "both")
    # Case folding: "Pin" and "pin" count as one pair, with both casings seen.
    assert set(pin.casings) == {("Pin", "Hunu"), ("pin", "hunu")}


def test_sample_blank_marker_and_unrelated_lines(sample_lines, sample_classes):
    res = run(sample_lines, sample_classes)
    assert res.n_blank == 3                       # blank target, <range>, both blank
    assert [line.number for line, _ in res.skipped] == [15]
    assert res.n_lines == 24
    lines_seen = {n for w in res.word_pairs for n in w.lines}
    assert not lines_seen & {13, 14, 15, 21}


def test_sample_punctuation(sample_lines, sample_classes):
    res = run(sample_lines, sample_classes)
    assert {(p.source, p.target, p.direction) for p in res.punct_pairs} == {
        ("“", "«", "both"), ("”", "»", "both")}


def test_min_count_drops_rare_rules(sample_lines, sample_classes):
    res = run(sample_lines, sample_classes, min_count=10)
    assert {k[0][:2] for k in rule_keys(res)} == {("a", "aa"), ("e", "")}
    assert not any(w.direction for w in res.word_pairs)


def test_review_rejecting_a_rule_changes_word_pairs(sample_lines, sample_classes, tmp_path):
    review = tmp_path / "rules.csv"
    asked = []
    mine(*sample_lines, sample_classes, Params(holdout=0, review=review),
         **QUIET, **NO_PAUSE)                          # first run writes the file
    text = review.read_text(encoding="utf-8-sig")
    rows = text.splitlines()
    rows = [r + "0" if r.startswith("a,aa,") else r for r in rows]
    review.write_text("\n".join(rows) + "\n", encoding="utf-8-sig")
    params = Params(holdout=0, review=review)
    res = mine(*sample_lines, sample_classes, params, ask=asked.append, **QUIET)
    assert not asked                                   # existing file: no pause
    assert ("a", "aa", None, None) not in {k[0] for k in rule_keys(res)}
    kala = next(w for w in res.word_pairs if w.source == "kala")
    assert kala.kind == "substitution"


def test_review_file_leaves_keep_empty(tmp_path):
    from diff2dict.mine import CharRule, review
    path = tmp_path / "rules.csv"
    rules = [CharRule(("a",), ("e",), None, None, 3, 0.5, 0.5, ""),
             CharRule(("o",), ("u",), None, None, 9, 1.0, 1.0, "both")]
    # Nothing is rejected or forced until the user says so, so a rule that is
    # unreliable in the first pass can still be accepted in a later one.
    assert review(path, rules, ask=lambda _: "") == (set(), set())
    rows = path.read_text(encoding="utf-8-sig").splitlines()
    rows[2] = rows[2] + "0"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8-sig")
    assert review(path, rules, ask=lambda _: "") == ({(("o",), ("u",), None, None)}, set())


def test_review_pauses_when_file_is_new(sample_lines, sample_classes, tmp_path):
    asked = []
    params = Params(holdout=0, review=tmp_path / "rules.csv")
    mine(*sample_lines, sample_classes, params, ask=asked.append, **QUIET)
    assert len(asked) == 1


def test_generalise_prefers_least_specific_reliable_context():
    # e deleted after n and l, always word-finally; e also occurs mid-word.
    src = Counter({tuple("tone"): 3, tuple("mele"): 3, tuple("teka"): 5})
    tgt = Counter({tuple("ton"): 3, tuple("mel"): 3, tuple("teka"): 5})
    contexts = Counter({("n", BOUNDARY, "n", BOUNDARY): 3, ("l", BOUNDARY, "l", BOUNDARY): 3})
    rules = generalise(tuple("e"), (), contexts, ngram_counts(src), ngram_counts(tgt),
                       Params(), {})
    assert [(r.left, r.right, r.count, r.direction) for r in rules] == [
        (None, BOUNDARY, 6, "fwd")]


def test_generalise_reports_unreliable_rules():
    src = Counter({tuple("ab"): 2, tuple("ac"): 8})
    tgt = Counter({tuple("eb"): 2, tuple("ec"): 8})
    rules = generalise(("a",), ("e",), Counter({(BOUNDARY, "x", BOUNDARY, "x"): 1,
                                                (BOUNDARY, "y", BOUNDARY, "y"): 1}),
                       ngram_counts(src), ngram_counts(tgt), Params(), {})
    assert [(r.left, r.right, r.direction) for r in rules] == [(None, None, "")]


def test_holdout_is_deterministic(sample_lines, sample_classes):
    lines, _ = prepare(*sample_lines, sample_classes)
    a = split_holdout(lines, 0.25, seed=3)
    b = split_holdout(lines, 0.25, seed=3)
    assert [x.number for x in a[1]] == [x.number for x in b[1]]
    assert len(a[1]) == round(len(lines) * 0.25)
    assert not {x.number for x in a[0]} & {x.number for x in a[1]}


def test_heldout_lines_are_not_mined(sample_lines, sample_classes):
    res = mine(*sample_lines, sample_classes, Params(holdout=0.3), **QUIET)
    held = {line.number for line in res.held}
    assert held
    assert not held & {n for w in res.word_pairs for n in w.lines}
    assert res.evaluation["forward"] >= res.evaluation["baseline"]


def test_uneven_files_are_padded(sample_classes):
    lines, blank = prepare(["kala mana", "tapa"], ["kaalaa maanaa"], sample_classes)
    assert [x.number for x in lines] == [1] and blank == 1


def test_pair_explained_only_in_reverse_is_genuine():
    from diff2dict.align import Link
    from diff2dict.mine import Line, classify_pairs
    from diff2dict.teckit import Converter, MapRule
    rule = MapRule("char", ("e",), ("a",), None, None, None, None, "bwd", 5, 0.2, 1.0, "e>a")
    line = Line(1, "men", "man", ["men"], ["man"], [tuple("men")], [tuple("man")], [], [])
    pairs, _ = classify_pairs([(line, [Link("sub", (0,), (0,), 0.3)])],
                              Converter([rule], True), Params(min_count=1))
    assert [(p.source, p.target, p.kind) for p in pairs] == [("men", "man", "substitution")]


def test_line_count_uses_longer_file(sample_classes):
    res = mine(["kala"], ["kaalaa", "", "tapa"], sample_classes, Params(holdout=0), **QUIET)
    assert res.n_lines == 3


def test_p_bwd_uses_target_context():
    # b -> p after a, but a itself became e, so no target p follows an a.
    src = Counter({tuple("abx"): 3, tuple("aby"): 3, tuple("bo"): 6})
    tgt = Counter({tuple("epx"): 3, tuple("epy"): 3, tuple("po"): 6})
    contexts = Counter({("a", "x", "e", "x"): 3, ("a", "y", "e", "y"): 3})
    rules = generalise(("b",), ("p",), contexts, ngram_counts(src), ngram_counts(tgt),
                       Params(), {})
    assert [(r.left, r.right, r.p_fwd, r.p_bwd, r.direction) for r in rules] == [
        ("a", None, 1.0, 0.0, "fwd")]
