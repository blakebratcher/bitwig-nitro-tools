"""Command-line entry points for bitwig-nitro-tools.

Each submodule exposes ``main(argv=None) -> int`` and is wired to a
``console_scripts`` entry point:

===================== ==============================
console script        callable
===================== ==============================
nitro-decompile       ``decompile.main``
nitro-validate        ``validate.main``
nitro-extract-keys    ``extract_keys.main``
nitro-decrypt-corpus  ``decrypt_corpus.main``
nitro-decrypt-std     ``decrypt_std.main``
nitro-build-atlas     ``build_atlas.main``
nitro-build-ast-tables ``build_ast_tables.main``
===================== ==============================

Every module imports only from :mod:`bitwig_nitro` and the Python standard
library, and returns a process exit code from ``main``.
"""
from __future__ import annotations

__all__: list[str] = []
