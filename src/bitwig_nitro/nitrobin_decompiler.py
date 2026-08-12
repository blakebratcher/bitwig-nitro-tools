"""Faithful `.nitrobin` decompiler: full AST recovery including function bodies.

Phase D of the Nitro Decompiler v1 plan. Distinct from:
  - `nitrobin_parser.py`, legacy structural parser (ports/fields/function names only)
  - `nitro_decompiler.py`, legacy stub generator (empty bodies)

This module walks the full binary AST per the packaged grammar tables:
  - nitro_ast_tags.json (tag-byte → AST class)
  - nitro_ast_class_methods.json (per-class deserialize specs)

Driven by spec data (no hand-written per-class deserializers).

Usage:
    from bitwig_nitro.nitrobin_decompiler import decompile_nitrobin_file

    ast = decompile_nitrobin_file("SallenKey.dec")
    print(ast.kind)        # "NitroFile"
    print(len(ast.children))   # number of declarations
"""
from __future__ import annotations

import io
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from .paths import packaged_data_dir

# ---------- Spec data (loaded once) ----------


def _load_specs() -> tuple[dict[int, str], dict[str, list[dict]], dict[str, bool], set[str]]:
    data_dir = packaged_data_dir()
    tags = json.loads((data_dir / "nitro_ast_tags.json").read_text())
    methods = json.loads((data_dir / "nitro_ast_class_methods.json").read_text())
    tag_to_class: dict[int, str] = {
        e["tag_int"]: e["ast_class"] for e in tags["registered"].values()
    }
    class_to_seq: dict[str, list[dict]] = {}
    class_singleton: dict[str, bool] = {}
    # Hardcoded super-classes for abstract intermediate AST classes that
    # are not registered in xSQ (so don't appear in methods.classes) but
    # show up in super-class chains. Derived from `javap -p` on each:
    super_of: dict[str, str] = {
        "BinaryExpression": "Expression",
        "UnaryExpression": "Expression",
        "ComparisonExpression": "BinaryExpression",
        "ReduceExpression": "Expression",
        "ValueExpression": "Expression",
        "NumberValueExpression": "ValueExpression",
        "IncrementExpression": "UnaryExpression",
        "TemplateParameterDeclaration": "Declaration",   # ← key intermediate
        "Expression": "Statement",
        "Statement": "BxQ",
        "Declaration": "BxQ",
        "Type": "BxQ",
        "NumberType": "Type",
        "IntegerType": "NumberType",
        "SequentialType": "Type",
        "Specifier": "BxQ",
        "Annotation": "BxQ",
    }
    for cls, info in methods["classes"].items():
        class_to_seq[cls] = info.get("read_sequence", [])
        class_singleton[cls] = info.get("deserialize_via") == "singleton_getInstance"
        sc = info.get("super_class", "") or ""
        if sc.startswith("com.bitwig.nitro."):
            super_of[cls] = sc.rsplit(".", 1)[-1]

    # Transitive closure: any AST class whose super-chain contains
    # `Declaration` gets an extra trailing NodeList in the wire format
    # (from AHs.WtU() bytecode: `if (node instanceof Declaration) attach fVP()`).
    declarations: set[str] = set()
    def _walk(name: str, seen: set[str]) -> bool:
        if name in seen:
            return False
        seen.add(name)
        sup = super_of.get(name)
        if sup is None:
            return False
        if sup == "Declaration":
            return True
        return _walk(sup, seen)
    for cls in class_to_seq:
        if _walk(cls, set()):
            declarations.add(cls)
    return tag_to_class, class_to_seq, class_singleton, declarations


_TAG_TO_CLASS, _CLASS_TO_SEQ, _CLASS_SINGLETON, _IS_DECLARATION = _load_specs()

# ---------- TrV frame markers ----------

NODE = 0x01
NODE_END = 0x02
NULL = 0x03
NODE_LIST = 0x04
NODE_LIST_END = 0x05
STRING_DEFINITION = 0x06
STRING_REFERENCE = 0x07
STRING_ARRAY = 0x08
TRUE = 0x09
FALSE = 0x0A
INT64 = 0x0B
DOUBLE = 0x0C


# ---------- AST node ----------

