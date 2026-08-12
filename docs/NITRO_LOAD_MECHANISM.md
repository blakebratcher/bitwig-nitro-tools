# How Bitwig loads Nitro DSP

This describes how Bitwig discovers, decrypts, compiles, and runs its Nitro DSP
modules, and where in that chain `bitwig_nitro` can and cannot reach. It is a
map of the mechanism, drawn from disassembling the loader and from reading the
on-disk artifacts on a licensed install. Everything here is offline
observation. Nothing in this document has been tested by feeding a modified
image back to a running Bitwig, and where that matters it is called out.

## The compilation pipeline

Nitro goes through three stages from source text to running code:

1. **Source to AST.** Nitro source (`.nitro`) is parsed by Bitwig's ANTLR-based
   parser into a syntax tree. That tree is serialized to `.nitrobin` (the
   TrV-framed binary described in
   [NITRO_BINARY_PROTOCOL.md](NITRO_BINARY_PROTOCOL.md)) and packed into the
   shipping `nitro-image` archive. This is the stage the toolchain reads: the
   `.nitrobin` payloads are pre-compiled ASTs, not machine code.

2. **AST to LLVM IR.** On first use, Bitwig lowers each module's AST to LLVM IR
   *text* and caches the result on disk, keyed by a content hash. The cached
   files are ordinary LLVM IR: a `source_filename` line, type and global
   declarations, and function definitions, followed by a small table of C
   symbol names (constructor, process, reset, and so on).

3. **LLVM IR to native.** An embedded LLVM JIT compiles the IR to native
   machine code at runtime. The result runs inline on the audio thread.

So the `.nitrobin` you decompile is the compiler's front-end output. The
back-end (IR lowering and JIT) happens inside Bitwig on demand.

## The nitro-image container

`<install>/Library/nitro-image` is a **plain ZIP archive**, not an encrypted
blob. It starts with the usual `PK\x03\x04` local-file signature and contains
hundreds of members named by DSP path (for example `filter/SallenKey.nitrobin`
or `grid/level/grid_level_pinch.nitrobin`). The ZIP itself is not encrypted;
what is encrypted is each member's stored payload.

Each member payload has this layout:

```
[version: 1 byte] [iv: 198 bytes] [ciphertext]
```

To recover the plaintext `.nitrobin`:

```
plaintext = dag_decrypt(ciphertext, key=<nitro-image key>, iv=<the 198-byte iv>)
```

The cipher is a keystream XOR, which means it is **self-inverse**: encrypting
and decrypting are the same operation with the same key and IV. There is no
separate encrypt routine. This is why re-packing is even possible.

The `bitwig_nitro.nitro_image` module implements the whole container:

```python
from bitwig_nitro import read_image, read_entry, NitroImage

img   = NitroImage.load()                              # installed image
names = img.to_dict().keys()                           # member names
plain = img.plaintext("filter/SallenKey.nitrobin")     # decrypted bytes
```

Reading requires the nitro-image key, which you supply yourself (see
[KEY_EXTRACTION.md](KEY_EXTRACTION.md)). The package ships no keys.

### nitro-std is a different layer

`<install>/Library/nitro-std` (the stdlib *source*, not the compiled image) is
also a plain ZIP, and its members also carry a one-byte version prefix, but the
198-byte-IV recipe above does **not** decrypt them. It uses a different
transform in the cipher chain. Offline decryption of `nitro-std` is not solved
here; only `nitro-image` is. If you want the compiled DSP, `nitro-image` is the
one you can read end to end.

## Verified round-trip

Measured offline against a current shipped image:

- Every member decrypts to a well-formed `.nitrobin` that parses to clean EOF.
- Every member re-encrypts byte-for-byte identically to its stored bytes.
- An identity re-pack of the whole archive reproduces the original file
  exactly (matching SHA-256). This depends on copying each untouched member's
  stored deflate bytes and ZIP records verbatim rather than re-compressing,
  because a handful of members do not reproduce under a stock `zlib` level.

That last point matters for any tool that rewrites the archive: preserve the
compressed bytes of members you did not touch. `write_image` does this.

