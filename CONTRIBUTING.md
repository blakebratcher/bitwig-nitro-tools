# Contributing to bitwig-nitro-tools

Thanks for your interest. This project is a set of offline tools and format
specs for Bitwig's Nitro DSP binary format. Contributions of all kinds are
welcome: bug fixes, new format findings, better docs, more test coverage.

## Content policy (please read first)

This repository ships **tools and format documentation only**. To keep it
clean and respectful of Bitwig, the following must **never** be committed:

- **Cipher keys** of any kind: no hex key literals in source, no `keys.json`,
  no keys embedded as defaults. Keys are resolved at runtime from your own
  environment (see `docs/KEY_EXTRACTION.md`).
- **Decrypted Bitwig content**: no `.dec` files, no decompiled DSP source,
  no extracted DSP atlas or corpus, no bundled `nitro-image` payloads.
- Any file derived from a specific Bitwig installation.

The `.gitignore` already excludes `keys.json`, `*.dec`, `/corpus/`, and
`/atlas/`. Do not override those entries. If you send a pull request, double
check `git diff` for stray keys or decrypted material before pushing.

Everything the tools operate on is **bring-your-own-install**: users extract
their own keys and point the tools at their own Bitwig files. We redistribute
neither.

## Setup

Python 3.10 or newer is required. Runtime code uses the standard library only;
`pytest` is the sole development dependency.

```bash
python -m venv .venv
source .venv/bin/activate        # fish: source .venv/bin/activate.fish
pip install -e ".[dev]"
pytest
```

That installs the package in editable mode and makes the `nitro-*` command
line entry points available on your `PATH`.

## Running the tools

The console scripts installed by `pip install -e .`:

| Command                  | Purpose                                                    |
|--------------------------|------------------------------------------------------------|
| `nitro-decompile`        | Decompile a `.nitrobin` file to readable pseudo-source.    |
| `nitro-extract-keys`     | Help locate and record keys from your local Bitwig install.|
| `nitro-decrypt-corpus`   | Decrypt a `nitro-image` archive into a local working dir.  |
| `nitro-build-atlas`      | Build a per-module index over a decrypted corpus.          |
| `nitro-build-ast-tables` | Regenerate the AST grammar tables (see below).             |
| `nitro-validate`         | Round-trip check the parser / writer against a corpus.     |

Anything that touches encrypted Bitwig data needs keys you supply yourself.
Set `BITWIG_NITRO_DAG_KEY` / `BITWIG_NITRO_IMAGE_KEY` as hex strings, or point
`BITWIG_NITRO_KEYS` at a `keys.json` (schema:
`{"dag_key": "<hex>", "nitro_image_key": "<hex>"}`). Without keys, the crypto
paths raise `MissingKeyError` and tell you which variable to set.

## Code style

- Standard library only for runtime code. If you think you need a third-party
  dependency, open an issue first so we can talk it through.
- Keep the public API stable. The names exported from `bitwig_nitro/__init__.py`
  (`__all__`) are the contract. Add new names deliberately; don't rename or
  remove existing ones without a deprecation.
- 4-space indentation, `from __future__ import annotations` at the top of new
  modules, type hints on public functions, and a short docstring on each
  public function or class.
- Prefer small, testable functions. Binary-format code should have a
  round-trip test (parse then re-serialize, assert byte-identical) wherever the
  format allows it.

## Regenerating the AST grammar tables

Two JSON tables ship inside the package at `src/bitwig_nitro/data/`:

- `nitro_ast_tags.json`: maps AST tag bytes to node class names.
- `nitro_ast_class_methods.json`: the per-class field read/deserialize order.

These are derived data. If a future Bitwig version changes the AST layout,
regenerate them (rather than hand-editing) with:

```bash
nitro-build-ast-tables
```

The generator reads from a source you supply locally and writes the two JSON
files back into `src/bitwig_nitro/data/`. Commit the regenerated tables (they
contain grammar structure only, no keys and no decrypted source), and note the
Bitwig version you generated against in your pull request.

## Tests

Run the full suite with `pytest`. Tests are offline and use small fixtures
under `tests/fixtures/`; none of them require a Bitwig install or any key.
Please add a test for any behavior you change or add.

## Reporting format findings

If you decode a new field, tag, or structure, a short write-up in `docs/` plus
a round-trip test is the ideal contribution. Describe the bytes, not the
decrypted content.