@dataclass
class AstNode:
    """One AST node with positional children.

    Field names are not preserved (would require tracing the constructor's
    putfield/super.<init> arg names). For analysis the order is sufficient.
    """
    kind: str
    tag: int
    children: list[Any] = field(default_factory=list)
    is_singleton: bool = False

    def __repr__(self) -> str:  # pragma: no cover, debug only
        if not self.children:
            return f"{self.kind}{'(singleton)' if self.is_singleton else '()'}"
        return f"{self.kind}({len(self.children)} children)"


class NitroParseError(Exception):
    """Raised when the .nitrobin stream is malformed or unrecognized."""


# ---------- Reader ----------

class _Reader:
    """Stateful reader over a `.nitrobin` byte stream.

    Models the Java `AHs` reader: holds the input stream, the per-file
    string interning table, and the `include_source_loc` flag.
    """

    def __init__(self, stream: BinaryIO) -> None:
        self._s = stream
        self._strings: list[str] = []
        flag = self._read_byte()
        if flag == TRUE:
            self._include_source_loc = True
        elif flag == FALSE:
            self._include_source_loc = False
        else:
            raise NitroParseError(
                f"Expected leading bool flag (0x09/0x0A), got 0x{flag:02X}"
            )

    def _read_byte(self) -> int:
        b = self._s.read(1)
        if not b:
            raise NitroParseError("Unexpected EOF")
        return b[0]

    def _peek_byte(self) -> int | None:
        b = self._s.read(1)
        if not b:
            return None
        self._s.seek(-1, io.SEEK_CUR)
        return b[0]

    def _read_exact(self, n: int) -> bytes:
        b = self._s.read(n)
        if len(b) != n:
            raise NitroParseError(f"Expected {n} bytes, got {len(b)}")
        return b

    def _read_u16_be(self) -> int:
        return struct.unpack(">H", self._read_exact(2))[0]

    def _read_u32_be(self) -> int:
        return struct.unpack(">I", self._read_exact(4))[0]

    def _read_i64_be(self) -> int:
        return struct.unpack(">q", self._read_exact(8))[0]

    def _read_f64_be(self) -> float:
        return struct.unpack(">d", self._read_exact(8))[0]

    def _read_inline_string(self) -> str:
        header = self._read_u32_be()
        length = header & 0x7FFFFFFF
        if length > 67_108_864:
            raise NitroParseError(f"String length {length} exceeds 64MB")
        return self._read_exact(length).decode("utf-8")

    def read_bool(self) -> bool:
        marker = self._read_byte()
        if marker == TRUE:
            return True
        if marker == FALSE:
            return False
        raise NitroParseError(f"Expected bool marker, got 0x{marker:02X}")

    def read_long(self) -> int:
        marker = self._read_byte()
        if marker != INT64:
            raise NitroParseError(f"Expected Int64 marker (0x0B), got 0x{marker:02X}")
        return self._read_i64_be()

    def read_double(self) -> float:
        marker = self._read_byte()
        if marker != DOUBLE:
            raise NitroParseError(f"Expected Double marker (0x0C), got 0x{marker:02X}")
        return self._read_f64_be()

    def read_string(self) -> str:
        """Non-nullable string (AHs.QOE)."""
        return self._read_string_with_marker(self._read_byte())

    def read_string_optional(self) -> str | None:
        """Nullable string (AHs.KJT), returns None on 0x03 marker."""
        marker = self._read_byte()
        if marker == NULL:
            return None
        return self._read_string_with_marker(marker)

    def _read_string_with_marker(self, marker: int) -> str:
        if marker == STRING_DEFINITION:
            s = self._read_inline_string()
            self._strings.append(s)
            return s
        if marker == STRING_REFERENCE:
            idx = self._read_u32_be()
            if idx >= len(self._strings):
                raise NitroParseError(
                    f"StringReference idx {idx} out of bounds (interned: {len(self._strings)})"
                )
            return self._strings[idx]
        raise NitroParseError(
            f"Expected String marker (0x06/0x07), got 0x{marker:02X}"
        )

    def read_string_array(self) -> list[str]:
        marker = self._read_byte()
        if marker != STRING_ARRAY:
            raise NitroParseError(
                f"Expected StringArray marker (0x08), got 0x{marker:02X}"
            )
        count = self._read_u32_be()
        return [self.read_string() for _ in range(count)]

    def read_node_list(self) -> list[AstNode]:
        """Read a List<BxQ>: [0x04][u32 count_hint][(0x01 [tag] body 0x02)*][0x05].

        Per AHs.fVP() bytecode, the u32 count is a preallocation hint only;
        loop termination is the 0x05 NodeListEnd marker, not the count.
        """
        marker = self._read_byte()
        if marker != NODE_LIST:
            raise NitroParseError(
                f"Expected NodeList marker (0x04), got 0x{marker:02X}"
            )
        _count_hint = self._read_u32_be()
        out: list[AstNode] = []
        while True:
            sep = self._read_byte()
            if sep == NODE_LIST_END:
                return out
            if sep != NODE:
                raise NitroParseError(
                    f"Expected NodeList element (0x01) or end (0x05), got 0x{sep:02X}"
                )
            out.append(self._read_node_body())

    def read_node(self) -> AstNode:
        marker = self._read_byte()
        if marker != NODE:
            raise NitroParseError(
                f"Expected Node marker (0x01), got 0x{marker:02X}"
            )
        return self._read_node_body()

    def read_node_optional(self) -> AstNode | None:
        marker = self._read_byte()
        if marker == NULL:
            return None
        if marker == NODE:
            return self._read_node_body()
        raise NitroParseError(
            f"Expected Node or Null (0x01/0x03), got 0x{marker:02X}"
        )

    def _read_node_body(self) -> AstNode:
        """Tag dispatch + per-class deserialize. Assumes 0x01 already consumed."""
        tag = self._read_u16_be()
        cls = _TAG_TO_CLASS.get(tag)
        if cls is None:
            raise NitroParseError(f"Unknown AST tag 0x{tag:04X}")
        seq = _CLASS_TO_SEQ.get(cls, [])
        is_singleton = _CLASS_SINGLETON.get(cls, False)
        children: list[Any] = []
        for step in seq:
            op = step.get("op")
            if op == "read_node":
                children.append(self.read_node())
            elif op == "read_node_inner":
                children.append(self.read_node_optional())
            elif op == "read_long":
                children.append(self.read_long())
            elif op == "read_double":
                children.append(self.read_double())
            elif op == "read_bool":
                children.append(self.read_bool())
            elif op == "read_string":
                children.append(self.read_string())
            elif op == "read_string_v2":
                # AHs.KJT, nullable string
                children.append(self.read_string_optional())
            elif op == "read_string_array":
                children.append(self.read_string_array())
            elif op == "read_list":
                # AHs.fVP, List<BxQ>
                children.append(self.read_node_list())
            elif op == "read_list_v2":
                # AHs.zc2, actually List<String> (Arrays.asList(it()))
                children.append(self.read_string_array())
            elif op in ("super_init", "store_field"):
                continue
            else:
                raise NitroParseError(f"Unimplemented op {op!r} in {cls} read_sequence")
        # Per AHs.WtU() bytecode, the order after the body is:
        #   1. 0x02 NodeEnd
        #   2. (if include_source_loc) source-location triple
        #   3. (if Declaration subclass) extra NodeList of nested decls
        end = self._read_byte()
        if end != NODE_END:
            raise NitroParseError(
                f"Expected NodeEnd (0x02) after {cls}, got 0x{end:02X}"
            )
        if self._include_source_loc:
            _ = self.read_string()
            _ = self._read_u16_be()
            _ = self._read_u16_be()
        decl_suffix: list[AstNode] | None = None
        if cls in _IS_DECLARATION:
            decl_suffix = self.read_node_list()
        node = AstNode(kind=cls, tag=tag, children=children, is_singleton=is_singleton)
        if decl_suffix is not None:
            # Stored as an attribute rather than mixed into children to keep
            # children's positional semantics from Phase B intact.
            node.children.append({"_decl_suffix": decl_suffix})
        return node


# ---------- Public API ----------

def decompile_nitrobin(data: bytes) -> AstNode:
    """Parse a `.nitrobin` byte buffer into a full AST tree."""
    return _Reader(io.BytesIO(data)).read_node()


def decompile_nitrobin_file(path: str | Path) -> AstNode:
    """Parse a decrypted `.nitrobin` (`.dec`) file from disk."""
    return decompile_nitrobin(Path(path).read_bytes())
