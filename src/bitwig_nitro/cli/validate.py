"""``nitro-validate``: validate the decompiler against a corpus of ``.dec`` files.

Walks a directory tree, decompiles every ``*.dec`` file, and reports:

  * **parsed**: files the decompiler read without raising;
  * **clean**: files whose AST re-serializes byte-identically to the source
    (a strong proxy for a clean end-of-stream: bytes that were never consumed
    cannot be reproduced);
  * **failures**: files that could not be decompiled at all.

Exit status is non-zero when any file fails to decompile.

Usage::

    nitro-validate            # validates ./corpus
    nitro-validate some/dir
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bitwig_nitro import (
    NitroParseError,
    NitroSerializeError,
    decompile_nitrobin,
    serialize_nitrobin,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nitro-validate",
        description="Decompile every .dec file under a directory and report "
        "parse success and round-trip (clean-EOF) counts.",
    )
    parser.add_argument(
        "dir",
        nargs="?",
        default="corpus",
        help="directory tree of .dec files (default: ./corpus)",
    )
    args = parser.parse_args(argv)

    root = Path(args.dir)
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    files = sorted(root.rglob("*.dec"))
    if not files:
        print(f"error: no .dec files found under {root}", file=sys.stderr)
        return 1

    print(f"validating {len(files)} .dec file(s) under {root} ...")

    parsed = 0
    clean = 0
    failures: list[tuple[str, str]] = []
    dirty: list[str] = []

    for f in files:
        rel = f.relative_to(root).as_posix()
        data = f.read_bytes()
        try:
            ast = decompile_nitrobin(data)
        except NitroParseError as exc:
            failures.append((rel, str(exc)))
            continue
        except Exception as exc:  # defensive: report, don't crash the sweep
            failures.append((rel, f"{type(exc).__name__}: {exc}"))
            continue
        parsed += 1
        try:
            reserialized = serialize_nitrobin(ast)
            is_clean = reserialized == data
        except (NitroSerializeError, Exception):
            is_clean = False
        if is_clean:
            clean += 1
        else:
            dirty.append(rel)

    print(f"  parsed     : {parsed}/{len(files)}")
    print(f"  clean-EOF  : {clean}/{len(files)}  (re-serializes byte-identically)")
    print(f"  failures   : {len(failures)}")

    if dirty:
        print(f"\n{len(dirty)} file(s) parsed but did NOT round-trip cleanly:")
        for rel in dirty[:20]:
            print(f"    {rel}")
        if len(dirty) > 20:
            print(f"    ... and {len(dirty) - 20} more")

    if failures:
        print(f"\n{len(failures)} file(s) FAILED to decompile:")
        for rel, err in failures[:20]:
            print(f"    {rel}: {err[:100]}")
        if len(failures) > 20:
            print(f"    ... and {len(failures) - 20} more")
        return 1

    print("\nall files decompiled successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
