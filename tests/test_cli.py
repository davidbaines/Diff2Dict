import shutil

import openpyxl

from diff2dict import report
from diff2dict.cli import main

from .conftest import SAMPLE


def copy_sample(tmp_path):
    for name in ("source.txt", "target.txt"):
        shutil.copy(SAMPLE / name, tmp_path / name)
    return tmp_path / "source.txt", tmp_path / "target.txt"


def test_run_without_chars_generates_them_and_stops(tmp_path):
    src, tgt = copy_sample(tmp_path)
    chars = tmp_path / "chars.csv"
    assert main(["run", str(src), str(tgt), "--chars", str(chars),
                 "--out", str(tmp_path / "r.xlsx")]) == 2
    assert chars.exists() and not (tmp_path / "r.xlsx").exists()


def test_full_run_writes_all_outputs(tmp_path):
    src, tgt = copy_sample(tmp_path)
    chars, out, map_path = tmp_path / "chars.csv", tmp_path / "r.xlsx", tmp_path / "r.map"
    assert main(["scan-chars", str(src), str(tgt), "--chars", str(chars)]) == 0
    assert main(["run", str(src), str(tgt), "--chars", str(chars), "--out", str(out),
                 "--map", str(map_path), "--holdout", "0"]) == 0
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["Summary", "CharPairs", "WordPairs", "Lexicon", "PunctPairs",
                             "CaseOnly", "SkippedLines"]
    word_pairs = [r[:3] for r in wb["WordPairs"].iter_rows(min_row=2, values_only=True)]
    assert ("pin", "hunu", "substitution") in word_pairs
    skipped = [r[0] for r in wb["SkippedLines"].iter_rows(min_row=2, values_only=True)]
    assert skipped == [15]
    assert map_path.exists() and (tmp_path / "oneway_rules.csv").exists()
    oneway = (tmp_path / "oneway_rules.csv").read_text(encoding="utf-8-sig")
    assert "e → ∅ / o _" in oneway


def test_workbook_keeps_formula_like_text_as_text(tmp_path):
    wb = openpyxl.Workbook()
    report._sheet(wb, "T", ["a"], [["=1+1"], ["-"], ["bad\x01char"]])
    wb.save(tmp_path / "t.xlsx")
    ws = openpyxl.load_workbook(tmp_path / "t.xlsx")["T"]
    assert [c.value for c in ws["A"]][1:] == ["=1+1", "-", "bad�char"]
    assert ws["A2"].data_type == "s"
