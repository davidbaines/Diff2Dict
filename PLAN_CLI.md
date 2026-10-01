# CLI design: one project folder

## Context

The CLI had grown many path options that overwrote each other between runs. It was
reduced to a single project-folder command that is easy to learn:

```
diff2dict FOLDER SOURCE TARGET [options]
```

`FOLDER` is an existing directory; `SOURCE` and `TARGET` are the two aligned texts,
in that order. All three are required. There is no non-folder mode.

## Folder layout

Everything is derived from `FOLDER`; the stem is the folder's own name.

- `FOLDER/teckit/` holds the generated `<name>.map`, `.tec`, `.xlsx`,
  `<name>_oneway.csv` and `<name>_chars.csv`.
- `FOLDER/input/` holds the files to convert (optional).
- `FOLDER/output/` holds the converted files.

A character class file that has been reviewed lives at
`FOLDER/teckit/<name>_chars.csv`; to share one class file across projects, copy or
combine it there by hand.

## Options

- `--check-chars`: pause to review the character classes before mining. The first
  run writes `FOLDER/teckit/<name>_chars.csv` and stops with the command to run
  again; the next run finds it, pauses for a last check, then continues.
- `--input-ext`: which files in `FOLDER/input` to convert, with or without the dot;
  `*`, `.*` or `*.*` means every text file (the default). Not recursive.
- `-r` / `--reverse`: convert target to source instead of source to target.
- Advanced mining: `--threshold`, `--min-count`, `--max-iter`, `--reliability`,
  `--holdout`, `--seed`, `--review`.

The map header names come from the source and target file names. There are no
per-output path flags: the folder convention fixes every path.

## Behaviour

- A missing or non-directory `FOLDER` is an error.
- If `FOLDER/input` exists but teckit is not installed, the mining outputs are
  still written and conversion is skipped with a warning.
- Conversion uses `txtconv` with the freshly compiled `.tec`; files are read
  through `read_lines`, which normalises to NFC, so the map's `ExpectsNFC` holds.
- Per-file conversion failures are reported and give a non-zero exit.

## Implementation

All in `diff2dict/cli.py`:
- `build_parser`: positionals `folder`, `source`, `target`; the options above.
- `_derive_paths`: sets `chars`, `map`, `tec`, `out`, `oneway`, `input_folder`,
  `output_folder` from `FOLDER`.
- `run`: validate the folder, derive paths, create `FOLDER/teckit`, resolve the
  character classes, mine, write the map, workbook and one-way report, compile the
  `.tec`, and convert `FOLDER/input` into `FOLDER/output` when it exists and teckit
  is present.

## Example

`examples/British2American/` is a project folder whose `teckit/` holds the result
of:

```
uv run diff2dict examples/British2American data/oeb-british.txt data/oeb-american.txt
```

Its reviewed `teckit/British2American_chars.csv` is kept in the repo.
