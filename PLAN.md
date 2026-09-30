# Diff2Dict: create a TECkit map from two aligned texts of different orthographies or dialects

## Context

We have two **line-aligned** plain-text files that are the same text in two
dialects or orthographies. Line *N* in the source corresponds to line *N* in the
target. The tool mines the differences and emits a rule set that SIL's
EncodingConverter can apply.

**Deliverables:**
1. **Miner:** discover, with counts and context:
   - **Character-level substitution rules** (`a`→`aa`, one vowel→another,
     insertions/deletions like `e`→∅), each with the context they occur in.
   - **Word-level substitutions** that are *genuine lexical replacements*, that is
     not the automatic consequence of a regular character rule.
2. **TECkit `.map` generator:** emit the mined rules as a single TECkit mapping
   file. We do **not** build a runtime converter. `teckit_compile` compiles the
   `.map`, and **txtconv** / **SIL Converters** apply it (bidirectionally) inside
   the user's normal Paratext/FieldWorks/Word/SFM workflow.

Two hard parts. First, systematic character correspondences may make most words
differ, hiding real word substitutions in the noise, so the miner **bootstraps**
between character rules and word pairs. Second, rules are often **not invertible**
(`oe`→`o` plus native `o`→`o` is a merger), so directionality is decided per rule
at generation time.

## Decisions

- **Language:** Python 3.10+. Deps: `regex` (grapheme clusters via `\X`),
  `openpyxl` (xlsx), `unicodedataplus` (up-to-date Unicode names/categories).
  Stdlib: `unicodedata` (NFC), `csv`, `argparse`, `collections`, `dataclasses`,
  `subprocess`.
- **Environment & dependency management:** uv, with `pyproject.toml` and a committed
  `uv.lock`. Create/sync the env with `uv sync`; run everything via `uv run`. Deps
  are added with `uv add` (and `uv add --dev` for dev deps such as `pytest`).
- **Alignment:** files are already line-aligned in 'vref' format (line N maps to
  line N); plain text, no markup. A line pair where either side is empty (a blank
  vref verse) or is a marker such as `<range>` is skipped entirely.
- **Character unit:** grapheme cluster.
- **'Both' rule:** an ambiguous char (e.g. `'`) is word-forming only between two
  word-forming characters; punctuation otherwise.
- **Case:** rules are mined case-folded, so `A`→`Aa` and `a`→`aa` count together.
  The map emits lower and upper case variants of each rule. Pairs that differ only
  in case are reported but produce no rule.
- **Punctuation:** consistent 1→1 punctuation swaps (e.g. `"`→`“`) are mined too and
  go in the map when reliable.
- **Reliability bound:** `--reliability` (default 0.95), used both for context
  generalisation and for choosing bidirectional versus one-way.
- **Rule shape:** `x → y / L _ R` (L, R a grapheme or `#` word boundary); `x` or `y`
  may be empty (**n,m ∈ 0..3, never both 0**). Keep the least-specific context that
  stays reliable. Same for whole-word insertion/deletion pairs.
- **Word pair kinds:** every aligned pair falls into one of three kinds:

  | Kind | Example | Use |
  |---|---|---|
  | Identical | `the` → `the` | Only counts toward line similarity. No rule. |
  | Regular | `banana` → `baanaanaa` | The words differ, but the `a`→`aa` char rule already turns one into the other. |
  | Genuine | `dog` → `hound` | No char rule explains the change, so it needs a word rule. |

  Only genuine pairs become word rules in the map. Regular pairs need no word rule,
  because the char rules already produce them, and one could conflict with those
  rules. No rule ever maps a word or character to itself.
- **Directionality:** compute reliability per direction, P(y|x,ctx) forward and
  P(x|y,ctx) backward. Emit **one `rules.map`**: bidirectional rules where
  reversible, one-way rules where lossy (a merger), plus a **one-way-rules report**.
- **No runtime converter.** Emit `.map` only; the SIL toolchain applies it.
- **Self-evaluation:** shell out to `teckit_compile` + `txtconv` when on PATH; skip
  gracefully (with a note) when not installed.
