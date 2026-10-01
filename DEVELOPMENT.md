# Development log

How Diff2Dict was specified and built, and what the code reviews found and fixed.
PLAN.md holds the design, including its "Implementation notes" section; README.md
explains how to use the tool.

## The request

Build a tool that compares two line-aligned texts in two dialects or
orthographies, and:

- lists every character with its Unicode information in an editable CSV, so the
  user can mark each one as word-forming, punctuation or both (for example `'`);
- skips line pairs that bear no relation to each other;
- on related lines, finds the character-level rules (such as `a → aa`, or a
  vowel replaced by another) and the genuine word replacements, with counts.

## How the design was reached

The design came out of an interview. The main decisions, in order:

1. **Basics:** Python, line-aligned (vref) input, a two-pass CSV for character
   classes, Excel output. A 'both' character is word-forming only between two
   word-forming characters. Matching is case-sensitive at first (later changed
   to folded, see below).
2. **Bootstrapping:** regular character correspondences make most words differ,
   hiding genuine word swaps. So the miner alternates between character rules
   and word alignment, feeding learned rules back as cheaper edit costs.
3. **Rule shape:** `x → y / L _ R`, with n and m from 0 to 3 graphemes, never
   both 0, so insertions and deletions such as `Oddwoerd → Oddword` are
   covered. Grapheme clusters are the unit.
4. **A converter in both directions:** the user asked for a bidirectional
   converter and whether Racket would suit a domain-specific language. Staying
   in Python and emitting a TECkit `.map` was chosen instead. Rules that merge
   two forms (`oe → o` where `o` also occurs alone) cannot be reversed, so
   reliability is measured in each direction and such rules are written
   one-way.
5. **No custom runtime:** the user pointed out that SIL Converters already
   apply TECkit maps, so only the `.map` is produced and txtconv or SIL
   Converters apply it. This halved the work.
6. **Refinements:** uv for environment and dependencies; line pairs skipped
   when either side is blank or a marker such as `<range>`; case folded while
   mining, with lower, Title and UPPER variants in the map; one-for-one
   punctuation swaps mined too; about 10% of lines held out for evaluation and
   never mined; character rules mined only from close word pairs; a
   `--reliability` setting (0.95).
7. **Word pair kinds:** identical pairs only count toward line similarity;
   regular pairs (explained by character rules) need no word rule; only
   genuine pairs become word rules. No rule maps anything to itself.

## What was built

- `diff2dict/` package: `unicodetools.py`, `tokens.py`, `align.py`, `mine.py`,
  `teckit.py`, `report.py`, `cli.py`.
- Command: `diff2dict SOURCE TARGET [options]` (add `--check-chars` to review the
  character classes first, `--input-folder` to convert files with the map).
- Outputs: `result.xlsx` (Summary, CharPairs, WordPairs, Lexicon, PunctPairs,
  CaseOnly, SkippedLines), `rules.map`, `rules.tec`, and `oneway_rules.csv`.
- `sample/`: a small invented dialect pair covering `a → aa`, word-final `e`
  deletion, the one-way merger `oe → o`, a genuine swap (`pin → hunu`), a
  compound split (`ropilo → ropi lo`), a deleted particle, a quote swap,
  blank, `<range>` and unrelated lines, all-caps words, `ʼ` (U+02BC), a
  combining macron, digits and apostrophes.
- `tests/`: 82 pytest tests. The TECkit tests compile maps and convert text
  with the real tools when they are on the PATH or `TECKIT_DIR` points to them.
- `data/` and `examples/`: the Open English Bible in Commonwealth and US
  spelling (public domain, from the BibleNLP eBible corpus), and the resulting
  `British2American.map`, workbook and one-way report. The WEB and LXX2012
  files in that corpus turned out to be empty, so the OEB was used.

## Results

- The sample map, run through real TECkit, turns the source into the target
  exactly, apart from the deleted particle (word deletions are not mapped) and
  the deliberately blank, `<range>` and unrelated lines.
- British to American (11,663 verses, about 15 seconds): word rules such as
  `honour → honor`, `recognise → recognize` and `plough → plow`, and the quote
  swap `‘ → “`. On held-out verses TECkit raises word accuracy from 0.997 to
  0.999 in both directions.

## Findings while building

