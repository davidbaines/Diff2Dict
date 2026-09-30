import subprocess

from diff2dict import teckit
from diff2dict.align import BOUNDARY
from diff2dict.mine import CharRule
from diff2dict.teckit import (NONWORD, WORDCHAR, Converter, Layout, MapRule, char_map_rules,
                              compile_map, find_tools, render_rules, run_txtconv, write_map)
from diff2dict.unicodetools import BOTH

from .conftest import teckit_missing


def rule(x, y, left=None, right=None, direction="both"):
    return CharRule(tuple(x), tuple(y), left, right, 5, 1.0, 1.0, direction)


def char(lhs, rhs, pre=None, post=None, direction="both", count=5):
    return MapRule("char", tuple(lhs), tuple(rhs), pre, post, pre, post, direction, count,
                   1.0, 1.0, f"{lhs}>{rhs}")


def test_fold_empty_sides_into_context():
    rules, unmapped = char_map_rules([
        rule("e", "", "o", None, "fwd"),          # oe -> o
        rule("", "h", "s", None),                 # s -> sh
        rule("", "h", None, "o"),                 # o -> ho
        rule("e", "", None, BOUNDARY, "both"),    # e -> nothing at the end: one way only
        rule("", "x", None, BOUNDARY),            # cannot be written
    ])
    got = {(r.lhs, r.rhs, r.pre, r.post, r.direction) for r in rules}
    assert got == {
        (("o", "e"), ("o",), None, None, "fwd"),
        (("s",), ("s", "h"), None, None, "both"),
        (("o",), ("h", "o"), None, None, "both"),
        (("e",), (), None, BOUNDARY, "fwd"),
    }
    assert len(unmapped) == 1


def test_converter_prefers_specific_rules_and_does_not_rescan():
    rules = [char("a", "b"), char("a", "c", pre="x"), char("b", "d")]
    fwd = Converter(rules, forward=True)
    assert fwd.convert_word(tuple("xab"))[0] == tuple("xcd")   # a after x -> c; b -> d once
    assert fwd.convert_word(tuple("aa"))[0] == tuple("bb")      # output b is not rescanned


def test_converter_respects_direction_and_word_rules():
    rules = [MapRule("word", tuple("pin"), tuple("hunu"), BOUNDARY, BOUNDARY, BOUNDARY,
                     BOUNDARY, "both", 5, 1, 1, "pin"),
             char("oe", "o", direction="fwd"), char("a", "aa")]
    fwd, bwd = Converter(rules, True), Converter(rules, False)
    toks = [tuple("pin"), tuple("woerd"), tuple("kala")]
    assert fwd.convert_tokens(toks) == [tuple("hunu"), tuple("word"), tuple("kaalaa")]
    assert bwd.convert_tokens([tuple("hunu"), tuple("word"), tuple("kaalaa")]) == [
        tuple("pin"), tuple("word"), tuple("kala")]


def test_render_rules_syntax_and_case_variants():
    lines = render_rules([char("a", "aa", post=None)], Layout(upper=True))
    rules = [x.split(";")[0].strip() for x in lines if x and not x.startswith(";")]
    assert rules == [
        "U+0061 <> U+0061 U+0061",
        # Title: A -> Aa only at the start of a word and before a non-capital.
        "U+0041 / ^[W] _ ^[U] <> U+0041 U+0061 / ^[W] _ ^[U]",
        "U+0041 <> U+0041 U+0041"]                              # UPPER


def test_upper_variant_kept_for_single_grapheme_rules():
    lines = render_rules([char("e", "i")], Layout(upper=True))
    rules = [x.split(";")[0].strip() for x in lines if x and not x.startswith(";")]
    assert rules == ["U+0065 <> U+0069",
                     "U+0045 / ^[W] _ ^[U] <> U+0049 / ^[W] _ ^[U]",   # Title
                     "U+0045 <> U+0049"]                              # UPPER, inside BED


def test_boundary_allows_both_characters():
    word = MapRule("word", tuple("dog"), tuple("hound"), BOUNDARY, BOUNDARY, BOUNDARY,
                   BOUNDARY, "both", 5, 1, 1, "dog")
    text = "\n".join(render_rules([word], Layout(both=True)))
    assert "/ (^[WB] | ^[W] [B]) _ (^[WB] | [B] ^[W]) <>" in text
    assert "/ ^[W] _ ^[W] <>" in "\n".join(render_rules([word], Layout()))


def test_converter_uses_teckit_precedence():
    # TECkit ranks by match length before context length.
    rules = [char("a", "x", pre="b", post="c"), char("ab", "y")]
    assert Converter(rules, True).convert_word(tuple("bab"))[0] == tuple("by")
    # The ^[M] guard counts as context, so the guarded rule wins a tie.
    rules = [char("a", "x", post=BOUNDARY), char("a", "z", pre="b")]
    assert Converter(rules, True, Layout()).convert_word(tuple("ba"))[0] == tuple("bx")
    assert Converter(rules, True, Layout(guard=True)).convert_word(tuple("ba"))[0] == tuple("bz")


