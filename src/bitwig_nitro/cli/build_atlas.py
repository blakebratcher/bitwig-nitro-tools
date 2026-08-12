"""``nitro-build-atlas``: build a per-module DSP atlas from a ``.dec`` corpus.

For every ``*.dec`` under the corpus directory this decompiles the module,
renders its Nitro pseudo-source (:func:`bitwig_nitro.pretty_print`), and does a
best-effort walk of the AST to pull out the module's structs, ports, state
fields and functions. One JSON record is written per corpus file, plus an
``_index.json`` summarising every record.

Usage::

    nitro-build-atlas                                  # ./corpus -> ./atlas
    nitro-build-atlas --corpus ./corpus --out ./atlas

The extraction is deliberately shallow; it reads only what the public
:class:`bitwig_nitro.AstNode` interface exposes (``kind`` + positional
``children``), so it stays robust across grammar revisions.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterator

from bitwig_nitro import (
    AstNode,
    NitroParseError,
    decompile_nitrobin_file,
    pretty_print,
)

_STRUCT_KINDS = ("ComponentDeclaration", "StructureDeclaration")


def _iter_nodes(node: object) -> Iterator[AstNode]:
    """Yield every :class:`AstNode` in a tree (depth-first)."""
    if isinstance(node, AstNode):
        yield node
        for child in node.children:
            yield from _iter_nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from _iter_nodes(child)


def _first_str(node: AstNode) -> str | None:
    """Return the first string child of ``node`` (usually its declared name)."""
    for child in node.children:
        if isinstance(child, str):
            return child
    return None


def _first_node_kind(node: AstNode) -> str | None:
    """Return the kind of the first AstNode child (usually a type node)."""
    for child in node.children:
        if isinstance(child, AstNode):
            return child.kind
    return None


def _collect(ast: AstNode, kinds: tuple[str, ...]) -> list[AstNode]:
    return [n for n in _iter_nodes(ast) if n.kind in kinds]


def build_record(dec_path: Path, rel: str) -> dict:
    """Build one atlas record for a single ``.dec`` file."""
    ast = decompile_nitrobin_file(dec_path)

    structs = [
        name
        for n in _collect(ast, _STRUCT_KINDS)
        if (name := _first_str(n)) is not None
    ]
    ports = [
        {"name": _first_str(n), "type": _first_node_kind(n)}
        for n in _collect(ast, ("PortDeclaration",))
    ]
    state_fields = [
        name
        for n in _collect(ast, ("VariableDeclaration",))
        if (name := _first_str(n)) is not None
    ]
    functions = [
        name
        for n in _collect(ast, ("FunctionDeclaration",))
        if (name := _first_str(n)) is not None
    ]

    return {
        "name": dec_path.stem,
        "path": rel,
        "structs": structs,
        "ports": ports,
        "state_fields": state_fields,
        "functions": functions,
        "source": pretty_print(ast),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nitro-build-atlas",
        description="Build a per-module DSP atlas (one JSON record per .dec "
        "file, plus an _index.json) from a decrypted corpus.",
    )
    parser.add_argument(
        "--corpus",
        default="corpus",
        help="input directory of .dec files (default: ./corpus)",
    )
    parser.add_argument(
        "--out",
        default="atlas",
        help="output directory for atlas records (default: ./atlas)",
    )
    args = parser.parse_args(argv)

    corpus = Path(args.corpus)
    if not corpus.is_dir():
        print(f"error: corpus directory not found: {corpus}", file=sys.stderr)
        return 2

    files = sorted(corpus.rglob("*.dec"))
    if not files:
        print(f"error: no .dec files found under {corpus}", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"building atlas from {len(files)} module(s) in {corpus} ...")

    index: dict[str, dict] = {}
    errors: list[dict] = []

    for f in files:
        rel = f.relative_to(corpus).as_posix()
        atlas_file = rel.replace("/", "__").removesuffix(".dec") + ".json"
        try:
            record = build_record(f, rel)
        except NitroParseError as exc:
            errors.append({"path": rel, "error": str(exc)})
            continue
        except Exception as exc:  # defensive: never abort the whole build
            errors.append({"path": rel, "error": f"{type(exc).__name__}: {exc}"})
            continue

        (out_dir / atlas_file).write_text(json.dumps(record, indent=2))
        index[rel] = {
            "name": record["name"],
            "atlas_file": atlas_file,
            "structs": record["structs"],
            "ports_count": len(record["ports"]),
            "state_fields_count": len(record["state_fields"]),
            "functions_count": len(record["functions"]),
        }

    summary = {
        "total_files": len(files),
        "records": len(index),
        "errors": len(errors),
    }
    (out_dir / "_index.json").write_text(
        json.dumps(
            {"_summary": summary, "_errors": errors, "modules": index}, indent=2
        )
    )

    print(f"  records: {len(index)}")
    print(f"  errors : {len(errors)}")
    print(f"  -> {out_dir}")
    if errors:
        for e in errors[:10]:
            print(f"    FAILED {e['path']}: {e['error'][:100]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
