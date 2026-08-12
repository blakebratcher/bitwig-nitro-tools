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

### 1. The nitro-image key

Decrypts the member payloads inside `<install>/Library/nitro-image` (the
compiled DSP archive; see
[NITRO_LOAD_MECHANISM.md](NITRO_LOAD_MECHANISM.md)). This is the key you need
to read compiled modules.

Where it lives: Bitwig's cipher is built from a **chain of stream transforms**
defined in `bitwig.jar`. Each transform is an instance of the PRNG-based stream
cipher class (obfuscated as `BIa` on the builds examined) and carries a key
field (`key_Xzy`) and an IV-size field (`iv_size_uEK`). The nitro-image
payloads use the transform entry whose **`iv_size_uEK == 198`**. That entry's
`key_Xzy` is the nitro-image key (a short byte string, on the order of 99 bytes
on the builds examined). There is a second, sibling entry that carries the
*same* key with `iv_size_uEK == 0`; select by the IV-size field (198), not by
position in the chain, because the order is not guaranteed stable across
releases.

This key sits in the static transform-chain definition, so it is the more
mechanically recoverable of the two.

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

The `nitro-extract-keys` command attempts to recover the keys from a Bitwig
install and write a `keys.json`:

```bash
nitro-extract-keys            # locate the install, recover what it can, report
```

Automated extraction is **best-effort.** The nitro-image key, living in the
static transform chain, is the one most likely to come out cleanly. The Dag
key, materialized at runtime, may not be statically recoverable at all on a
given build; when that happens the command tells you what it could not find and
you fall back to the manual path below.

## Manual and semi-automated extraction

This is the documented, reliable path. It requires a JDK for `javap` (or a
class-file parser) and, for the Dag key, a way to read the running JVM's object
graph.

**Finding the nitro-image key (static).**

1. Unzip `bitwig.jar` and locate the package that holds the stream-transform
   classes (in `com.bitwig.nitro` / the base I/O package). The relevant classes
   are the abstract transform base, a PRNG-based stream cipher, and a
   transform-chain wrapper.
2. Disassemble the cipher and chain classes (`javap -p -c <class>`), or parse
   them with a class-file reader. Enumerate the transform-chain entries and read
   each entry's key field and IV-size field.
3. Select the entry whose IV size is **198**. Its key field is the
   nitro-image key. Emit it as hex.

**Finding the Dag key (runtime).**

The Dag key is not in static data, so recover it from a running instance:

1. Reach the cipher object at runtime through the `ZKE.uEK -> BIa.Xzy` field
   chain (again, obfuscated names shift between releases; identify the classes
   by role, not by name, using the static disassembly as a map).
2. Read the key bytes from the live object and emit them as hex.

A common way to get code into the JVM without patching the app is Bitwig's
public controller extension API, which loads your code inside the running
process where it can reflect over application objects. The native side is worth
understanding as background:
disassembly of the audio engine binary shows the cipher (registered under the
name `BIa`) is a small object constructed by a thread-safe factory, with the
key material staged through runtime state rather than baked into the class
file. That is the reason the static route works for the nitro-image key but not
for the Dag key.

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