def test_render_context_and_empty_rhs():
    lines = render_rules([char("e", "", post=BOUNDARY, direction="fwd"),
                          char("a", "e", pre="b", post="c")], Layout(guard=True, upper=True))
    text = "\n".join(lines)
    assert "U+0065 / _ ^[W] >   ;" in text
    assert "U+0061 / (U+0062 | U+0042) _ (U+0063 | U+0043) ^[M] <>" in text


def test_punct_rules_for_both_class_characters():
    from diff2dict.mine import PunctPair
    rules = teckit.punct_map_rules([PunctPair("’", "”", 5, 1.0, 0.5, "fwd", [])],
                                   {"’": BOTH})
    assert {(r.pre, r.post, r.direction) for r in rules} == {
        (NONWORD, None, "fwd"), (WORDCHAR, NONWORD, "fwd")}


def test_write_map_classes(tmp_path):
    path = tmp_path / "r.map"
    write_map(path, [char("a", "aa")], {"a", "b", "c", "e"}, set(), set(), "L", "R", "l", "r")
    text = path.read_text(encoding="utf-8-sig")
    assert "Class [W] = ( U+0061..U+0063 U+0065 )" in text
    assert "Class [M]" not in text
    assert text.startswith("; Generated")


@teckit_missing
def test_sample_map_compiles_and_converts(tmp_path, sample_lines, sample_classes):
    from diff2dict.mine import Params, mine
    src, tgt = sample_lines
    res = mine(src, tgt, sample_classes, Params(holdout=0), log=lambda *_: None)
    map_path, tec = tmp_path / "rules.map", tmp_path / "rules.tec"
    write_map(map_path, res.map_rules, res.word_chars, res.mark_chars, res.both_chars,
              "A", "B", "a", "b")
    compiler, txtconv = find_tools()
    ok, message = compile_map(compiler, map_path, tec)
    assert ok, message
    out = run_txtconv(txtconv, tec, src, reverse=False)
    # Every line converts exactly, all-caps lines included, except the deleted
    # particle 'ti' (word deletions are not mapped) and the blank, <range> and
    # unrelated lines.
    expect_diff = {5, 10, 13, 14, 15}
    assert len(out) == len(tgt) == 24
    for n, (got, want) in enumerate(zip(out, tgt), start=1):
        if n not in expect_diff:
            assert got == want, (n, got, want)


@teckit_missing
def test_bad_map_fails_to_compile(tmp_path):
    bad = tmp_path / "bad.map"
    bad.write_text("pass(Unicode)\n<> U+0061\n", encoding="utf-8")
    compiler, _ = find_tools()
    ok, _ = compile_map(compiler, bad, tmp_path / "bad.tec")
    assert not ok


def test_teckit_evaluate_without_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(teckit, "find_tools", lambda: (None, None))
    assert "not found" in teckit.teckit_evaluate(tmp_path / "x.map", [], {})["teckit"]


def test_subprocess_is_not_shell(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 1, "", "err")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg = compile_map("tc", tmp_path / "a b.map", tmp_path / "a.tec")
    assert not ok and msg == "err"
    assert isinstance(seen["cmd"], list) and "shell" not in seen["kw"]


@teckit_missing
def test_word_boundaries_match_the_tokeniser(tmp_path):
    word = MapRule("word", tuple("dog"), tuple("hound"), BOUNDARY, BOUNDARY, BOUNDARY,
                   BOUNDARY, "both", 5, 1, 1, "dog")
    final = char("e", "", post=BOUNDARY, direction="fwd")
    map_path, tec = tmp_path / "b.map", tmp_path / "b.tec"
    letters = set("abcdefghijklmnopqrstuvwxyz")
    write_map(map_path, [word, final], letters, set(), {"'", "’"}, "A", "B", "a", "b")
    compiler, txtconv = find_tools()
    ok, message = compile_map(compiler, map_path, tec)
    assert ok, message
    got = run_txtconv(txtconv, tec, ["dog's 'dog' dog’ dog, mele's mele' mele"], False)
    # dog's and mele's are single words to the tokeniser, so they are left alone.
    assert got == ["dog's 'hound' hound’ hound, mele's mel' mel"]


def test_split_punctuation_swap_is_not_reported_one_way():
    from diff2dict.mine import PunctPair
    rules = teckit.punct_map_rules([PunctPair("\u2019", "\u201d", 50, 1.0, 1.0, "both", [])],
                                   {"\u2019": BOTH})
    assert {r.direction for r in rules} == {"fwd", "bwd"}
    assert teckit.oneway_rows(rules, [], 0.95) == []