- **Review checkpoint:** default automatic (fixpoint with min-count + max-iter);
  `--review` pauses to let the user edit the mined char-rules CSV.
- **Unicode hygiene:** NFC-normalise both files first; combining marks (Mn/Mc)
  default to word-forming; `ʼ` U+02BC (Lm, a letter) listed separately from `'`
  U+0027 and `’` U+2019; all CSVs written `utf-8-sig`.
- **TECkit `.map` syntax verified against the official SIL reference during
  implementation** (not from memory), including the bidirectional (`<>`) versus
  one-way operators and context/word-boundary notation.

## Algorithm: mining

**Stage A: character classification (two-pass, always).** `scan-chars` NFC-
normalises both files, enumerates every distinct grapheme, writes `chars.csv`
(`char, codepoints, unicode_name, category, suggested_class, class`). Suggested from
Unicode category (L*/M* → word, P*/S*/Z* → punctuation, apostrophes → both). User
edits `class` for ambiguous rows. If missing at run time, generate and stop.

**Stage B: tokenize.** Split each line into word tokens using `chars.csv` and the
'both'-between-letters rule. Words are grapheme lists. Punctuation tokens are kept
as a separate sequence per line.

**Stage C: bootstrapping loop.** First discard every line pair where either side is
empty or a marker such as `<range>`; those are neither mined nor counted. Then set
aside a random ~10% of the remaining pairs as the held-out set, which is never mined.
On the rest:
- **C1 fuzzy word alignment per line pair.** Weighted Needleman-Wunsch DP;
  substitution cost = normalised character edit distance under current rule weights
  (learned rules cost less); allow 1↔2, 1↔3 merges/splits for compounds. Line
  similarity = fraction of aligned words under a cost bound; below `--threshold`
  (default 0.7) the pair goes to SkippedLines.
- **C2 mine character rules with context.** Only close pairs feed this step
  (normalised distance below 0.5), so a genuine swap such as `dog`→`hound` yields no
  junk rules. Grapheme-level weighted Levenshtein with backpointers per 1→1 pair;
  coalesce adjacent non-equal/insert/delete ops into n→m
  chunks (0..3, not both 0); record one grapheme of L/R context; aggregate counts and
  examples; generalise context to least-specific-that-stays-reliable.
- **C3 feed rules back as cost reducers** (not global rewrites). Re-run C1 so noisy
  lines clear the bound and hidden pairs surface. Iterate to fixpoint or `--max-iter`
  (5). `--min-count` (2) drops rare rules. `--review` pauses after first C2 to edit
  `rules.csv` (`source,target,left,right,count,examples,keep`).
- **C4 punctuation swaps.** For each aligned line pair, align the punctuation
  sequences with Levenshtein and count 1→1 substitutions.

**Stage D: classify pairs.** Identical pairs are dropped. A differing pair is
genuine when the source word, after applying accepted char rules, still differs
from the target, otherwise regular. Genuine pairs go to WordPairs; both kinds go to
Lexicon.

## Map generation

- Build one TECkit `.map` from accepted char rules, reliable punctuation swaps and
  genuine word pairs as word rules (bounded by `^[W]`, see Implementation notes),
  each in lower and upper case variants. Order rules so application is deterministic (leftmost-longest,
  specific before general).
- Mark each rule bidirectional (`<>`) when both P(fwd) and P(bwd) clear a
  reliability bound; otherwise emit a one-way rule in its reliable direction and add
  it to `oneway_rules.csv` (rule, direction, p_fwd, p_bwd, reason).
- Compile check and eval: if `teckit_compile`/`txtconv` are on PATH, compile the map
  and run txtconv (forward and `-r`) on the held-out lines to score
  per-direction word accuracy. Round-trip is invalid because mergers are lossy.

## Output

- `result.xlsx` (openpyxl):
  - **CharPairs:** `source, target, left_ctx, right_ctx, count, p_fwd, p_bwd, examples`.
  - **WordPairs:** `source, target, count, n_source, n_target, example_lines`,
    genuine substitutions only.
  - **Lexicon:** every regular and genuine pair, with its kind and the char rules
    that explain it. Identical pairs are not listed.
  - **PunctPairs:** `source, target, count, p_fwd, p_bwd, examples`.
  - **CaseOnly:** pairs that differ only in case, with counts.
  - **SkippedLines:** line number plus both texts.
  - **Summary:** files, params, iterations, held-out accuracy per direction (or a
    note that the TECkit tools were absent).
