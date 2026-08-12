# Regenerating the corpus

The "corpus" is the decrypted, decompiled form of every module in your Bitwig
install's `nitro-image`. It is derived entirely from your own installation and
your own keys. **It is never committed and never redistributed** (it is
decrypted Bitwig content). The repository's `.gitignore` keeps the output
directory out of version control; keep it that way.

This is the local, end-to-end flow. Each step is a console script installed
with the package.

## Prerequisites

- The package installed (`pip install -e .`).
- A licensed Bitwig install on this machine.
- Your two keys resolvable (a `keys.json` or the key env vars). See
  [KEY_EXTRACTION.md](KEY_EXTRACTION.md).

`bitwig_nitro.paths` finds your install automatically on standard Linux, macOS,
and Windows layouts. If yours is non-standard, point it there:

```bash
export BITWIG_NITRO_LIBRARY=/path/to/Bitwig/Library   # dir containing nitro-image
# or, to name the archive directly:
export BITWIG_NITRO_IMAGE=/path/to/nitro-image
export BITWIG_NITRO_OUT=/path/to/output               # default: current dir
export BITWIG_NITRO_CONFIG=/path/to/config/dir        # where keys.json lives (default: per-OS config dir)
```

## 1. Get your keys

```bash
nitro-extract-keys
```

Best-effort automated recovery into a `keys.json`. If it cannot recover a key
(the Dag key in particular may not be statically recoverable), it says so and
you follow the manual path in [KEY_EXTRACTION.md](KEY_EXTRACTION.md). You only
need the `nitro_image_key` for the corpus; the `dag_key` is for `0004` document
files and is not required here.

Confirm resolution before going further:

```python
from bitwig_nitro import resolve_nitro_image_key
resolve_nitro_image_key()   # raises MissingKeyError if not configured
```

## 2. Decrypt the image

```bash
nitro-decrypt-corpus
```

Locates `nitro-image`, decrypts every member with your nitro-image key, and
writes the plaintext modules into the output directory, preserving the member
path layout (`filter/SallenKey`, `grid/level/grid_level_pinch`, and so on).
Every written module is verified by re-serializing its AST and asserting
byte-identity with the decrypted plaintext, so nothing lands that the
parser/serializer pair cannot reproduce.

Under the hood this is the same operation as:

```python
from bitwig_nitro import NitroImage
img = NitroImage.load()
for name, plaintext in img.to_dict().items():
    ...   # write plaintext to <out>/<name>
```

## 3. Decompile a module

```bash
nitro-decompile filter/SallenKey
```

Reads one decrypted module and prints its Nitro pseudo-source. This is the
step you use for reading DSP. Equivalent in code:

```python
from bitwig_nitro import decompile_nitrobin_file, pretty_print
ast = decompile_nitrobin_file("filter/SallenKey")   # a decrypted module
print(pretty_print(ast))
```

You can decompile straight from the image without writing the corpus to disk
first:

```python
from bitwig_nitro import read_entry, decompile_nitrobin, pretty_print
print(pretty_print(decompile_nitrobin(read_entry(None, "filter/SallenKey.nitrobin"))))
```

## 4. Build the atlas

```bash
nitro-build-atlas
```

Walks every decrypted module and extracts a structured record per module:
identity and category, ports (audio/value/event, in and out), state fields with
types, parameters, constants, the lifecycle blocks (`init` / `start` /
`process` / ...) with their source, functions, imported primitives, and the
rendered pseudo-source. Output is one JSON record per module plus an index, in
the output directory. This is the queryable form of the corpus.

## Validating a round trip

```bash
nitro-validate
```

Parses every module and re-serializes it, asserting byte-identity and clean
EOF across the whole set. Run it after a Bitwig update or a toolchain change to
confirm the parser and serializer still reproduce the shipped image exactly. A
clean-EOF parse proves the grammar; it does not prove Bitwig would load a
modified module (see [NITRO_LOAD_MECHANISM.md](NITRO_LOAD_MECHANISM.md)).

## Rebuilding the grammar tables

The AST grammar tables (`nitro_ast_tags.json`, `nitro_ast_class_methods.json`)
ship in the package and rarely need regenerating. After a Bitwig upgrade, if
you want to re-derive them against the new jar:

```bash
nitro-build-ast-tables
```

This disassembles the tag enum and dispatch registry from `bitwig.jar` (it
needs a JDK for `javap`) and rewrites both tables. The AST class names and their
byte tags are stable across releases even though the surrounding obfuscated
names move, so this is only occasionally necessary.

## A note on staying current

A Bitwig update rewrites `nitro-image`, so individual modules can go stale
against a corpus extracted from an older build. Re-run steps 2 through 4 after
updating Bitwig to refresh. Always keep a verified backup of the original
`nitro-image` before doing anything that writes back to the install tree.
