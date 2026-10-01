"""Command line: `diff2dict SOURCE TARGET [options]`.

Mines the differences between two line-aligned texts, writes the TECkit map,
the workbook and the one-way report, compiles the map to a .tec, and can convert
a folder of text files with it.
"""

import argparse
import shlex
import sys
from collections.abc import Callable
from pathlib import Path

from . import __version__, report, teckit
from .mine import Params, mine
from .unicodetools import (CLASSES, count_graphemes, read_chars_csv, read_lines,
                           write_chars_csv)

ALL_TEXT = {"*", ".*", "*.*"}       # an --input-ext that means every text file
TEXT_PROBE = 8192                   # bytes read to tell text from binary


def scan_chars(src: Path, tgt: Path, chars: Path) -> None:
    counts = count_graphemes(read_lines(src) + read_lines(tgt))
    existing = read_chars_csv(chars) if chars.exists() else {}
    write_chars_csv(chars, counts, existing)


def _class_help(chars: Path) -> str:
    names = ", ".join(CLASSES)
    return (f"Edit the 'class' column of {chars}: each row must be one of {names} "
            "(a unique prefix such as w, p or b works too); leave none blank. "
            "'word' is part of a word, 'punctuation' separates words, and 'both' is "
            "word-forming only between two word characters, as an apostrophe is in "
            "don't.")


def resolve_chars(args: argparse.Namespace, invocation: str,
                  ask: Callable[[str], str]) -> dict[str, str] | None:
    """Return the character classes, or None when the run should stop so the user
    can review a freshly written file."""
    chars = args.chars
    if args.check_chars:
        if not chars.exists():
            scan_chars(args.source, args.target, chars)
            print(f"Wrote {chars} with a suggested class for every character.")
            print(_class_help(chars))
            print("Then run the same command again to continue:")
            print(f"  {invocation}")
            print("(If you launched it with 'uv run', keep that prefix.)")
            return None
        print(f"Found {chars}. {_class_help(chars)}")
        try:
            ask("Press Enter to mine with these classes, or Ctrl-C to stop and edit it: ")
        except (EOFError, OSError):
            pass
    elif not chars.exists():
        scan_chars(args.source, args.target, chars)
        print(f"Wrote {chars} with suggested character classes and used them. "
              "Re-run with --check-chars to review them first.")
    return read_chars_csv(chars)


def _is_text_file(path: Path) -> bool:
    """A file is text when its first bytes hold no NUL and decode as UTF-8. A
    multi-byte character cut at the probe boundary is tolerated."""
    try:
        chunk = path.read_bytes()[:TEXT_PROBE]
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError as e:
        return e.start >= len(chunk) - 3
    return True


def select_files(folder: Path, ext: str) -> list[Path]:
    """Files directly in `folder` (not recursive) that match the extension, or
    every text file when the extension is one of the all-text markers."""
    files = sorted(p for p in folder.iterdir() if p.is_file())
    if ext in ALL_TEXT:
        return [p for p in files if _is_text_file(p)]
    suffix = "." + ext.lower().lstrip(".")
    return [p for p in files if p.suffix.lower() == suffix]


