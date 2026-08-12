"""Faithful `.nitrobin` serializer: the inverse of `nitrobin_decompiler`.

Walks an `AstNode` tree and emits TrV-framed bytes matching the original
`.nitrobin` layout produced by Bitwig's compiler. Round-trips
byte-identically over the full 513-file corpus.

This is the write-side foundation that unlocks:
  - authoring new Nitro DSP modules from Python (via `nitro_builder`)
  - source compiler that produces a `.nitrobin` from a `.nitro` source file
  - any future tooling that needs to emit a syntactically valid Nitro binary

The serializer mirrors the read protocol and uses the same per-class
deserialize specs (the packaged `nitro_ast_class_methods.json`) the decompiler
uses.

Usage:
    from bitwig_nitro.nitrobin_writer import serialize_nitrobin
    data = serialize_nitrobin(ast_root)
    Path("out.nitrobin").write_bytes(data)
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from .nitrobin_decompiler import (
    AstNode,
    NitroParseError,
    _CLASS_TO_SEQ,
    _CLASS_SINGLETON,
    _IS_DECLARATION,
    NODE,
    NODE_END,
    NULL,
    NODE_LIST,
    NODE_LIST_END,
    STRING_DEFINITION,
    STRING_REFERENCE,
    STRING_ARRAY,
    TRUE,
    FALSE,
    INT64,
    DOUBLE,
)


class NitroSerializeError(Exception):
    """Raised when an AST node cannot be serialized."""


class _Writer:
    """Stateful writer that mirrors the `AHs` reader's wire format.

    String interning matches reader behaviour: each unique string gets one
    StringDefinition (0x06) on its first appearance and StringReference
    (0x07) on every subsequent appearance. Order is determined by the
    depth-first walk of the AST, which matches the read order exactly.
    """

    def __init__(self, include_source_loc: bool = False) -> None:
        self._buf = bytearray()
        self._strings: dict[str, int] = {}
        self._include_source_loc = include_source_loc

    # -- primitive emitters --

    def _u16(self, n: int) -> None:
        self._buf.extend(struct.pack(">H", n))

    def _u32(self, n: int) -> None:
        self._buf.extend(struct.pack(">I", n))

    def _i64(self, n: int) -> None:
        self._buf.extend(struct.pack(">q", n))

    def _f64(self, x: float) -> None:
        self._buf.extend(struct.pack(">d", x))

    # -- value emitters --

    def write_bool(self, v: bool) -> None:
        self._buf.append(TRUE if v else FALSE)

    def write_long(self, v: int) -> None:
        self._buf.append(INT64)
        self._i64(v)

    def write_double(self, v: float) -> None:
        self._buf.append(DOUBLE)
        self._f64(v)

    def write_string(self, s: str) -> None:
        """Non-nullable string with interning."""
        if s in self._strings:
            self._buf.append(STRING_REFERENCE)
            self._u32(self._strings[s])
            return
        idx = len(self._strings)
        self._strings[s] = idx
        self._buf.append(STRING_DEFINITION)
        enc = s.encode("utf-8")
        self._u32(len(enc))
        self._buf.extend(enc)

    def write_string_optional(self, s: str | None) -> None:
        if s is None:
            self._buf.append(NULL)
        else:
            self.write_string(s)

    def write_string_array(self, items: list[str]) -> None:
        self._buf.append(STRING_ARRAY)
        self._u32(len(items))
        for s in items:
            self.write_string(s)

    def write_node_list(self, nodes: list[AstNode]) -> None:
        self._buf.append(NODE_LIST)
        self._u32(len(nodes))
        for n in nodes:
            self.write_node(n)
        self._buf.append(NODE_LIST_END)

    def write_node(self, node: AstNode) -> None:
        # Separate the trailing _decl_suffix sentinel (appended by the
        # decompiler for Declaration subclasses) from the positional
        # children that map to the read_sequence ops.
        children: list[Any] = list(node.children)
        decl_suffix: list[AstNode] | None = None
        if children and isinstance(children[-1], dict) and "_decl_suffix" in children[-1]:
            decl_suffix = children[-1]["_decl_suffix"]
            children = children[:-1]

        self._buf.append(NODE)
        self._u16(node.tag)

        if node.is_singleton:
            if children:
                raise NitroSerializeError(
                    f"Singleton {node.kind} should have no children, got {len(children)}"
                )
        else:
            seq = _CLASS_TO_SEQ.get(node.kind, [])
            real_ops = [s for s in seq if s.get("op") not in ("super_init", "store_field")]
            if len(real_ops) != len(children):
                raise NitroSerializeError(
                    f"{node.kind}: read_sequence has {len(real_ops)} real ops "
                    f"but node has {len(children)} children"
                )
            for op_info, child in zip(real_ops, children):
                op = op_info["op"]
                if op == "read_node":
                    self.write_node(child)
                elif op == "read_node_inner":
                    if child is None:
                        self._buf.append(NULL)
                    else:
                        self.write_node(child)
                elif op == "read_long":
                    self.write_long(child)
                elif op == "read_double":
                    self.write_double(child)
                elif op == "read_bool":
                    self.write_bool(child)
                elif op == "read_string":
                    self.write_string(child)
                elif op == "read_string_v2":
                    self.write_string_optional(child)
                elif op == "read_string_array":
                    self.write_string_array(child)
                elif op == "read_list":
                    self.write_node_list(child)
                elif op == "read_list_v2":
                    # AHs.zc2, actually List<String>, mirrors read side.
                    self.write_string_array(child)
                else:
                    raise NitroSerializeError(
                        f"Unimplemented op {op!r} in {node.kind} read_sequence"
                    )

        self._buf.append(NODE_END)

        if self._include_source_loc:
            # The decompiler discards the source-location triple on read,
            # so we cannot faithfully reproduce it from an AST round-trip.
            # Bitwig's stdlib emits include_source_loc=False, so this path
            # is unused in practice. Raise to surface any mistaken use.
            raise NitroSerializeError(
                "include_source_loc=True requires source_loc on each node; "
                "current AstNode does not preserve it"
            )

        if node.kind in _IS_DECLARATION:
            self.write_node_list(decl_suffix or [])

    def write_root(self, root: AstNode) -> bytes:
        self._buf.append(TRUE if self._include_source_loc else FALSE)
        self.write_node(root)
        return bytes(self._buf)


# ---------- Public API ----------

def serialize_nitrobin(root: AstNode, include_source_loc: bool = False) -> bytes:
    """Serialize an `AstNode` tree to `.nitrobin` bytes."""
    return _Writer(include_source_loc=include_source_loc).write_root(root)


def serialize_nitrobin_file(root: AstNode, path: str | Path,
                             include_source_loc: bool = False) -> None:
    """Serialize and write to disk."""
    Path(path).write_bytes(serialize_nitrobin(root, include_source_loc))
