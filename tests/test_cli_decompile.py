"""CLI tests for ``nitro-decompile``, offline, key-free.

Exercises the three resolution routes (exact file path, corpus-relative module
name, and the not-found path) using the synthetic fixture. The installed-image
route is not exercised here because it needs a real Bitwig install + key; a
missing module deterministically returns exit code 2 whether or not an install
is present.
"""
from __future__ import annotations

from pathlib import Path

from bitwig_nitro.cli import decompile
from fixtures.builders import (
    SYNTHETIC_MODULE_NAME,
    build_synthetic_module_bytes,
)


def _write(corpus: Path, rel: str) -> Path:
    p = corpus / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(build_synthetic_module_bytes())
    return p


def test_decompile_exact_file_path(tmp_path, capsys):
    f = tmp_path / "SallenKey.nitrobin"
    f.write_bytes(build_synthetic_module_bytes())
    assert decompile.main([str(f)]) == 0
    assert SYNTHETIC_MODULE_NAME in capsys.readouterr().out


def test_decompile_resolves_module_name_from_corpus(tmp_path, capsys):
    corpus = tmp_path / "corpus"
    _write(corpus, "filter/SallenKey.nitrobin")
    # Bare name (no extension), resolved under --corpus.
    assert decompile.main(["filter/SallenKey", "--corpus", str(corpus)]) == 0
    assert SYNTHETIC_MODULE_NAME in capsys.readouterr().out


def test_decompile_ast_flag(tmp_path, capsys):
    corpus = tmp_path / "corpus"
    _write(corpus, "filter/SallenKey.nitrobin")
    assert decompile.main(["filter/SallenKey", "--corpus", str(corpus), "--ast"]) == 0
    out = capsys.readouterr().out
    assert "NitroFile" in out  # raw AST dump names node kinds


def test_decompile_missing_returns_2(tmp_path, capsys):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    rc = decompile.main(["does/not/exist", "--corpus", str(corpus)])
    assert rc == 2
    assert "error" in capsys.readouterr().err.lower()
