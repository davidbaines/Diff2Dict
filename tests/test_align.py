from diff2dict.align import BOUNDARY, CostModel, align_words, similarity
from diff2dict.mine import chunks


def w(text):
    return tuple(text)


def test_distance_basics():
    m = CostModel()
    assert m.distance(w("kala"), w("kala")) == 0.0
    assert m.distance(w("pin"), w("hunu")) > 0.5
    assert 0 < m.distance(w("kala"), w("kaalaa")) <= 0.5


def test_learned_rule_makes_words_cheap():
    plain = CostModel()
    ruled = CostModel([(w("a"), w("aa"), None, None)])
    assert ruled.distance(w("tapa"), w("taapaa")) < plain.distance(w("tapa"), w("taapaa"))
    assert ruled.distance(w("tapa"), w("taapaa")) < 0.1


def test_rule_context_is_respected():
    ruled = CostModel([(w("e"), (), None, BOUNDARY)])   # e deleted word-finally only
    assert ruled.distance(w("tone"), w("ton")) < 0.1
    assert ruled.distance(w("teon"), w("ton")) > 0.1


def test_insertion_rule_transition():
    ruled = CostModel([((), w("h"), "s", None)])     # insert h after s
    assert ruled.distance(w("sop"), w("shop")) < 0.1
    assert ruled.distance(w("top"), w("thop")) > 0.1


def test_chunks_doubling_reads_as_a_to_aa():
    m = CostModel()
    rules = chunks(w("kala"), w("kaalaa"), m.align(w("kala"), w("kaalaa")))
    assert [r[:2] for r in rules] == [(w("a"), w("aa"))] * 2


def test_chunks_insertion_plus_deletion_not_two_substitutions():
    m = CostModel()
    rules = chunks(w("kale"), w("kaal"), m.align(w("kale"), w("kaal")))
    assert (w("a"), w("aa")) in [r[:2] for r in rules]
    assert (w("e"), (), "l", BOUNDARY, "l", BOUNDARY) in rules


def test_chunks_merger_keeps_empty_side_with_context():
    m = CostModel()
    assert chunks(w("woerd"), w("word"), m.align(w("woerd"), w("word"))) == [
        (w("e"), (), "o", "r", "o", "r")]


def test_chunks_record_target_context():
    m = CostModel()
    # a -> e next to b -> p: in the target, p follows e, not a.
    rules = chunks(w("lab"), w("lep"), m.align(w("lab"), w("lep")))
    assert rules == [(w("ab"), w("ep"), "l", BOUNDARY, "l", BOUNDARY)]
    rules = chunks(w("labo"), w("lepo"), CostModel([(w("a"), w("e"), None, None)]).align(
        w("labo"), w("lepo")))
    assert (w("b"), w("p"), "a", "o", "e", "o") in rules


def test_chunks_skip_long_edits():
    m = CostModel()
    assert chunks(w("abcdefg"), w("xyzwv"), m.align(w("abcdefg"), w("xyzwv"))) == []


def words(text):
    return [w(x) for x in text.split()]


def test_align_words_split_insert_and_substitution():
    m = CostModel()
    links = align_words(words("sama ropilo ti pin"), words("sama ropi lo hunu"), m)
    kinds = [(k.kind, k.src, k.tgt) for k in links]
    assert ("split", (1,), (1, 2)) in kinds
    assert ("del", (2,), ()) in kinds
    assert ("sub", (3,), (3,)) in kinds


def test_deleted_word_is_not_glued_to_neighbour():
    m = CostModel([(w("a"), w("aa"), None, None)])
    links = align_words(words("ti kala"), words("kaalaa"), m)
    assert [k.kind for k in links] == ["del", "sub"]


def test_similarity():
    m = CostModel()
    same = align_words(words("a b c"), words("a b c"), m)
    assert similarity(same, 3, 3) == 1.0
    unrelated = align_words(words("rum tik sol"), words("wiru nopa kel"), m)
    assert similarity(unrelated, 3, 3) < 0.7
    assert similarity([], 0, 0) == 0.0
