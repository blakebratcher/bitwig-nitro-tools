# bitwig-nitro-tools

An offline reverse-engineering toolchain for Bitwig Studio's Nitro DSP format.
Nitro is the language every Bitwig native device and Grid module is written in,
and the compiled modules ship as an encrypted archive of serialized syntax
trees (`.nitrobin`). This toolchain decrypts that archive, decompiles the
modules back to readable Nitro pseudo-source, and lets you parse, edit, and
re-serialize them. It runs entirely on your machine against your own Bitwig
install, using standard-library Python only.

It ships tools and format specs. It does not ship any Bitwig source, any
decrypted content, or any cipher key. You bring your own licensed install and
your own extracted keys.

![The Nitro toolchain: decrypt an encrypted module, parse it, decompile it, and read the DSP](assets/pipeline.png)

## What you can actually do

Read the DSP behind any built-in device or Grid module. Point the decompiler at
a module and instead of a wall of bytes you get its Nitro pseudo-source: its
ports, its state, and the `process` block that does the per-sample work:

```bash
nitro-decompile filter/GrandLPF
```

![Bitwig's GrandLPF decompiled: a 4-pole Moog ladder](assets/grandlpf-ladder.png)

That is Bitwig's `GrandLPF`, verbatim: a 4-pole Moog ladder, 4× oversampled,
with a TPT integrator, zero-delay feedback, a per-stage ADAA soft-clip in the
feedback path, and a selectable saturation flavor (`GRANDMOTHER` here). You can
read exactly why it sounds the way it does.

The character of the other filters is just as legible. Here is the Sallen-Key's
nonlinearity. It has two selectable curves, a symmetric `tanhApprox7` and a
biased, asymmetric `clipClassA`:

![The Sallen-Key nonlinearity, decompiled](assets/sallenkey-nonlinearity.png)

The same works for oscillators, envelopes, dynamics, delays, and the rest of the
module set. You can also go the other way: mutate a numeric constant in a module
at fixed size and repack the archive, byte-identical except for the member you
touched (see [Status & limitations](#status--limitations) for what that does and
does not get you).

## Quickstart

```bash
pip install -e .

# 1. recover your nitro-image key from your own install, via the bundled
#    controller (see docs/KEY_EXTRACTION.md):
nitro-extract-keys --install-controller   # copy the controller into Bitwig
#    then add "Nitro Key Dump" in Bitwig -> Settings -> Controllers, and:
nitro-extract-keys --live                 # read its dump, write keys.json
#    (already have the hex? nitro-extract-keys --image-key <hex>)

# 2. decrypt every module from your install's nitro-image
nitro-decrypt-corpus

# 3. read a module
nitro-decompile filter/SallenKey

# (optional) decrypt the nitro-std stdlib SOURCE. Unlike nitro-image it needs a
# live JVM (runtime PRNG cipher), so it runs via its own bundled controller:
nitro-decrypt-std --install-controller    # copy the controller into Bitwig
#    then add "Nitro Std Dump" in Bitwig -> Settings -> Controllers, and:
nitro-decrypt-std                          # verify + report the decrypted tree
```

![Adding the bundled "Nitro Key Dump" controller in Bitwig's Settings → Controllers: pick vendor bitwig-nitro-tools, product Nitro Key Dump, and Add](assets/add-controller.png)

Extracting keys is the one step that needs setup, because the keys live in your
Bitwig install and this project ships none. `nitro-extract-keys` writes
`keys.json` to your per-user config directory (`%APPDATA%\bitwig-nitro` on
Windows, `$XDG_CONFIG_HOME/bitwig-nitro` or `~/.config/bitwig-nitro`
elsewhere); set `BITWIG_NITRO_CONFIG` to use a different directory. See
[docs/KEY_EXTRACTION.md](docs/KEY_EXTRACTION.md).

From Python, the same read path without writing anything to disk:

```python
from bitwig_nitro import read_entry, decompile_nitrobin, pretty_print

plaintext = read_entry(None, "filter/SallenKey.nitrobin")  # decrypts from your image
ast       = decompile_nitrobin(plaintext)
print(pretty_print(ast))
```

`read_entry` resolves the installed `nitro-image` and your nitro-image key
automatically. If neither is found you get a clear error pointing at the key
docs, not a stack trace.

## What ships, and what does not

**Ships:** the Python tools (decrypt, decompile, parse, serialize, edit,
pretty-print, atlas builder), the CLI commands, the `.nitrobin` binary-protocol
and AST grammar specs, and the two AST grammar tables the parser is driven from.

**Does not ship:** any cipher key, any decrypted Bitwig content, any decompiled
DSP source, any corpus or atlas data. Those are all derived locally from your
own install and are gitignored, never redistributed.

Bring your own licensed Bitwig Studio. MIT licensed.

## How it works

Four stages take you from the encrypted archive to readable source:

1. **`dag_cipher`**: Bitwig's Dag cipher is a self-inverse keystream XOR.
   `dag_decrypt(data, key, iv)` decrypts (and, being self-inverse, re-encrypts)
   a payload once you supply the key and IV.

2. **`nitro_image`**: `<install>/Library/nitro-image` is a plain ZIP whose
   member payloads are each `[version][iv:198][ciphertext]`. `NitroImage.load()`
   opens the archive; `.plaintext(name)` decrypts one member.

3. **`nitrobin_decompiler`**: a decrypted member is a serialized AST in
   Bitwig's TrV frame format. `decompile_nitrobin(bytes)` walks the frames and
   rebuilds the `AstNode` tree, driven entirely by the shipped grammar tables.

4. **`nitro_pretty`**: `pretty_print(ast)` renders the tree as Nitro
   pseudo-source for reading and diffing.

`nitrobin_writer` closes the loop by re-serializing an `AstNode` tree back to
bytes (byte-identical when unchanged), and `nitro_edit` mutates constants at
fixed wire size so a repack stays valid.

The formats behind each stage are documented in `docs/`:
[binary protocol](docs/NITRO_BINARY_PROTOCOL.md),
[AST model](docs/NITRO_AST.md), and
[load mechanism](docs/NITRO_LOAD_MECHANISM.md).

## Notable findings

A few things that surfaced while reverse-engineering the format:

- **Bitwig JIT-compiles its own DSP.** Every device and Grid module is a small
  program, compiled at first use: Nitro source becomes an AST, then LLVM IR,
  then native machine code that runs inline on the audio thread. The LLVM IR is
  even cached in plaintext under `~/.BitwigStudio/cache/nitro/`, so there are two
  readable layers: the decompiled Nitro source and the IR itself.
- **The "encrypted" module library is a plain ZIP.** `nitro-image` is an ordinary
  ZIP archive; only each member's payload is XOR'd, and the cipher is
  self-inverse, so encrypting and decrypting are the same operation. Every module
  re-encrypts byte-identically, and a whole-image identity repack matches the
  shipped file's SHA-256.
- **Nothing on the load path is integrity-checked.** No signature, checksum, or
  content hash validates the image; the cipher is unauthenticated. (BLAKE3 shows
  up only as a compile-*cache* key.) The loader will even read a *directory* where
  the ZIP normally sits. Whether it accepts a *modified* image is the one open,
  untested question. See [the load-mechanism doc](docs/NITRO_LOAD_MECHANISM.md).
- **The analog modeling is readable.** GrandLPF is a real Moog ladder, Sallen-Key
  exposes tanh-vs-class-A saturation, and MS20 models diode-clip resonance. What
  you read is the actual per-sample math, not opcodes.
- **Recovery is complete, not partial.** Over 170,000 AST nodes across the whole
  module set parse to a clean EOF, including the device-specific filters that
  older tooling left as empty stubs.

## Status & limitations

- **The read/build side is solved and offline.** Every module in a current
  shipped image decrypts, parses to a clean EOF, and re-serializes
  byte-for-byte identically to its decrypted input. The whole module set round
  trips.
- **A clean parse proves the grammar, not loader acceptance.** That the
  toolchain reproduces the bytes says the format is understood. It does not
  prove Bitwig's loader would accept a *modified* module. That is a separate,
  live, untested question. See
  [docs/NITRO_LOAD_MECHANISM.md](docs/NITRO_LOAD_MECHANISM.md).
- **Key extraction runs against your own install, not this repo.** Both keys are
  materialized at runtime — neither appears anywhere in Bitwig's jars, in any
  encoding — so there is no static or offline key recovery. Recovery goes
  through a small controller extension this project bundles: you install it, add
  it once in Bitwig, and it dumps the key for the CLI to pick up
  (`nitro-extract-keys --install-controller`, then `--live`). That live step
  loads inside your own licensed Bitwig and is yours to run and confirm; the Dag
  key (for `0004` documents) is entered manually. See
  [docs/KEY_EXTRACTION.md](docs/KEY_EXTRACTION.md).
- **Repacked-image loader acceptance is unproven.** Repacking the archive is
  byte-exact offline, but whether Bitwig loads your repack is one restart-gated
  experiment that this project does not perform for you. Keep a verified backup
  of the original image before writing to your install.
- **`nitro-std` (stdlib source) needs a live JVM — not offline.** The compiled
  `nitro-image` modules decrypt fully offline (self-inverse Dag/XOR cipher). The
  `nitro-std` *source* archive is different: each member is wrapped in a runtime
  PRNG stream cipher that is not reproducible offline, so it is decrypted inside
  a running Bitwig via the bundled **Nitro Std Dump** controller
  (`nitro-decrypt-std`), which walks the archive through
  `NitroFile`'s `(ctx, byte[]) -> java.io.Reader` source-decrypt method and
  writes the plaintext tree to your disk. The decrypt mechanism is verified on
  Bitwig 6.0.11 (121/121 members).
- **`0004` document files decrypt at the metadata level.** The Dag key opens
  the readable metadata; a file body may sit behind a further layer this key
  does not open.

## Documentation

- [docs/NITRO_BINARY_PROTOCOL.md](docs/NITRO_BINARY_PROTOCOL.md): the
  `.nitrobin` byte format (frame markers, string interning, file frame).
- [docs/NITRO_AST.md](docs/NITRO_AST.md): the AST model, grammar tables, and
  the decompile/pretty-print pipeline.
- [docs/NITRO_LOAD_MECHANISM.md](docs/NITRO_LOAD_MECHANISM.md): how Bitwig
  compiles and loads Nitro, and what injection is and is not reachable.
- [docs/KEY_EXTRACTION.md](docs/KEY_EXTRACTION.md): where the two keys live and
  how to extract them from your own install.
- [docs/REGENERATING_THE_CORPUS.md](docs/REGENERATING_THE_CORPUS.md): the full
  local decrypt-decompile-atlas flow.

## Contributing

Contributions to the tools and the format specs are welcome. Do not commit any
key, any decrypted Bitwig content, or any corpus or atlas output; those are
local artifacts and stay gitignored. Runtime code is standard-library only,
Python 3.10+. Tests run under `pytest` (dev extra).

## License

MIT. See [LICENSE](LICENSE).

## Credits

Built from clean-room reverse engineering of Bitwig Studio's Nitro format:
disassembling the format's own serializer and loader to recover the byte
grammar and the AST tables, and reading the on-disk artifacts on a licensed
install. Bitwig Studio is a product of Bitwig GmbH; this project is
independent and unaffiliated, and distributes none of Bitwig's code or content.
