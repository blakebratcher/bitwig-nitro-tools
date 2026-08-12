"""``nitro-extract-keys``: persist the cipher keys you already obtained.

This package ships **no** cipher keys. Its primary job here is to write a
``keys.json`` (validated) at the resolved location so the other tools can find
your keys::

    nitro-extract-keys --dag-key <hex> --image-key <hex>
    nitro-extract-keys --dag-key <hex> --image-key <hex> --out ./keys.json

``--from-jar`` is a **best-effort** convenience that only reports whether a
``bitwig.jar`` is present. It does **not** extract keys; automated extraction
is not supported. See ``docs/KEY_EXTRACTION.md`` for the manual procedure.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bitwig_nitro import MissingKeyError, write_keys_file

_DOCS = "docs/KEY_EXTRACTION.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nitro-extract-keys",
        description="Persist cipher keys you already obtained into a keys.json "
        "so the other tools can resolve them.",
    )
    parser.add_argument("--dag-key", help="Dag cipher key as a hex string")
    parser.add_argument("--image-key", help="nitro-image key as a hex string")
    parser.add_argument(
        "--out",
        help="destination keys.json (default: keys.json in your per-user "
        "config dir: $BITWIG_NITRO_CONFIG when set, else the APPDATA folder "
        "on Windows, otherwise $XDG_CONFIG_HOME/bitwig-nitro or "
        "~/.config/bitwig-nitro)",
    )
    parser.add_argument(
        "--from-jar",
        metavar="PATH",
        help="best-effort: check for a bitwig.jar (does NOT extract keys; "
        f"see {_DOCS} for the manual procedure)",
    )
    args = parser.parse_args(argv)

    if args.from_jar:
        jar = Path(args.from_jar)
        found = jar.is_file()
        print(
            "--from-jar is a BEST-EFFORT helper and does NOT extract keys.",
            file=sys.stderr,
        )
        print(
            f"  bitwig.jar {'found' if found else 'NOT found'} at {jar}",
            file=sys.stderr,
        )
        print(
            "Automated key extraction is not supported. Obtain the keys "
            f"yourself, then pass --dag-key/--image-key. See {_DOCS}.",
            file=sys.stderr,
        )
        return 2

    if not args.dag_key or not args.image_key:
        print(
            "error: both --dag-key and --image-key are required to write "
            "keys.json.",
            file=sys.stderr,
        )
        print(
            f"Obtain the keys from your own Bitwig install (see {_DOCS}); "
            "use --from-jar for the best-effort notice.",
            file=sys.stderr,
        )
        return 2

    try:
        dest = write_keys_file(args.dag_key, args.image_key, args.out)
    except MissingKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"wrote keys.json to {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
