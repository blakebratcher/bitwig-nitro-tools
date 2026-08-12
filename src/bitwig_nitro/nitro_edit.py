"""Address and mutate numeric literals inside a decompiled Nitro AST.

``nitrobin_decompiler`` gives you an :class:`AstNode` tree whose children are
positional (field names are not recoverable from the bytecode specs), so
callers have historically hand-walked ``node.children`` hunting for float
leaves. This module makes that addressable and safe:

    from bitwig_nitro.nitro_edit import find_constants, mutate_constant_bytes

    consts = find_constants_in_bytes(pinch_bytes, kinds=("float",))
    # -> [ConstantRef(path=(0, 2, 1, 4, 0, 2, 1, 1, 0, 1), kind='float',
    #                 value=0.5, node_kind='FloatValueExpression')]
    new_bytes = mutate_constant_bytes(pinch_bytes, consts[0].path, 0.25)

Path scheme
-----------
A ``path`` is a tuple of child indices from the AST root. Each element indexes
into the current container: an :class:`AstNode`'s ``children`` list, or, when
the addressed child is itself a ``list`` of nodes, that list. So
``(0, 2, 1)`` means "root.children[0].children[2].children[1]", and a list
child simply consumes one extra index. :func:`format_path` /
:func:`parse_path` render it as ``"0.2.1"`` for CLIs.

Same-size guarantee
-------------------
Nitro's wire format stores every integer literal as a tagged big-endian i64
and every float literal as a tagged big-endian f64, both fixed width. So
changing a literal's VALUE (never its kind) cannot change the serialized
length. :func:`set_constant` enforces the kind invariant and
:func:`mutate_constant_bytes` asserts the length invariant, which is what lets
a mutated module be re-encrypted into a nitro-image entry of exactly the same
plaintext size.

Everything here is offline. Whether a mutated module is actually *loaded and
executed* by Bitwig is unproven and only a live probe settles it.
"""
from __future__ import annotations


from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .nitrobin_decompiler import AstNode, decompile_nitrobin
from .nitrobin_writer import serialize_nitrobin

__all__ = [
    "ConstantRef",
    "NitroEditError",
    "assert_same_size",
    "decompile_bytes",
    "find_constants",
    "find_constants_in_bytes",
    "format_path",
    "get_constant",
    "mutate_constant_bytes",
    "parse_path",
    "set_constant",
]

FLOAT = "float"
INT = "int"
_KINDS = (FLOAT, INT)


class NitroEditError(Exception):
    """Raised for bad paths, kind mismatches, and broken size invariants."""


@dataclass(frozen=True)
class ConstantRef:
    """A stable address for one numeric literal in an AST."""

    path: tuple[int, ...]
    kind: str  # "float" | "int"
    value: float | int
    node_kind: str  # owning AST class, e.g. "FloatValueExpression"

    @property
    def path_str(self) -> str:
        return format_path(self.path)

    def __str__(self) -> str:  # pragma: no cover, display only
        return f"{self.path_str}  {self.node_kind}.{self.kind} = {self.value!r}"


# --------------------------------------------------------------------------
# path helpers
# --------------------------------------------------------------------------


def format_path(path: Sequence[int]) -> str:
    """``(0, 2, 1)`` -> ``"0.2.1"``."""
    return ".".join(str(i) for i in path)


def parse_path(text: str) -> tuple[int, ...]:
    """``"0.2.1"`` -> ``(0, 2, 1)``. Raises on anything else."""
    parts = [p.strip() for p in str(text).split(".")]
    if not parts or any(not p for p in parts):
        raise NitroEditError(f"invalid constant path: {text!r}")
    out = []
    for p in parts:
        if not p.isdigit():
            raise NitroEditError(f"invalid constant path component {p!r} in {text!r}")
        out.append(int(p))
    return tuple(out)


def _resolve(ast: AstNode, path: Sequence[int]) -> tuple[list[Any], int]:
    """Return ``(container, index)`` for the leaf addressed by ``path``."""
    if not path:
        raise NitroEditError("empty constant path")
    container: list[Any] = ast.children
    for depth, idx in enumerate(path[:-1]):
        if not isinstance(idx, int) or idx < 0 or idx >= len(container):
            raise NitroEditError(
                f"path {format_path(path)} out of range at depth {depth} "
                f"(container has {len(container)} children)"
            )
        item = container[idx]
        if isinstance(item, AstNode):
            container = item.children
        elif isinstance(item, list):
            container = item
        else:
            raise NitroEditError(
                f"path {format_path(path)} cannot descend into "
                f"{type(item).__name__} at depth {depth}"
            )
    last = path[-1]
    if not isinstance(last, int) or last < 0 or last >= len(container):
        raise NitroEditError(
            f"path {format_path(path)} out of range at the leaf "
            f"(container has {len(container)} children)"
        )
    return container, last


def _kind_of(value: Any) -> str | None:
    if isinstance(value, bool):
        return None  # booleans are TRUE/FALSE frames, not numeric literals
    if isinstance(value, float):
        return FLOAT
    if isinstance(value, int):
        return INT
    return None


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------


