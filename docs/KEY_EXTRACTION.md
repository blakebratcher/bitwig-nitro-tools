# Extracting the cipher keys

`bitwig_nitro` ships **no cipher keys and no decrypted Bitwig content.** To
decrypt anything you supply the keys yourself, extracted from your own licensed
Bitwig installation. This document tells you *where the keys live* and how to
recover them. It documents the location and format of the keys, never the key
material itself.

If you do not have a licensed Bitwig install, this toolchain can still parse,
serialize, edit, and pretty-print `.nitrobin` bytes you already have in
plaintext; it just cannot decrypt anything for you.

## The two keys

There are two independent keys, for two independent cipher surfaces.

**Both keys are materialized at runtime.** Neither the nitro-image key nor the
Dag key is stored as static data. Neither appears in `bitwig.jar`, `libs.jar`,
or `lwjgl.jar` in any form — not raw, not hex, not base64 — nor in the native
binaries; this was checked directly. A plain disassembly or a `grep` over the
jars will not hand you either key. **Static jar recovery is impossible.** The
only proven way to recover a key is to read it out of a running Bitwig JVM,
which is what the bundled controller extension does (see
[Running nitro-extract-keys](#running-nitro-extract-keys)).

### 1. The nitro-image key

Decrypts the member payloads inside `<install>/Library/nitro-image` (the
compiled DSP archive; see
[NITRO_LOAD_MECHANISM.md](NITRO_LOAD_MECHANISM.md)). This is the key you need
to read compiled modules.

Where it lives: at runtime, Bitwig builds its cipher from a **chain of stream
transforms**. Each transform is an instance of a PRNG-based stream cipher class
(obfuscated as `BIa` on the builds examined) and carries a key field
(`key_Xzy`) and an IV-size field (`iv_size_uEK`). The nitro-image payloads use
the transform entry whose **`iv_size_uEK == 198`**; that entry's `key_Xzy` holds
the nitro-image key (a short byte string, on the order of 99 bytes on the builds
examined). A sibling entry carries the *same* key value with `iv_size_uEK == 0`,
so select by the IV-size field (198), not by position in the chain — the order
is not guaranteed stable across releases.

**This key is not in the jar.** The transform *classes* are defined in
`bitwig.jar`, but their key bytes are populated at runtime; the 99-byte value
does not appear anywhere in `bitwig.jar`, `libs.jar`, or `lwjgl.jar`, in any
encoding (this was checked). You recover it the same way you recover the Dag
key: by reading the live transform objects out of a running JVM. There is no
static shortcut.

### 2. The Dag key

Decrypts Bitwig's `0004`-encoded document files (the encrypted `BtWg`
container). `decrypt_0004` / `read_encrypted_btwg` use it. On these files the
Dag key recovers the readable metadata section; the file body may sit behind a
further layer that this key does not open, so treat `0004` support as
metadata-level.

Where it lives: the Dag key is reached in the running JVM through the field
chain `ZKE.uEK -> BIa.Xzy` (obfuscated class and field names, which shift
between releases). It is **not** stored as static class data. It is materialized
into Java runtime objects that are initialized by native code, which means a
plain static disassembly will not hand you the bytes; you recover it from a
live object graph.

## The cipher, for context

The Dag cipher is a keystream XOR:

```
SWC = key[16:]           # keystream key material
azd = iv + key[:16]      # per-file pad seed

transform(byte):         # the same routine encrypts and decrypts
    ... ^ SWC[i] ^ rotate_right(azd[j], counter & 7)
```

Because the per-position keystream byte depends only on the key, IV, and
position (never on the data), the transform is its own inverse. That is why
`bitwig_nitro` can re-encrypt a modified module by calling the same decrypt
routine. `dag_decrypt(data, key, iv)` in `bitwig_nitro.dag_cipher` implements
it; you provide `key` and `iv`.

## keys.json

Once you have the two keys as hex strings, put them in a `keys.json`:

```json
{
  "dag_key": "<hex>",
  "nitro_image_key": "<hex>"
}
```

`bitwig_nitro.keys` resolves each key, in this order:

1. an environment variable holding a hex string:
   `BITWIG_NITRO_DAG_KEY` or `BITWIG_NITRO_IMAGE_KEY` (a direct override);
2. a `keys.json`, located via `BITWIG_NITRO_KEYS` (a full path), then
   `./keys.json` in the current directory, then `keys.json` in the per-user
   config directory, then `~/.config/bitwig-nitro/keys.json` as a portable
   fallback.

The per-user config directory follows the platform convention:
`%APPDATA%\bitwig-nitro` on Windows, `$XDG_CONFIG_HOME/bitwig-nitro` where
that variable is set, and `~/.config/bitwig-nitro` everywhere else. Set
`BITWIG_NITRO_CONFIG` to a directory to override it; the `keys.json` lookup
and `write_keys_file` then use that directory instead.

If neither source provides the requested key, `resolve_dag_key()` /
`resolve_nitro_image_key()` raise `MissingKeyError` with a message naming the
environment variable and pointing back here.

You can write the file programmatically once you have the hex:

```python
from bitwig_nitro import write_keys_file
write_keys_file(dag_key_hex="...", image_key_hex="...")
# -> keys.json in the per-user config dir  (validates the hex before writing)
```

Keep `keys.json` out of version control. It is your key material, tied to your
license.

## Running nitro-extract-keys

Because both keys live only in a running JVM, recovery goes through a small
Bitwig **controller extension** that this project bundles:
`controller/BitwigNitroKeyDump.control.js`. You install it, add it once in
Bitwig, let it write the key out, and the CLI reads that dump and writes your
`keys.json`:

```bash
# 1. copy the bundled controller into Bitwig's Controller Scripts directory
nitro-extract-keys --install-controller
#    (override the destination with --controllers-dir DIR)

# 2. in Bitwig: Settings -> Controllers -> Add -> "bitwig-nitro-tools /
#    Nitro Key Dump". On load it reflects over the running engine, writes the
#    key dump, and shows a popup. You can remove the controller afterward.

# 3. read the dump and write keys.json
nitro-extract-keys --live
#    --image PATH   validate against a specific nitro-image
#    --force        write even if the key fails to validate
```

The controller writes its dump to `~/.bitwig-nitro/nitro-key-dump.json`
(override with the `BITWIG_NITRO_KEYDUMP` environment variable, which both the
controller and the CLI honor). `--live` reads that file, selects the nitro-image
key from it (preferring the transform whose IV size is 198, falling back to a
value shared across the transform entries), and — if a `nitro-image` is
installed — validates the key by decrypting one member and confirming it parses
cleanly before writing `keys.json`. If the dump is missing, `--live` exits with
the install instructions above.

This is the proven recovery approach, but the live half is **yours to run**: the
controller has to load inside your own licensed Bitwig, on your own machine. The
reflection technique it uses was ported from a prototype proven on a 6.0.x
build; the shipped controller itself has not been run inside Bitwig by this
project, so whether it loads and materializes the key cleanly on your build is
what your own run confirms. See
[controller/README.md](../controller/README.md) for the controller's own notes.

The Dag key (used for `0004` document files) is not recovered by this flow;
supply it manually with `--dag-key` when you need it.

## Manual key entry

If you already have the key hex — from your own controller dump, or recovered
some other way against your own install — write it in directly, without the
controller flow:

```bash
nitro-extract-keys --image-key <hex>              # nitro-image key
nitro-extract-keys --dag-key <hex>                # Dag key (0004 documents)
nitro-extract-keys --dag-key <hex> --image-key <hex>
```

Both flags validate the hex and write `keys.json`; supply one or both.

## Transform-chain structure (background, not a key-recovery method)

The material below maps the cipher classes so you can identify them **by role**
in a live object graph. It is structure only: it does **not** recover a key,
because the key bytes are not in the class files — they are populated at runtime.
Use it to understand what the controller reflects over, or to aim your own
runtime reflection at the right objects.

Unzip `bitwig.jar` and locate the package that holds the stream-transform
classes (in `com.bitwig.nitro` / the base I/O package): the abstract transform
base, a PRNG-based stream cipher, and a transform-chain wrapper. Disassemble
them (`javap -p -c <class>`), or parse them with a class-file reader, to read the
chain's shape — each entry's key field (`key_Xzy`) and IV-size field
(`iv_size_uEK`), and the `iv_size_uEK == 198` selector that marks the
nitro-image entry. For the Dag key the live object is reached through the
`ZKE.uEK -> BIa.Xzy` field chain (obfuscated names shift between releases;
identify the classes by role, not by name, using the disassembly as a map).

What you will **not** find in the class files is either key value. On disk those
fields are empty/default; the bytes are staged in at runtime. Disassembly of the
audio engine binary shows the cipher (registered under the name `BIa`) is a
small object constructed by a thread-safe factory, with the key material staged
through runtime state rather than baked into the class file. That is why there
is no static route for either key, and why recovery goes through a live JVM.

**Verify.**

Once you have written `keys.json`, confirm the nitro-image key by decrypting one
member and parsing it:

```python
from bitwig_nitro import read_entry, decompile_nitrobin
plain = read_entry(None, "filter/SallenKey.nitrobin")   # uses nitro_image_key
ast   = decompile_nitrobin(plain)                        # clean parse == right key
```

A wrong key yields high-entropy garbage that fails to parse; a right key yields
a `.nitrobin` that parses to clean EOF.

## Reverse-engineering provenance

The location claims above come from disassembling `bitwig.jar` and the audio
engine binary on a licensed install. `bitwig.jar` is heavily obfuscated
(three-character class names that change per release), but enough structure is
recoverable to locate the transform chain and the cipher classes by role. The
key material itself is never included in this repository, and you should not
publish yours. Extract from your own install, keep the keys local, and use them
only against content you are licensed to run.