## Editing a module in place

`bitwig_nitro.nitro_edit` addresses individual numeric literals in a
decompiled AST and guarantees a same-size rewrite. Float literals are
fixed-width, so mutating one leaves the plaintext length, and therefore the
encrypted member length, unchanged. Combined with `write_image`, you can
produce a repacked archive that is byte-identical to the original except for
the exact members you edited.

## The loader

Bitwig's loader resolves `<install>/Library/nitro-image` and branches on what
it finds there:

- If the path is a **file**, it is read as a ZIP archive (the shipping case).
- If the path is a **directory**, it is read by a separate loose-tree reader.
- If neither is present, startup fails with an error naming `library/nitro/`
  or `library/nitro-image` as required.

The directory branch is a real, second load route, distinct from overwriting
the ZIP. What the loose-tree reader expects on disk (encrypted member bytes
versus plaintext, and the exact filename layout it scans) has not been decoded
here, so treat it as an observed branch, not a usable path yet.

### No integrity check

The loader and both readers contain no digest, signature, checksum, or hash
verification of the image. The cipher is unauthenticated XOR. The only version
gate on this path is a project-version string that invalidates the *compile
cache*; it does not validate the image file.

A malformed image is still fatal at startup, so a verified backup outside the
install tree is mandatory before swapping anything.

## The compile cache

The AST-to-IR cache lives under the user cache directory, keyed by a 64-hex
content hash. Each cache entry is the LLVM IR text plus a symbol table. The
cache is self-healing: a corrupt or unrecognized entry is discarded and
recompiled rather than loaded.

The **cache-key hash function and its exact input are not identified.** SHA-256
over the plaintext `.nitrobin`, over the encrypted ZIP member, and over the
cached IR body all fail to match observed cache filenames. A content hash
elsewhere in Bitwig (BLAKE3) is a candidate but untested. This is the missing
piece for any approach that would replace a cache entry directly, since you
cannot address the entry you want without reproducing its key.

## Injection: what is and is not reachable

Reading and rewriting the image is solved offline. Getting Bitwig to *load* a
rewrite is the open question.

| Approach | What you inject | Status |
|----------|-----------------|--------|
| Repack the ZIP | A mutated or authored `.nitrobin` via `serialize_nitrobin` + `write_image` | Offline-proven, loader-acceptance untested. The cipher and serializer round-trips are byte-exact against the shipped image; whether Bitwig accepts *your* repack is one restart-gated experiment, not done here. Replacing an existing member by name is inference, not proof; adding a brand-new module additionally needs the module-registry binding, which is not decoded. |
| Loose directory | The same, laid out as a directory tree | Untested. The loader branches on `isDirectory()`, so the route exists, but the on-disk layout the loose-tree reader expects is undecoded. |
| Replace a cache entry | Hand-authored LLVM IR at a known cache key | Blocked. The cache-key hash and its input are unidentified, so you cannot address the entry, and a wrong guess is silently discarded. |
| New cache entry | New IR under a new source hash | Blocked. Bitwig only requests hashes its module registry knows about. |
| Source-level | User `.nitro` text picked up at startup | Unknown. The ANTLR source parser still ships in the jar, so the compiler is present, but no user-scope source path has been found. |

## What this does unlock

Even with live injection unproven, the read and build side is complete and
offline:

- **Read every module's DSP.** Decrypt the image, decompile each `.nitrobin`,
  and pretty-print it to Nitro pseudo-source.
- **Author or mutate modules.** The AST serializer is round-trip-proven, and
  `nitro_edit` mutates constants at fixed size.
- **Rebuild the image around a change**, byte-identical except for the members
  you touched.

The one remaining unlock is a single live question: does Bitwig's loader accept
a repacked image? Both round-trips it depends on are proven offline; the answer
is a restart-gated test on your own machine, not a research program.

Note also that a Bitwig update rewrites `nitro-image`, so any injected change
is reverted by the next update. Never treat an injected module as durable
state, and always keep a verified backup of the original image.
