import pytest

from diff2dict.unicodetools import (BOTH, PUNCT, WORD, count_graphemes, graphemes, is_marker,
                                    lower_graphemes, read_chars_csv, read_lines, suggest_class,
                                    write_chars_csv)


def test_read_lines_normalises_and_keeps_line_numbers(tmp_path):
    f = tmp_path / "t.txt"
    # BOM, CRLF, a decomposed e + acute, and U+2028 inside a line.
    f.write_bytes("﻿café\r\na b\r\n\r\nlast".encode("utf-8"))
    assert read_lines(f) == ["café", "a b", "", "last"]


def test_graphemes_keep_combining_marks():
    assert graphemes("n̄a") == ["n̄", "a"]
    assert lower_graphemes("N̄A") == ("n̄", "a")


@pytest.mark.parametrize("g, cls", [
    ("a", WORD), ("é", WORD), ("n̄", WORD), ("7", WORD),
    ("ʼ", WORD),          # modifier letter apostrophe is a letter
    ("'", BOTH), ("’", BOTH), ("-", BOTH),
    (".", PUNCT), ("“", PUNCT), ("=", PUNCT),
])
def test_suggest_class(g, cls):
    assert suggest_class(g) == cls


def test_count_graphemes_ignores_spaces():
    counts = count_graphemes(["a b a", "\tb"])
    assert counts == {"a": 2, "b": 2}


def test_is_marker():
    assert is_marker("<range>")
    assert is_marker("  <range> ")
    assert not is_marker("a <b> c")


def test_chars_csv_round_trip_keeps_edits_and_accents(tmp_path):
    path = tmp_path / "chars.csv"
    counts = count_graphemes(["ǒ n̄ ' ’ ."])   # o with caron, n + macron
    write_chars_csv(path, counts)
    classes = read_chars_csv(path)
    assert classes["ǒ"] == WORD and classes["n̄"] == WORD
    assert classes["'"] == BOTH and classes["."] == PUNCT
    # A second scan keeps a class the user changed.
    write_chars_csv(path, counts, {**classes, "'": WORD})
    assert read_chars_csv(path)["'"] == WORD
    assert "ǒ" in path.read_text(encoding="utf-8-sig")


def test_chars_csv_accepts_abbreviations_and_rejects_nonsense(tmp_path):
    path = tmp_path / "chars.csv"
    path.write_text("char,codepoints,class\na,U+0061,w\n.,U+002E,Punct\n", encoding="utf-8")
    assert read_chars_csv(path) == {"a": WORD, ".": PUNCT}
    path.write_text("char,codepoints,class\na,U+0061,letter\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        read_chars_csv(path)
