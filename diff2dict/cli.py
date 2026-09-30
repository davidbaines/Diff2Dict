"""Command line: `diff2dict scan-chars` and `diff2dict run`."""

import argparse
import sys
from pathlib import Path

from . import __version__, report, teckit
from .mine import Params, mine
from .unicodetools import count_graphemes, read_chars_csv, read_lines, write_chars_csv


def scan_chars(src: Path, tgt: Path, chars: Path) -> None:
    counts = count_graphemes(read_lines(src) + read_lines(tgt))
    existing = read_chars_csv(chars) if chars.exists() else {}
    write_chars_csv(chars, counts, existing)
    kept = sum(1 for g in counts if g in existing)
    print(f"Wrote {len(counts)} characters to {chars}"
          + (f" (kept your classes for {kept})." if existing else "."))
    print("Check the 'class' column (word, punctuation or both), then run 'diff2dict run'.")


def run(args: argparse.Namespace) -> int:
    if not args.chars.exists():
        scan_chars(args.source, args.target, args.chars)
        print("Stopping so you can review the character classes first.")
        return 2
    classes = read_chars_csv(args.chars)
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
    if "teckit_forward" in tk and "teckit_backward" in tk:
        print(f"TECkit word accuracy on held-out lines: {tk['teckit_forward']:.3f} forward, "
              f"{tk['teckit_backward']:.3f} backward.")
    else:
        print(f"TECkit: {tk['teckit']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="diff2dict", description=(
        "Mine character rules and word substitutions from two line-aligned texts "
        "and write them as a TECkit map."))
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan-chars", help="list every character for classification")
    s.add_argument("source", type=Path)
    s.add_argument("target", type=Path)
    s.add_argument("--chars", type=Path, default=Path("chars.csv"))

    r = sub.add_parser("run", help="mine the differences and write the outputs")
    r.add_argument("source", type=Path)
    r.add_argument("target", type=Path)
    r.add_argument("--chars", type=Path, default=Path("chars.csv"))
    r.add_argument("--out", type=Path, default=Path("result.xlsx"))
    r.add_argument("--map", type=Path, default=Path("rules.map"))
    r.add_argument("--oneway", type=Path, default=None,
                   help="one-way rules report (default: oneway_rules.csv next to the map)")
    r.add_argument("--threshold", type=float, default=0.7,
                   help="line similarity below which a pair is skipped (default 0.7)")
    r.add_argument("--min-count", type=int, default=2)
    r.add_argument("--max-iter", type=int, default=5)
    r.add_argument("--reliability", type=float, default=0.95,
                   help="probability a rule must reach in a direction (default 0.95)")
    r.add_argument("--holdout", type=float, default=0.1,
                   help="fraction of line pairs kept back for evaluation (default 0.1)")
    r.add_argument("--seed", type=int, default=1, help="seed for the held-out split")
    r.add_argument("--review", type=Path, default=None,
                   help="pause after the first pass to edit this rules CSV")
    r.add_argument("--lhs-name", default=None)
    r.add_argument("--rhs-name", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    if args.command == "scan-chars":
        scan_chars(args.source, args.target, args.chars)
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
