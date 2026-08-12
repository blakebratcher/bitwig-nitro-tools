# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Runtime key extraction via a bundled controller.** `nitro-extract-keys
  --install-controller` copies the bundled `BitwigNitroKeyDump.control.js`
  controller (shipped as package data) into Bitwig's Controller Scripts
  directory (override with `--controllers-dir`).
  Loading it in Bitwig reflects over the running engine and writes a key dump to
  `~/.bitwig-nitro/nitro-key-dump.json` (override with `BITWIG_NITRO_KEYDUMP`).
  `nitro-extract-keys --live` reads that dump, selects and — against an
  installed `nitro-image` — validates the nitro-image key, then writes
  `keys.json` (`--image` points at a specific image; `--force` writes despite a
  failed validation). Verified end-to-end on Bitwig 6.0.11: the controller loads
  and dumps the cipher chain (with IV sizes), and `--live` selects the key by IV
  size `198` and validates it against the installed nitro-image.

### Changed

- **Corrected the key-recovery docs.** `docs/KEY_EXTRACTION.md` and the README
  previously implied the nitro-image key was statically recoverable from a
  transform-chain definition in `bitwig.jar`. Verified false: neither the
  nitro-image key nor the Dag key appears in `bitwig.jar`, `libs.jar`, or
  `lwjgl.jar` in any form (raw, hex, or base64), nor in the native binaries —
  both are materialized only at runtime, so static jar recovery is impossible.
  The docs now describe the bundled-controller flow as the proven route, label
  live verification as the user's own step, and keep the transform-chain
  disassembly only as structure-only background.

### Removed

- Dropped the `--from-jar` flag from `nitro-extract-keys`; it advertised a
  static-recovery path that does not exist.

## [0.1.0] - 2026-08-12

Initial public release. A standard-library-only toolchain for working with
Bitwig's Nitro DSP binary format, offline, on your own machine.

### Added

- **`dag_cipher`**: Dag stream cipher for Bitwig `0004`-encoded files:
  `dag_decrypt`, `decrypt_0004`, `read_encrypted_btwg`. Ships no keys.
- **`keys`**: runtime key resolution (`resolve_dag_key`,
  `resolve_nitro_image_key`, `write_keys_file`, `MissingKeyError`). Keys come
  from environment variables or a local `keys.json`; a `MissingKeyError` names
  the variable to set when a key is absent. No key is ever embedded as a
  default.
- **`paths`**: install and data-path discovery (`packaged_data_dir`,
  `bitwig_install_roots`, `nitro_image_install_path`, `keys_search_paths`,
  `local_output_dir`).
- **`nitro_image`**: read, decrypt, encrypt, and repack the `nitro-image`
  archive of per-entry-encrypted DSP modules (`read_image`, `read_entry`,
  `write_image`, `decrypt_entry`, `encrypt_entry`, `NitroImage`).
- **`nitrobin_parser`**: faithful binary reader for `.nitrobin` files
  (`parse_nitrobin`, `parse_nitrobin_file`, `parse_all`, `NitroBinModule`).
- **`nitrobin_writer`**: serializer that round-trips a parsed tree back to
  bytes (`serialize_nitrobin`, `serialize_nitrobin_file`).
- **`nitrobin_decompiler`**: spec-driven full-AST decompiler
  (`decompile_nitrobin`, `decompile_nitrobin_file`, `AstNode`).
- **`nitro_pretty`**: pretty-printer that emits readable pseudo-source from a
  decompiled AST (`pretty_print`).
- **`nitro_edit`**: locate and mutate numeric literals in a compiled module
  with a same-size guarantee (`find_constants`, `get_constant`, `set_constant`,
  `mutate_constant_bytes`, `ConstantRef`, and path helpers).
- **`nitro_builder`**: small AST construction helpers for building nodes by
  hand (`int_lit`, `float_lit`, `bool_lit`, `str_lit`, `id_`, `block`, `add`,
  `sub`, `mul`, `div`, `list_builders`, `builder_doc`).
- **AST grammar tables**: `nitro_ast_tags.json` and
  `nitro_ast_class_methods.json` bundled as package data; regenerable via the
  `nitro-build-ast-tables` command.
- **Command line tools**: `nitro-decompile`, `nitro-extract-keys`,
  `nitro-decrypt-corpus`, `nitro-build-atlas`, `nitro-build-ast-tables`,
  `nitro-validate`.
- **Docs**: format specs for the Nitro binary protocol and the reverse
  engineering notes behind the toolchain, plus `docs/KEY_EXTRACTION.md` for
  bringing your own keys.

### Notes

- Runtime code depends on the Python standard library only.
- The project redistributes no Bitwig cipher keys and no decrypted Bitwig
  content. Decryption is bring-your-own-install.

[Unreleased]: https://github.com/blakebratcher/bitwig-nitro-tools/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/blakebratcher/bitwig-nitro-tools/releases/tag/v0.1.0