- `rules.map`: TECkit source.
- `oneway_rules.csv`: rules that are safe in only one direction.

## Files to create

- `diff2dict/` package:
  - `cli.py`: argparse subcommands `scan-chars`, `run`.
  - `unicodetools.py`: NFC, grapheme splitting, char classification, CSV IO.
  - `tokens.py`: line to word and punctuation tokens (class map plus 'both' rule).
    Not named `tokenize.py`, which would shadow the stdlib module.
  - `align.py`: weighted word DP plus grapheme Levenshtein with backpointers.
  - `mine.py`: bootstrapping loop, rule/context extraction and generalisation,
    per-direction reliability.
  - `teckit.py`: emit `.map` (syntax per SIL reference); optional compile+txtconv
    eval via `subprocess`.
  - `report.py`: openpyxl workbook builder.
- `pyproject.toml` + `uv.lock`: uv project metadata and pinned deps (`regex`,
  `openpyxl`, `unicodedataplus`), plus a `diff2dict` console-script entry point. No
  `requirements.txt`.
- `README.md`: uv-based install (`uv sync`), two-pass char workflow, examples, and
  how to apply the map with txtconv / SIL Converters downstream.
- `sample/source.txt` + `sample/target.txt`: small pair with an `a`→`aa` rule, an
  `e`→∅ context rule, one genuine word swap and one punctuation swap, each occurring
  at least twice so it survives `--min-count 2`.
- `tests/`: pytest for tokeniser, alignment, rule extraction, map emission, and (if
  TECkit present) a compile smoke test.

## CLI

```
# uv manages the venv; a `diff2dict` console script is defined in pyproject.toml.
uv run diff2dict scan-chars SRC TGT --chars chars.csv
uv run diff2dict run SRC TGT --chars chars.csv --out result.xlsx --map rules.map \
    [--threshold 0.7] [--min-count 2] [--max-iter 5] [--reliability 0.95] \
    [--review rules.csv]
```

## Verification

1. `uv sync` installs `regex`, `openpyxl`, `unicodedataplus`, with no native deps.
2. `scan-chars` on `sample/` then inspect `chars.csv`: letters=word, punct=punct,
   apostrophe=both, combining marks=word, `ʼ` U+02BC listed separately.
3. `run` on `sample/` then open `result.xlsx`: CharPairs has `a`→`aa` and the `e`→∅
   context rule with counts and per-direction probabilities; WordPairs has the
   genuine swap only; Lexicon holds regular and genuine pairs but no identical ones;
   PunctPairs has the punctuation swap; SkippedLines empty for the all-related
   sample.
4. Add an unrelated target line; it lands in SkippedLines.
5. Add a line pair with one side blank and one with `<range>`; confirm both are
   skipped, not counted.
6. Capitalise a sentence-initial word containing `a`; confirm it counts toward the
   `a`→`aa` rule and the map has both case variants.
7. `--review` pauses, writes `rules.csv`; rejecting a rule changes WordPairs.
8. Open `rules.map`; validate against the TECkit reference; if `teckit_compile` is
   installed, confirm it compiles, and check `oneway_rules.csv` lists the merger(s).
9. If the TECkit tools are present, Summary shows per-direction held-out accuracy;
   if absent, Summary notes evaluation was skipped.
10. Round-trip a toned/accented grapheme through all CSVs, with no mojibake.

## Implementation notes

Decisions made while building, which refine the plan above.