def find_constants(
    ast: AstNode, kinds: Iterable[str] | None = None
) -> list[ConstantRef]:
    """Walk the AST and return every addressable numeric literal.

    Args:
        ast: Root node from ``decompile_nitrobin_file`` / :func:`decompile_bytes`.
        kinds: Restrict to ``"float"`` and/or ``"int"``. Default: both.

    Returns:
        Refs in depth-first order. Paths are unique and stable for a given
        AST shape (a value edit never changes the shape).

    CAVEAT: not every returned int is a DSP coefficient. This walk is purely
    structural: it returns any ``int``/``float`` leaf, and some AST classes
    carry ints that are *semantics*, not tunable constants. Measured over the
    first 120 corpus modules: ``FloatValueExpression`` holds 812/812 of the
    floats, but the ints split ``IntegerValueExpression`` 2762 /
    ``BreakStatement`` 57 / ``IsFpClassExpression`` 4. Editing a
    ``BreakStatement`` int is a control-flow change and editing an
    ``IsFpClassExpression`` int rewrites an fp-class mask; both keep the wire
    size but neither is a "constant" in the sense a caller usually means.
    Filter on ``ref.node_kind`` (``IntegerValueExpression`` /
    ``FloatValueExpression``) when you want literals only.
    """
    wanted = tuple(kinds) if kinds is not None else _KINDS
    for k in wanted:
        if k not in _KINDS:
            raise NitroEditError(f"unknown constant kind {k!r} (want {_KINDS})")

    found: list[ConstantRef] = []

    def visit(node: AstNode, prefix: tuple[int, ...]) -> None:
        for i, child in enumerate(node.children):
            here = prefix + (i,)
            if isinstance(child, AstNode):
                visit(child, here)
            elif isinstance(child, list):
                for j, item in enumerate(child):
                    there = here + (j,)
                    if isinstance(item, AstNode):
                        visit(item, there)
                    else:
                        kind = _kind_of(item)
                        if kind in wanted:
                            found.append(
                                ConstantRef(there, kind, item, node.kind)
                            )
            else:
                kind = _kind_of(child)
                if kind in wanted:
                    found.append(ConstantRef(here, kind, child, node.kind))

    visit(ast, ())
    return found


def decompile_bytes(data: bytes) -> AstNode:
    """Decompile an in-memory ``.nitrobin`` byte stream to an AST."""
    return decompile_nitrobin(data)


def find_constants_in_bytes(
    data: bytes, kinds: Iterable[str] | None = None
) -> list[ConstantRef]:
    """:func:`find_constants` over raw ``.nitrobin`` bytes."""
    return find_constants(decompile_bytes(data), kinds=kinds)


# --------------------------------------------------------------------------
# mutation
# --------------------------------------------------------------------------


def get_constant(ast: AstNode, path: Sequence[int]) -> float | int:
    """Read the literal at ``path``. Raises if it isn't a numeric literal."""
    container, idx = _resolve(ast, path)
    value = container[idx]
    if _kind_of(value) is None:
        raise NitroEditError(
            f"{format_path(path)} is not a numeric literal "
            f"(found {type(value).__name__})"
        )
    return value


def set_constant(ast: AstNode, path: Sequence[int], value: float | int) -> float | int:
    """Set the literal at ``path`` **in place**; returns the previous value.

    The literal's kind is preserved: an ``int`` slot only accepts an ``int``
    (booleans rejected, they are separate wire frames), a ``float`` slot
    accepts an ``int`` or ``float`` and stores a ``float``. This is what keeps
    the serialized length identical.
    """
    container, idx = _resolve(ast, path)
    previous = container[idx]
    kind = _kind_of(previous)
    if kind is None:
        raise NitroEditError(
            f"{format_path(path)} is not a numeric literal "
            f"(found {type(previous).__name__})"
        )
    if isinstance(value, bool):
        raise NitroEditError(
            f"{format_path(path)}: bool is not a numeric literal kind "
            "(TRUE/FALSE are distinct wire frames)"
        )
    if kind == INT:
        if not isinstance(value, int):
            raise NitroEditError(
                f"{format_path(path)} holds an int literal; refusing to write "
                f"{type(value).__name__} (that would change the wire frame and "
                "the serialized size)"
            )
        container[idx] = int(value)
    else:
        if not isinstance(value, (int, float)):
            raise NitroEditError(
                f"{format_path(path)} holds a float literal; refusing to write "
                f"{type(value).__name__}"
            )
        container[idx] = float(value)
    return previous


def assert_same_size(before: bytes, after: bytes) -> None:
    """Raise unless two serializations are the same length."""
    if len(before) != len(after):
        raise NitroEditError(
            f"serialized size changed {len(before)} -> {len(after)} B; a numeric "
            "literal edit must be same-size (i64/f64 are fixed width)"
        )


def mutate_constant_bytes(
    data: bytes,
    path: Sequence[int],
    value: float | int,
    *,
    require_same_size: bool = True,
) -> bytes:
    """Decompile, set one literal, re-serialize. Returns the new bytes.

    With ``require_same_size`` (default) the result is asserted to be exactly
    as long as the input, so the mutated module drops into a nitro-image entry
    without changing the encrypted payload size.
    """
    ast = decompile_bytes(data)
    set_constant(ast, path, value)
    out = serialize_nitrobin(ast)
    if require_same_size:
        assert_same_size(data, out)
    return out
