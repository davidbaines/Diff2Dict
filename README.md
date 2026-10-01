# Diff2Dict

Diff2Dict compares two line-aligned texts that are the same text in two
dialects or orthographies. It mines the character rules (such as `a → aa`,
or `e` deleted at the end of a word) and the genuine word substitutions that
separate them. The results come out as an Excel workbook and a TECkit map, which
SIL's usual tools can then apply in either direction.

See PLAN.md for the design.

## Install

Diff2Dict needs Python 3.10 or later and [uv](https://docs.astral.sh/uv/).

```
uv sync
```

## Workflow

One command mines the differences, writes the outputs and compiles the map:

```
uv run diff2dict source.txt target.txt --chars chars.csv \
    --out result.xlsx --map rules.map
```

If `chars.csv` does not exist, it is written with a suggested class for every
character and the run continues using those suggestions.

To check the classes first, add `--check-chars`:

```
uv run diff2dict source.txt target.txt --chars chars.csv --check-chars
```

The first time, this writes `chars.csv` and stops with the command to run next.
Open the file and check the `class` column. Each character is `word`,
`punctuation` or `both`. A `both` character, such as `'`, is part of a word only
when it sits between two word-forming characters. Rows are matched by the
`codepoints` column, so it does not matter if a spreadsheet mangles the `char`
column. Leave no class blank. Whitespace always separates words, so it is not
listed. Run the same command again: it finds `chars.csv`, pauses for a last
check, and continues when you press Enter. Re-running keeps the classes you set.

### Convert files with the map

Give an input folder, an extension and an output folder, and the files are
converted with the compiled map:

```
uv run diff2dict source.txt target.txt --chars chars.csv --map rules.map \
    --input-folder texts_in --input-ext txt --output-folder texts_out
```

The extension works with or without the dot; `*`, `.*` or `*.*` convert every
text file in the folder, skipping binaries. The scan is not recursive. Add `-r`
(or `--reverse`) to convert target to source. Conversion uses `txtconv`, so the
teckit package must be installed.

Useful options:

| Option | Default | Meaning |
|---|---|---|
| `--check-chars` | off | Pause to review the character classes before mining |
| `--tec` | map path with `.tec` | Compiled table, written when `teckit_compile` is found |
| `--input-folder` | | Folder of files to convert with the compiled map |
| `--input-ext` | | Extension to convert; `*`, `.*` or `*.*` means every text file |
| `--output-folder` | | Where converted files are written; must differ from the input |
| `-r`, `--reverse` | off | Convert target to source instead of source to target |
| `--threshold` | 0.7 | Line similarity below which a pair is skipped as unrelated |
| `--min-count` | 2 | Rules and word pairs seen fewer times are dropped |
| `--max-iter` | 5 | Bootstrapping passes at most |
| `--reliability` | 0.95 | Probability a rule must reach in a direction to be used that way |
| `--holdout` | 0.1 | Share of line pairs kept back to score the map |
| `--seed` | 1 | Seed for the held-out split |
| `--review FILE` | | Pause after the first pass so you can edit the mined rules |
| `--lhs-name`, `--rhs-name` | file names | Names written into the map header |
| `--oneway FILE` | next to the map | Report of rules that work one way only |

With `--review rules.csv` the first pass writes `rules.csv` and waits. Set
`keep` to 0 to reject a rule or 1 to force an unreliable one, then press Enter.
If the file already exists it is read without pausing, so you can re-run with
the same decisions.

Blank lines, and lines that are only a vref marker such as `<range>`, are
skipped when either side has one. They are counted in the Summary sheet.

## Outputs

`result.xlsx` has these sheets:

| Sheet | Contents |
|---|---|
| Summary | Files, parameters, line counts, iterations and held-out accuracy |
| CharPairs | Character rules with context, counts, per-direction reliability and examples |
| WordPairs | Genuine substitutions, splits, merges, insertions and deletions |
| Lexicon | Regular and genuine pairs, with the character rules that explain each regular one |
| PunctPairs | Punctuation swaps |
| CaseOnly | Pairs that differ only in capitalisation |
| SkippedLines | Line pairs too different to compare |

Every aligned word pair is one of three kinds:

| Kind | Example | Use |
|---|---|---|
| Identical | `the` → `the` | Only counts toward line similarity. No rule. |
| Regular | `banana` → `baanaanaa` | The words differ, but a character rule already turns one into the other. |
| Genuine | `dog` → `hound` | No character rule explains the change, so it becomes a word rule. |

`rules.map` is a TECkit mapping source. Rules written `<>` work both ways. A
rule written `>` or `<` works one way only, usually because two source forms
merge into one target form. `oneway_rules.csv` lists those rules and why.

## Applying the map

Diff2Dict compiles the map for you and, with `--input-folder`, converts a whole
folder (see above). To convert a single file yourself, use `txtconv` with the
compiled `.tec` (add `-r` to convert the other way):

```
txtconv -t rules.tec -i source.txt -o converted.txt -nobom
txtconv -t rules.tec -i target.txt -o back.txt -r -nobom
```

Recompile the `.tec` whenever the map changes, or let Diff2Dict do it. The map
can also be added to [SIL Converters](https://software.sil.org/silconverters/),
which applies it in Paratext, FieldWorks, Word and SFM files.

If `teckit_compile` and `txtconv` are on your PATH, the map is compiled and
scored on the held-out lines, and the Summary shows the result. Otherwise the
Summary says evaluation was skipped, and the `.tec` is not written. Either way the
Summary also shows a simulated score, which comes from Diff2Dict's own model
of how TECkit applies the rules. TECkit for Windows, macOS and Linux is at
<https://github.com/silnrsi/teckit/releases>.

## Example: British to American English

`data/` holds the Open English Bible in its Commonwealth and US editions
(public domain, from the BibleNLP eBible corpus). `examples/` holds the result
of:

```
uv run diff2dict data/oeb-british.txt data/oeb-american.txt \
    --chars examples/oeb-chars.csv --out examples/British2American.xlsx \
    --map examples/British2American.map --oneway examples/British2American_oneway.csv \
    --lhs-name British --rhs-name American
```

It finds word rules such as `honour → honor`, `recognise → recognize` and
`plough → plow`, and the swap of quotation marks, `‘ → “` and `’ → ”`. On held-out
verses, TECkit's conversion raises word accuracy from 0.997 to 0.999 in both
directions. The remaining differences are lexical choices such as
`round → around`, which are not consistent enough to become rules.

## Limits

* Word insertions and deletions are reported but not put in the map, because
  TECkit has no safe way to delete a word together with its space.
* Case is folded while mining. The map gets lower case, Title case and UPPER
  case variants of each rule, and word rules also get the casings seen in the
  text.
* Character rules carry at most one grapheme of context on each side.
* A multi-word rule, such as `ropilo → ropi lo`, matches only when the words
  are separated by a single space.
## Tests

```
uv run pytest
```

The TECkit tests run only when `teckit_compile` and `txtconv` are on the PATH,
or when `TECKIT_DIR` names the folder that holds them.