def test_punct_contexts_fall_back_to_suggested_class(sample_classes):
    from diff2dict.mine import Params, mine
    classes = {g: c for g, c in sample_classes.items() if g != "'"}   # no row for '
    res = mine(["dog's x' y' z'"] * 3, ["dog's x\u201d y\u201d z\u201d"] * 3,
               classes, Params(holdout=0), log=lambda *_: None)
    # The forward rule matches ', so it needs contexts to spare the one in dog's.
    fwd = [r for r in res.map_rules if r.kind == "punct" and r.direction in ("fwd", "both")]
    assert fwd and all(r.lhs == ("'",) and r.pre is not None for r in fwd)


def test_rules_with_both_characters_are_guarded():
    rules, unmapped = char_map_rules([rule("'", "", "n", None, "fwd"),     # don't -> dont
                                      rule("a", "e", "'", None, "fwd")],   # context is '
                                     both={"'"})
    assert [(r.lhs, r.rhs, r.pre, r.post) for r in rules] == [
        (("n", "'"), ("n",), None, WORDCHAR)]
    assert len(unmapped) == 1


@teckit_missing
def test_guarded_apostrophe_rule_spares_quotes(tmp_path):
    rules, _ = char_map_rules([rule("'", "", "n", None, "fwd")], both={"'"})
    map_path, tec = tmp_path / "q.map", tmp_path / "q.tec"
    write_map(map_path, rules, set("adehmnot"), set(), {"'"}, "A", "B", "a", "b")
    compiler, txtconv = find_tools()
    assert compile_map(compiler, map_path, tec)[0]
    assert run_txtconv(txtconv, tec, ["don't 'amen' amen'"], False) == ["dont 'amen' amen'"]


def test_no_title_variant_when_a_side_starts_with_a_both_character():
    rules, _ = char_map_rules([rule("", "'", None, "t", "bwd")], both={"'"})   # t < 't
    lines = render_rules(rules, Layout(both=True, upper=True))
    text = "\n".join(x for x in lines if x and not x.startswith(";"))
    assert "U+0074 < U+0027 U+0074 / [W] _" in text
    assert "^[U]" not in text                  # no Title variant, so no T < 't


def test_specific_rule_title_variant_wins_the_tie():
    rules, _ = char_map_rules([
        CharRule(("a",), ("a", "a"), None, None, 50, 1.0, 1.0, "both"),
        CharRule(("a",), ("e",), None, "p", 5, 1.0, 0.1, "fwd"),
        CharRule(("a",), ("o",), BOUNDARY, None, 5, 1.0, 0.1, "fwd")])
    assert [r.text for r in rules][-1] == "a \u2192 aa"      # least specific last
    lines = [x.split(";")[0].strip() for x in render_rules(rules, Layout(upper=True))
             if x and not x.startswith(";")]
    # Each rule keeps its own Title variant, written before the general one.
    assert "U+0041 / ^[W] _ U+0070 > U+0045" in lines
    assert "U+0041 / ^[W] _ ^[U] > U+004F" in lines
    assert lines.index("U+0041 / ^[W] _ U+0070 > U+0045") < lines.index(
        "U+0041 / ^[W] _ ^[U] > U+004F")
    # The general rule's Title variant has the same match as a -> o's, so it
    # is kept only in reverse.
    assert "U+0041 < U+0041 U+0061 / ^[W] _ ^[U]" in lines


def test_rare_observed_casing_does_not_hijack_title_case():
    from collections import Counter
    from diff2dict.mine import WordPair
    wp = WordPair("dog", "hound", "substitution", 6, 6, 6, 1.0, 1.0, "both",
                  casings=Counter({("dog", "hound"): 5, ("dog", "Hound"): 1}))
    lines = [x.split(";")[0].strip() for x in render_rules(teckit.word_map_rules([wp]), Layout())
             if x and not x.startswith(";")]
    seq = teckit._seq
    ctx = " / ^[W] _ ^[W]"
    assert f"{seq(tuple('Dog'))}{ctx} <> {seq(tuple('Hound'))}{ctx}" in lines
    assert not any(line.startswith(seq(tuple("dog"))) and "U+0048" in line for line in lines)


def test_frequent_observed_casing_is_used():
    from collections import Counter
    from diff2dict.mine import WordPair
    wp = WordPair("lord", "yahweh", "substitution", 9, 9, 9, 1.0, 1.0, "both",
                  casings=Counter({("LORD", "Yahweh"): 9}))
    lines = [x.split(";")[0].strip() for x in render_rules(teckit.word_map_rules([wp]), Layout())
             if x and not x.startswith(";")]
    seq, ctx = teckit._seq, " / ^[W] _ ^[W]"
    assert f"{seq(tuple('LORD'))}{ctx} <> {seq(tuple('Yahweh'))}{ctx}" in lines
