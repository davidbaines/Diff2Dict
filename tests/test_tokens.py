import pytest

from diff2dict.tokens import tokenize
from diff2dict.unicodetools import BOTH, PUNCT, WORD

CLASSES = {"'": BOTH, "-": BOTH, ".": PUNCT, ",": PUNCT, "“": PUNCT}


@pytest.mark.parametrize("line, words, punct", [
    ("don't stop.", ["don't", "stop"], ["."]),
    ("'tik' rum", ["tik", "rum"], ["'", "'"]),
    ("rock'n'roll", ["rock'n'roll"], []),
    ("dogs' bones", ["dogs", "bones"], ["'"]),
    ("well-known -x", ["well-known", "x"], ["-"]),
    ("maʼa", ["maʼa"], []),
    ("n̄ata,sol", ["n̄ata", "sol"], [","]),
    ("a b\tc", ["a", "b", "c"], []),
    ("''", [], ["'", "'"]),
    ("", [], []),
])
def test_tokenize(line, words, punct):
    assert tokenize(line, CLASSES) == (words, punct)


def test_user_class_overrides_suggestion():
    assert tokenize("a.b", {".": WORD}) == (["a.b"], [])