- **TECkit syntax** was checked against "The TECkit Language" (rev 21) and the
  compiler source (`silnrsi/teckit`, `source/Compiler.cpp`):
  - `#` in a TECkit context means start or end of the text, not a word
    boundary. The map therefore defines `Class [W]` (word-forming characters)
    and writes a word boundary as `^[W]`, because the negated item also matches
    the end of the text. When the text has 'both' characters, the map also
    defines `[B]` and `[WB]`, and a boundary after a word is
    `(^[WB] | [B] ^[W])` (before a word, the mirror image). So `dog's` stays
    one word, as it does for the tokeniser, while `dog’` at the end of a quote
    does not.
  - TECkit ranks rules by match length in codepoints first and context length
    second, taking the longest branch of an alternation
    (`Compiler::sortRules`). The simulator uses the same key.
  - A rule line cannot start with its operator, so the match side is never
    empty. An insertion or deletion is folded into a neighbouring grapheme
    (`e → ∅ / o _` becomes `oe > o`). A deletion with no neighbouring grapheme
    in its context is written with an empty replacement, which works forward
    only. An insertion with no neighbouring grapheme cannot be written; it is
    listed in the one-way report as unmapped.
  - The map is UTF-8 with a BOM, which the compiler detects, so comments can
    show the characters. The rules use `U+XXXX` codes throughout.
- **Combining marks:** when the text has combining marks, the map defines
  `Class [M]` and ends each character rule's post-context with `^[M]`, so a
  rule never separates a base letter from its mark.
- **Doubling:** an insertion or deletion that doubles or undoubles a neighbour
  absorbs it, so doubling reads as `a → aa` rather than `∅ → a / a _`. Other
  insertions and deletions stay as rules with an empty side.
- **Target-side context:** each mined occurrence also records the graphemes
  around `y` in the target. `p_bwd` counts only occurrences whose target
  neighbours match the rule's context, because the reverse rule reads the
  target. So a rule next to another changed grapheme is not wrongly marked
  reversible.
- **'Both' characters in character rules:** when a rule's match starts or
  ends with a 'both' character, the map requires a word-forming character
  beyond it (`[W]`), so `n' > n` (from `don't → dont`) leaves closing quotes
  alone. A rule whose context is itself a 'both' character is left out of the
  map and listed as unmapped.
- **Grapheme alignment costs:** substitution 1.0, insertion or deletion 0.7,
  learned rule 0.1. With these costs `kale → kaal` aligns as `+a` and `-e`,
  not as two substitutions.
- **Compounds:** 1:2 and 1:3 links need a normalised distance of 0.15 or less,
  so a deleted word is not glued onto its neighbour.
- **Context generalisation** tries no context first, then one side (left or
  right, whichever covers more), then both. Each step accepts the group with
  the most occurrences that reaches `--reliability` in some direction.
- **Stage D** uses a Python model of TECkit's matching (TECkit's precedence,
  file order on ties, output not rescanned). A pair is regular only when the
  forward rules explain it, so a pair that only one-way reverse rules explain
  still gets a word rule. The same model gives the simulated held-out score in
  the Summary.
- **Title case** variants of character rules need a word boundary before the
  match and a non-capital after it (`^[U]`), so they never fire inside
  all-caps words such as `LORD`, where the UPPER variant applies.
- **Word insertions and deletions** appear in WordPairs but not in the map.
- **Case variants:** each rule is emitted in lower, Title and UPPER case. For
  word rules, each match form in each direction takes its most frequent
  observed casing when that casing was seen at least `--min-count` times (so
  `LORD → Yahweh` can be learned), otherwise the generated form. A variant
  whose match is already used in a direction is dropped from that direction.
  Character rules are written most specific first, so on TECkit's file-order
  ties a rule with context beats a general one.
- **Punctuation** rules for a 'both' character carry contexts, so an apostrophe
  inside a word is left alone.
- **Extra options:** `--holdout` (0.1), `--seed` (1), `--lhs-name`,
  `--rhs-name` and `--oneway`. In the review CSV, `keep` 0 rejects a rule and
  1 forces an unreliable one. An existing review file is read without pausing.
- **Extra sheets:** Summary comes first; CaseOnly lists pairs that differ only
  in case.
- **txtconv 2.5.12 on Windows** occasionally crashes (about once in 60 runs on
  the same input), so evaluation retries three times, and a failure is noted
  in the Summary instead of stopping the run.
- **Real data:** `data/` holds the Open English Bible in Commonwealth and US
  spelling, and `examples/` holds the resulting `British2American.map`.
