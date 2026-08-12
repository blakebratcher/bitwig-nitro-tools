"""``nitro-decompile``: decompile a Nitro DSP module to readable pseudo-source.

The argument can be either a path to a decrypted ``.nitrobin`` / ``.dec`` file,
or a module name that is resolved for you. Resolution order:

1. an existing file at that exact path;
2. a matching file under the corpus directory (``./corpus`` by default, or
   ``--corpus DIR``), trying the name as-is and with ``.nitrobin`` / ``.dec``;
3. the member read straight from your installed ``nitro-image`` (needs a key).

By default the AST is emitted as readable Nitro pseudo-source
(:func:`bitwig_nitro.pretty_print`). With ``--ast`` the raw
:class:`bitwig_nitro.AstNode` tree is dumped instead.

Usage::

    nitro-decompile path/to/SallenKey.dec
    nitro-decompile filter/SallenKey          # from ./corpus or your install
    nitro-decompile filter/SallenKey --ast
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bitwig_nitro import (
    AstNode,
    MissingKeyError,
    NitroImageError,
    NitroParseError,
    decompile_nitrobin,
    decompile_nitrobin_file,
    pretty_print,
    read_entry,
)


def _dump_ast(node: object, depth: int, lines: list[str]) -> None:
    """Append an indented one-line-per-node rendering of ``node`` to ``lines``."""
    pad = "  " * depth
    if isinstance(node, AstNode):
        label = node.kind
        if node.is_singleton:
            label += " (singleton)"
        lines.append(f"{pad}{label}")
        for child in node.children:
            _dump_ast(child, depth + 1, lines)
    elif isinstance(node, list):
        lines.append(f"{pad}[list: {len(node)}]")
        for child in node:
            _dump_ast(child, depth + 1, lines)
    else:
        lines.append(f"{pad}{node!r}")


def _local_candidate(arg: str, corpus_dir: Path) -> Path | None:
    """Return a matching local file for ``arg``, or ``None``.

    Tries the name verbatim and, if it has no ``.nitrobin`` / ``.dec`` suffix,
    with each of those suffixes, under both the current directory and the
    corpus directory.
    """
    names = [arg]
    if not arg.endswith((".nitrobin", ".dec")):
        names += [arg + ".nitrobin", arg + ".dec"]
    for base in (Path.cwd(), corpus_dir):
        for name in names:
            cand = base / name
            if cand.is_file():
                return cand
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nitro-decompile",
        description="Decompile a Nitro module to pseudo-source (or dump its AST "
        "with --ast). Accepts a .nitrobin/.dec file path, or a module name "
        "resolved from --corpus or your installed nitro-image.",
    )
    parser.add_argument(
        "target",
        help="a .nitrobin/.dec file path, or a module name (e.g. filter/SallenKey)",
    )
    parser.add_argument(
        "--corpus",
        default="corpus",
        help="directory of decrypted modules to resolve names from (default: ./corpus)",
    )
    parser.add_argument(
        "--ast",
        action="store_true",
        help="dump the raw AstNode tree instead of pretty-printed source",
    )
    args = parser.parse_args(argv)

    corpus_dir = Path(args.corpus)

    # 1 & 2: a real file, either at the given path or resolved under the corpus.
    path = Path(args.target)
    local = path if path.is_file() else _local_candidate(args.target, corpus_dir)

    try:
        if local is not None:
            ast = decompile_nitrobin_file(local)
        else:
            # 3: read the member straight from the installed nitro-image.
            member = args.target
            if not member.endswith(".nitrobin"):
                member += ".nitrobin"
            data = read_entry(None, member)
            ast = decompile_nitrobin(data)
    except MissingKeyError as exc:
        print(
            f"error: {exc}\n"
            "Reading a module straight from your install needs the nitro-image "
            "key. See docs/KEY_EXTRACTION.md, or decrypt a corpus first with "
            "nitro-decrypt-corpus.",
            file=sys.stderr,
        )
        return 2
    except (NitroImageError, FileNotFoundError, KeyError) as exc:
        print(
            f"error: could not find '{args.target}' as a file, under "
            f"{corpus_dir}/, or in your installed nitro-image ({exc}).",
            file=sys.stderr,
        )
        return 2
    except NitroParseError as exc:
        print(f"error: could not decompile '{args.target}': {exc}", file=sys.stderr)
        return 1

    if args.ast:
        lines: list[str] = []
        _dump_ast(ast, 0, lines)
        sys.stdout.write("\n".join(lines) + "\n")
    else:
        text = pretty_print(ast)
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