- **TECkit syntax**, checked against "The TECkit Language" and the compiler
  source (`silnrsi/teckit`, `source/Compiler.cpp`):
  - `#` in a context is the start or end of the text, not a word boundary, so
    the map defines `Class [W]` and writes boundaries with `^[W]`.
  - A rule line cannot begin with its operator, so the match side is never
    empty; insertions are folded into a neighbouring grapheme. An empty
    replacement is allowed, so an unfoldable deletion is written one-way.
- **Alignment costs:** with equal costs, `kale → kaal` aligned as two
  substitutions (`le → al`), which scattered the rules. Insertions and
  deletions now cost 0.7, so it aligns as `+a` and `-e`.
- **Compounds:** `ti kala → kaalaa` was taken as a merge. Merges now need a
  distance of 0.15 or less.
- **txtconv 2.5.12 on Windows** crashes (access violation) about once in 60 runs
  on the same valid input. Evaluation retries three times and records a failure
  in the Summary instead of stopping. It is worth checking whether Linux builds
  behave better.

## Code review rounds

The whole project was reviewed with `/code-review` after each round of fixes.
Before the first round, an advisor pass found two problems, which were fixed:
Title-case variants fired inside all-caps words (`KALA` would become `KAaLAa`),
and the review CSV defaulted unreliable rules to "reject", which blocked them
for good.

**Round 1**
- The UPPER variant was lost when a rule's two sides were single graphemes, so
  `E` inside `BED` was never converted. Variants are now de-duplicated on
  their full form.
- A 'both' character (apostrophe, hyphen) counted as a word boundary in the
  map but not in the tokeniser, so `dog's` could be half-converted. The map now
  defines `[B]` and `[WB]`, and a boundary is `(^[WB] | [B] ^[W])` after a word
  and its mirror image before one.
- A pair explained only by reverse rules was called regular, so forward
  conversion had no rule for it. Regular now means "explained by the forward
  rules", as PLAN.md says.
- The simulator ranked rules by match plus context length. The compiler source
  shows TECkit ranks by match length first, in codepoints, then context length
  (`Compiler::sortRules`). The simulator now uses the same key, including the
  `^[M]` guard.
- "Lines in files" ignored a longer target file.

**Round 2**
- A punctuation swap that is reliable both ways was reported as one-way when
  only one side was a 'both' character.
- Punctuation contexts ignored the suggested class when `chars.csv` had no row
  for a character, so `’ → ”` would also have changed apostrophes inside words.

**Round 3**
- `p_bwd` looked up the source-side context in the target, so a rule next to
  another changed grapheme could be marked reversible when it was not. Each
  occurrence now records its target neighbours, and `p_bwd` counts only those
  that match.
- A rule such as `n' > n` (from `don't → dont`) would also delete closing
  quotes. Rules whose match starts or ends with a 'both' character now require
  a word-forming character beyond it, and rules whose context is a 'both'
  character are left unmapped.
- A KeyError when only one txtconv direction succeeded.

**Round 4**
- A Title variant could replace a `[W]` guard, so `'tis` would have become
  `Tis` in reverse. Title variants are now written only where both sides start
  a word with a cased letter.

**Round 5**
- A general rule's Title variant could beat a more specific rule's on TECkit's
  file-order tie (`apple → epple` but `Apple → Aapple`). Character rules are
  now written most specific first.
- One odd observed casing (`dog → Hound` once) could take over the Title form.
  Each match form now takes its most frequent observed casing only if it was
  seen at least `--min-count` times, chosen separately for each direction, so
  `LORD → Yahweh` can still be learned.

**Round 6**
- Nothing significant. One unlikely edge case was left: file order follows the
  forward direction, so in reverse two nearly contradictory rules could tie in
  the wrong order.

## Running the TECkit checks

```
uv sync
TECKIT_DIR=/path/to/teckit/bin uv run pytest
uv run diff2dict data/oeb-british.txt data/oeb-american.txt \
    --chars examples/oeb-chars.csv --out examples/British2American.xlsx \
    --map examples/British2American.map --oneway examples/British2American_oneway.csv \
    --lhs-name British --rhs-name American
```

With `teckit_compile` and `txtconv` on the PATH, `run` also scores the map
with TECkit, and the Summary sheet shows the result.
