"""The package's public surface is importable and complete.

Every name in ``bitwig_nitro.__all__`` must resolve, and the builder-DSL sugar
that the docs promise must be present. This is a cheap guard against a
re-export regression breaking downstream imports.
"""
from __future__ import annotations

import importlib

import bitwig_nitro


def test_all_names_importable() -> None:
    mod = importlib.import_module("bitwig_nitro")
    missing = [name for name in mod.__all__ if not hasattr(mod, name)]
    assert not missing, f"names in __all__ not importable: {missing}"


def test_version_present() -> None:
    assert isinstance(bitwig_nitro.__version__, str)
    assert bitwig_nitro.__version__


def test_core_callables_present() -> None:
    # A representative slice across every module the port re-exports.
    for name in (
        "dag_decrypt",
        "decrypt_0004",
        "resolve_dag_key",
        "resolve_nitro_image_key",
        "packaged_data_dir",
        "read_image",
        "decompile_nitrobin",
        "pretty_print",
        "parse_nitrobin",
        "serialize_nitrobin",
        "find_constants",
        "mutate_constant_bytes",
        "int_lit",
        "float_lit",
        "block",
        "list_builders",
    ):
        assert callable(getattr(bitwig_nitro, name)), name


def test_builder_dsl_helpers_round_out() -> None:
    """The hand-written literal/arithmetic sugar builds AstNodes."""
    lit = bitwig_nitro.float_lit(0.5)
    assert lit.kind == "FloatValueExpression"
    expr = bitwig_nitro.add(bitwig_nitro.int_lit(1), bitwig_nitro.int_lit(2))
    assert expr.kind == "AddExpression"
    assert len(expr.children) == 2
