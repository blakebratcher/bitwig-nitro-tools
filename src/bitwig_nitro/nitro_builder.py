"""Python DSL for authoring Nitro DSP modules programmatically.

Wraps the AST classes from the packaged `nitro_ast_class_methods.json`
as Python functions that return `AstNode` instances. Output composes into
a tree that `nitrobin_writer.serialize_nitrobin` can emit byte-identically
to a `.nitrobin` file.

Three layers:

    1. **Raw builders**: one function per AST class, snake_case name,
       positional args mirror the class's read_sequence (149 builders
       total, auto-generated). Singleton types take no arguments.

    2. **Convenience aliases**: short names for the most common builders
       (`f32`, `i32`, `bool_t`, `lit`, `id_`, `block`, `add`, `mul`, …).

    3. **High-level patterns**: `Module()`, `Struct()`, `Function()`
       context-manager helpers for common DSP shapes.

Usage:

    from bitwig_nitro import nitro_builder as nb
    from bitwig_nitro.nitrobin_writer import serialize_nitrobin

    # Recreate the dummy_component.bwbin module
    ast = nb.nitro_file([nb.component_declaration("dummy", [])])
    data = serialize_nitrobin(ast)

The DSL is **structurally exact**: every positional child corresponds to
a real AST node, no schema invention. This guarantees the output matches
what Bitwig would produce if it parsed equivalent source.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .nitrobin_decompiler import (
    AstNode,
    _CLASS_TO_SEQ,
    _CLASS_SINGLETON,
    _IS_DECLARATION,
)
from .paths import packaged_data_dir

# ---------- Tag lookup ----------

_TAGS_PATH = packaged_data_dir() / "nitro_ast_tags.json"
_TAG_BY_CLASS: dict[str, int] = {}
for _entry in json.loads(_TAGS_PATH.read_text())["registered"].values():
    _TAG_BY_CLASS[_entry["ast_class"]] = int(_entry["tag_int"])


# ---------- Class name → snake_case helper ----------

_CAMEL_SPLIT = re.compile(r"(?<!^)(?=[A-Z])")


def _to_snake(name: str) -> str:
    """`ComponentDeclaration` → `component_declaration`."""
    return _CAMEL_SPLIT.sub("_", name).lower()


# ---------- Builder factory ----------

def _make_builder(class_name: str):
    tag = _TAG_BY_CLASS.get(class_name)
    if tag is None:
        return None  # AST class registered but no tag (shouldn't happen for the 148)
    is_singleton = _CLASS_SINGLETON.get(class_name, False)
    is_decl = class_name in _IS_DECLARATION
    seq = _CLASS_TO_SEQ.get(class_name, [])
    real_ops = [s for s in seq if s.get("op") not in ("super_init", "store_field")]
    n_args = len(real_ops)

    def builder(*children, decl_suffix: Iterable[AstNode] | None = None) -> AstNode:
        if is_singleton:
            if children or decl_suffix:
                raise ValueError(
                    f"{class_name} is a singleton, no children or decl_suffix allowed"
                )
            return AstNode(kind=class_name, tag=tag, children=[], is_singleton=True)
        if len(children) != n_args:
            raise ValueError(
                f"{class_name} expects {n_args} positional children, got {len(children)}"
            )
        kids: list[Any] = list(children)
        if is_decl:
            kids.append({"_decl_suffix": list(decl_suffix or [])})
        return AstNode(kind=class_name, tag=tag, children=kids, is_singleton=False)

    builder.__name__ = _to_snake(class_name)
    builder.__qualname__ = builder.__name__
    builder.__doc__ = (
        f"AST builder for `{class_name}` (tag 0x{tag:04X}).\n\n"
        f"Positional children: {n_args}\n"
        f"Is singleton: {is_singleton}\n"
        f"Is Declaration subclass (carries decl_suffix): {is_decl}\n\n"
        f"Read sequence (from class_methods spec):\n"
        + "\n".join(f"  - {op['op']}" for op in real_ops)
    )
    return builder


# ---------- Populate module namespace ----------

_BUILDERS: dict[str, Any] = {}
for _cls in _TAG_BY_CLASS:
    _fn = _make_builder(_cls)
    if _fn is not None:
        _BUILDERS[_fn.__name__] = _fn

# Inject all builders as module globals (e.g. nb.component_declaration, nb.add_expression).
globals().update(_BUILDERS)


# ---------- Convenience type-token aliases ----------

# Common scalar types as no-arg helpers (singleton AST nodes).
def f32() -> AstNode: return float32_type()  # noqa: F405,E704
def f64() -> AstNode: return float64_type()  # noqa: F405,E704
def i8() -> AstNode: return int8_type()      # noqa: F405,E704
def i16() -> AstNode: return int16_type()    # noqa: F405,E704
def i32() -> AstNode: return int32_type()    # noqa: F405,E704
def i64() -> AstNode: return int64_type()    # noqa: F405,E704
def u8() -> AstNode: return u_int8_type()    # noqa: F405,E704
def u16() -> AstNode: return u_int16_type()  # noqa: F405,E704
def u32() -> AstNode: return u_int32_type()  # noqa: F405,E704
def u64() -> AstNode: return u_int64_type()  # noqa: F405,E704
def bool_t() -> AstNode: return boolean_type()  # noqa: F405,E704
def void_t() -> AstNode: return void_type()     # noqa: F405,E704
def int_lit_t() -> AstNode: return integer_literal_type()    # noqa: F405,E704
def float_lit_t() -> AstNode: return float_literal_type()    # noqa: F405,E704


# ---------- Literal helpers ----------

def int_lit(value: int, type_tok: AstNode | None = None) -> AstNode:
    """Integer literal expression. Defaults to `IntegerLiteralType` if no type given."""
    t = type_tok if type_tok is not None else int_lit_t()
    return integer_value_expression(t, int(value))  # noqa: F405


def float_lit(value: float, type_tok: AstNode | None = None) -> AstNode:
    """Float literal expression. Defaults to `FloatLiteralType` if no type given."""
    t = type_tok if type_tok is not None else float_lit_t()
    return float_value_expression(t, float(value))  # noqa: F405


def bool_lit(value: bool) -> AstNode:
    """Boolean literal expression."""
    return boolean_value_expression(bool(value))  # noqa: F405


def str_lit(value: str) -> AstNode:
    """String literal expression."""
    return string_value_expression(value)  # noqa: F405


def id_(name: str) -> AstNode:
    """Identifier expression, refers to a name in scope."""
    return identifier_expression(name)  # noqa: F405


# ---------- Statement / expression sugar ----------

def block(stmts: Iterable[AstNode], list0: Iterable[AstNode] | None = None,
          list1: Iterable[AstNode] | None = None) -> AstNode:
    """BlockStatement with three child lists.

    Empirical layout (verified against `drum_logic_not.dec`): statements
    go in the THIRD list. The first two lists are typically empty in
    simple modules; they likely hold scope-level declarations and
    labels respectively, though exact semantics are not yet confirmed
    against more complex modules.
    """
    return block_statement(list(list0 or []), list(list1 or []), list(stmts))  # noqa: F405


def add(lhs: AstNode, rhs: AstNode) -> AstNode:
    return add_expression(lhs, rhs)  # noqa: F405


def sub(lhs: AstNode, rhs: AstNode) -> AstNode:
    return sub_expression(lhs, rhs)  # noqa: F405


def mul(lhs: AstNode, rhs: AstNode) -> AstNode:
    return mul_expression(lhs, rhs)  # noqa: F405


def div(lhs: AstNode, rhs: AstNode) -> AstNode:
    return div_expression(lhs, rhs)  # noqa: F405


# ---------- Public introspection ----------

def list_builders() -> list[str]:
    """All auto-generated AST builder names available on this module."""
    return sorted(_BUILDERS.keys())


def builder_doc(name: str) -> str:
    """Documentation for a specific AST builder."""
    if name not in _BUILDERS:
        raise KeyError(name)
    return _BUILDERS[name].__doc__ or ""