def _validate_conversion(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Check conversion arguments and tools before any mining, so a mistake costs
    nothing. Exits through parser.error."""
    if args.input_ext is None or args.output_folder is None:
        parser.error("--input-folder needs both --input-ext and --output-folder")
    if not args.input_folder.is_dir():
        parser.error(f"input folder not found: {args.input_folder}")
    if args.input_folder.resolve() == args.output_folder.resolve():
        parser.error("the input and output folders must be different")
    compiler, txtconv = teckit.find_tools()
    if not compiler or not txtconv:
        parser.error("conversion needs teckit_compile and txtconv on PATH; "
                     "install the teckit package")


def _convert_folder(args: argparse.Namespace, tec: Path, txtconv: str) -> int:
    """Convert every matching file into the output folder. Returns the number of
    files that failed."""
    files = select_files(args.input_folder, args.input_ext)
    args.output_folder.mkdir(parents=True, exist_ok=True)
    failures = 0
    for path in files:
        try:
            out_lines = teckit.run_txtconv(txtconv, tec, read_lines(path), args.reverse)
            (args.output_folder / path.name).write_text("\n".join(out_lines) + "\n",
                                                         encoding="utf-8")
        except (RuntimeError, OSError) as e:
            failures += 1
            print(f"  failed to convert {path.name}: {e}", file=sys.stderr)
    direction = "backward" if args.reverse else "forward"
    print(f"Converted {len(files) - failures} of {len(files)} files ({direction}) "
          f"to {args.output_folder}.")
    return failures


def run(args: argparse.Namespace, invocation: str, parser: argparse.ArgumentParser,
        ask: Callable[[str], str] = input) -> int:
    convert = args.input_folder is not None
    if convert:
        _validate_conversion(args, parser)

    classes = resolve_chars(args, invocation, ask)
    if classes is None:
        return 2
    src_lines, tgt_lines = read_lines(args.source), read_lines(args.target)
    if len(src_lines) != len(tgt_lines):
        print(f"Warning: {args.source} has {len(src_lines)} lines but {args.target} has "
              f"{len(tgt_lines)}; the shorter file is padded with blank lines.")
    params = Params(args.threshold, args.min_count, args.max_iter, args.reliability,
                    args.holdout, args.seed, args.review)
    result = mine(src_lines, tgt_lines, classes, params)

    lhs = args.lhs_name or args.source.stem
    rhs = args.rhs_name or args.target.stem
    teckit.write_map(args.map, result.map_rules, result.word_chars, result.mark_chars,
                     result.both_chars,
                     lhs, rhs, f"Text as in {args.source.name}", f"Text as in {args.target.name}")
    oneway = args.oneway or args.map.with_name("oneway_rules.csv")
    report.write_oneway_csv(oneway, teckit.oneway_rows(result.map_rules, result.unmapped,
                                                       params.reliability))
    tk = teckit.teckit_evaluate(args.map, result.held, classes)

    ev = result.evaluation
    summary: dict[str, object] = {
        "diff2dict version": __version__,
        "source file": str(args.source),
        "target file": str(args.target),
        "chars file": str(args.chars),
        "map file": str(args.map),
        "threshold": params.threshold,
        "min count": params.min_count,
        "max iterations": params.max_iter,
        "reliability": params.reliability,
        "held-out fraction": params.holdout,
        "random seed": params.seed,
        "review file": str(params.review) if params.review else "",
        "lines in files": result.n_lines,
        "line pairs skipped as blank or markers": result.n_blank,
        "line pairs mined": result.n_train,
        "line pairs held out": result.n_held,
        "mined lines skipped as unrelated": len(result.skipped),
        "iterations run": result.iterations,
        "converged": "yes" if result.converged else "no (hit max iterations)",
        "character rules accepted": sum(1 for r in result.char_rules if r.accepted),
        "genuine word pairs": sum(1 for w in result.word_pairs if w.genuine),
        "map rules (before case variants)": len(result.map_rules),
        "one-way or unmapped rules": len(teckit.oneway_rows(result.map_rules, result.unmapped,
                                                            params.reliability)),
        "held-out lines scored": ev["held_lines"],
    }
    if ev["held_lines"]:
        summary["word accuracy before conversion"] = round(ev["baseline"], 4)
        summary["simulated word accuracy forward"] = round(ev["forward"], 4)
        summary["simulated word accuracy backward"] = round(ev["backward"], 4)
    for k, v in tk.items():
        summary[k.replace("_", " ")] = round(v, 4) if isinstance(v, float) else v
    report.write_workbook(args.out, result, summary)
    print(f"Wrote {args.out}, {args.map} and {oneway}.")
    if ev["held_lines"]:
        print(f"Held-out word accuracy: {ev['baseline']:.3f} before, "
              f"{ev['forward']:.3f} forward, {ev['backward']:.3f} backward (simulated).")

    tec = args.tec or args.map.with_suffix(".tec")
    compiler, txtconv = teckit.find_tools()
    if convert:
        ok, message = teckit.compile_map(compiler, args.map, tec)
        if not ok:
            print(f"teckit_compile failed, so nothing was converted: {message}", file=sys.stderr)
            return 1
        print(f"Compiled {tec}.")
        return 1 if _convert_folder(args, tec, txtconv) else 0

    if compiler:
        ok, message = teckit.compile_map(compiler, args.map, tec)
        print(f"Compiled {tec}." if ok else f"teckit_compile failed: {message}")
    else:
        print("teckit_compile not found, so the .tec was not written. "
              "Install the teckit package to compile it.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="diff2dict", description=(
        "Mine character rules and word substitutions from two line-aligned texts, "
        "write them as a TECkit map, compile it, and optionally convert a folder."))
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("source", type=Path)
    p.add_argument("target", type=Path)
    p.add_argument("--chars", type=Path, default=Path("chars.csv"))
    p.add_argument("--check-chars", action="store_true",
                   help="pause to review the character classes before mining")
    p.add_argument("--out", type=Path, default=Path("result.xlsx"))
    p.add_argument("--map", type=Path, default=Path("rules.map"))
    p.add_argument("--tec", type=Path, default=None,
                   help="compiled table (default: the map path with a .tec suffix)")
    p.add_argument("--oneway", type=Path, default=None,
                   help="one-way rules report (default: oneway_rules.csv next to the map)")

    c = p.add_argument_group("conversion")
    c.add_argument("--input-folder", type=Path, default=None,
                   help="convert the files in this folder (not recursive)")
    c.add_argument("--input-ext", default=None,
                   help="extension to convert, with or without the dot; "
                        "*, .* or *.* means every text file")
    c.add_argument("--output-folder", type=Path, default=None,
                   help="where converted files are written; must differ from the input")
    c.add_argument("-r", "--reverse", action="store_true",
                   help="convert target to source instead of source to target")

    m = p.add_argument_group("mining")
    m.add_argument("--threshold", type=float, default=0.7,
                   help="line similarity below which a pair is skipped (default 0.7)")
    m.add_argument("--min-count", type=int, default=2)
    m.add_argument("--max-iter", type=int, default=5)
    m.add_argument("--reliability", type=float, default=0.95,
                   help="probability a rule must reach in a direction (default 0.95)")
    m.add_argument("--holdout", type=float, default=0.1,
                   help="fraction of line pairs kept back for evaluation (default 0.1)")
    m.add_argument("--seed", type=int, default=1, help="seed for the held-out split")
    m.add_argument("--review", type=Path, default=None,
                   help="pause after the first pass to edit this rules CSV")
    m.add_argument("--lhs-name", default=None)
    m.add_argument("--rhs-name", default=None)
    return p


def main(argv: list[str] | None = None, ask: Callable[[str], str] = input) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    shown = argv if argv is not None else sys.argv[1:]
    invocation = Path(sys.argv[0]).name + " " + " ".join(shlex.quote(a) for a in shown)
    return run(args, invocation, parser, ask)


if __name__ == "__main__":
    sys.exit(main())
