import shutil

import openpyxl
import pytest

from diff2dict import report, teckit
from diff2dict.cli import _derive_paths, build_parser, main, select_files

from .conftest import SAMPLE, teckit_missing


def copy_sample(tmp_path):
    for name in ("source.txt", "target.txt"):
        shutil.copy(SAMPLE / name, tmp_path / name)
    return tmp_path / "source.txt", tmp_path / "target.txt"


def project(tmp_path, name="proj"):
    folder = tmp_path / name
    folder.mkdir()
    src, tgt = copy_sample(tmp_path)
    return folder, src, tgt


def base_args(folder, src, tgt):
    return [str(folder), str(src), str(tgt), "--holdout", "0"]


def teckit_dir(folder):
    return folder / "teckit"


def test_check_chars_without_file_generates_them_and_stops(tmp_path):
    folder, src, tgt = project(tmp_path)
    code = main(base_args(folder, src, tgt) + ["--check-chars"], ask=lambda _: "")
    assert code == 2
    td = teckit_dir(folder)
    assert (td / "proj_chars.csv").exists() and not (td / "proj.xlsx").exists()


def test_check_chars_with_file_pauses_then_runs(tmp_path):
    folder, src, tgt = project(tmp_path)
    main(base_args(folder, src, tgt) + ["--check-chars"], ask=lambda _: "")  # writes chars
    asked = []
    code = main(base_args(folder, src, tgt) + ["--check-chars"],
                ask=lambda p: asked.append(p) or "")
    assert code == 0 and asked and (teckit_dir(folder) / "proj.xlsx").exists()


def test_default_run_generates_chars_and_completes(tmp_path):
    folder, src, tgt = project(tmp_path)
    assert main(base_args(folder, src, tgt)) == 0
    td = teckit_dir(folder)
    assert (td / "proj_chars.csv").exists()
    wb = openpyxl.load_workbook(td / "proj.xlsx")
    assert wb.sheetnames == ["Summary", "CharPairs", "WordPairs", "Lexicon", "PunctPairs",
                             "CaseOnly", "SkippedLines"]
    word_pairs = [r[:3] for r in wb["WordPairs"].iter_rows(min_row=2, values_only=True)]
    assert ("pin", "hunu", "substitution") in word_pairs
    skipped = [r[0] for r in wb["SkippedLines"].iter_rows(min_row=2, values_only=True)]
    assert skipped == [15]
    assert (td / "proj.map").exists() and (td / "proj_oneway.csv").exists()
    oneway = (td / "proj_oneway.csv").read_text(encoding="utf-8-sig")
    assert "e → ∅ / o _" in oneway


def test_stem_is_the_folder_name(tmp_path):
    folder, src, tgt = project(tmp_path, name="British2American")
    assert main(base_args(folder, src, tgt)) == 0
    for suffix in (".map", ".xlsx", ".tec", "_oneway.csv", "_chars.csv"):
        assert (folder / "teckit" / f"British2American{suffix}").exists()


def test_derive_paths_folder_layout(tmp_path):
    folder, src, tgt = project(tmp_path)
    args = build_parser().parse_args([str(folder), str(src), str(tgt)])
    _derive_paths(args)
    td = folder / "teckit"
    assert args.out_dir == td
    assert args.map == td / "proj.map" and args.chars == td / "proj_chars.csv"
    assert args.input_folder == folder / "input"
    assert args.output_folder == folder / "output"


def test_missing_folder_errors(tmp_path):
    src, tgt = copy_sample(tmp_path)
    with pytest.raises(SystemExit):
        main([str(tmp_path / "nope"), str(src), str(tgt), "--holdout", "0"])


def test_select_files_by_extension_and_all_text(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "b.sfm").write_text("\\v 1 world", encoding="utf-8")
    (tmp_path / "c.bin").write_bytes(b"\x00\x01\x02binary")
    assert [p.name for p in select_files(tmp_path, "txt")] == ["a.txt"]
    assert [p.name for p in select_files(tmp_path, ".sfm")] == ["b.sfm"]
    # All-text markers take every text file but skip the binary one.
    for marker in ("*", ".*", "*.*"):
        assert [p.name for p in select_files(tmp_path, marker)] == ["a.txt", "b.sfm"]


def test_input_without_teckit_skips_conversion(tmp_path, monkeypatch):
    monkeypatch.setattr(teckit, "find_tools", lambda: (None, None))
    folder, src, tgt = project(tmp_path)
    (folder / "input").mkdir()
    (folder / "input" / "one.txt").write_text("kala\n", encoding="utf-8")
    assert main(base_args(folder, src, tgt)) == 0
    assert (teckit_dir(folder) / "proj.map").exists()
    assert not (folder / "output").exists()


@teckit_missing
def test_full_pipeline_converts_folder_input_to_output(tmp_path):
    folder, src, tgt = project(tmp_path)
    (folder / "input").mkdir()
    (folder / "input" / "one.txt").write_text((SAMPLE / "source.txt").read_text(encoding="utf-8"),
                                              encoding="utf-8")
    assert main(base_args(folder, src, tgt)) == 0
    assert (teckit_dir(folder) / "proj.tec").exists()
    converted = (folder / "output" / "one.txt").read_text(encoding="utf-8").splitlines()
    target = (SAMPLE / "target.txt").read_text(encoding="utf-8").splitlines()
    # Line 1 (kala -> kaalaa) is converted by the compiled map.
    assert converted[0] == target[0]


def test_workbook_keeps_formula_like_text_as_text(tmp_path):
    wb = openpyxl.Workbook()
    report._sheet(wb, "T", ["a"], [["=1+1"], ["-"], ["bad\x01char"]])
    wb.save(tmp_path / "t.xlsx")
    ws = openpyxl.load_workbook(tmp_path / "t.xlsx")["T"]
    assert [c.value for c in ws["A"]][1:] == ["=1+1", "-", "bad�char"]
    assert ws["A2"].data_type == "s"
