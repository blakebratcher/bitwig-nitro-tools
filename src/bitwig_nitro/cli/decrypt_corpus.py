"""``nitro-decrypt-corpus``: decrypt your own ``Library/nitro-image`` to ``.dec``.

Reads the ``nitro-image`` DSP archive from your own Bitwig installation
(auto-located, or given with ``--image``), decrypts every member with the keys
you configured (see :mod:`bitwig_nitro.keys`), and writes each decrypted
``.nitrobin`` stream to a mirrored ``.dec`` file under the output directory
(``filter/SallenKey.nitrobin`` -> ``filter/SallenKey.dec``).

Usage::

    nitro-decrypt-corpus                       # installed image -> ./corpus
    nitro-decrypt-corpus --out ./corpus
    nitro-decrypt-corpus --image /path/to/nitro-image --out ./corpus

If no cipher key is configured this exits non-zero with a pointer to the key
extraction docs.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bitwig_nitro import (
    MissingKeyError,
    NitroImageError,
    bitwig_install_roots,
    nitro_image_install_path,
    read_image,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nitro-decrypt-corpus",
        description="Decrypt your own Library/nitro-image into a tree of .dec "
        "files using your configured keys.",
    )
    parser.add_argument(
        "--image",
        help="path to a nitro-image file (default: the installed image)",
    )
    parser.add_argument(
        "--out",
        default="corpus",
        help="output directory for .dec files (default: ./corpus)",
    )
    args = parser.parse_args(argv)

    if args.image:
        image_path: Path | None = Path(args.image)
    else:
        image_path = nitro_image_install_path()

    if image_path is None:
        searched = "\n".join(
            f"  - {root / 'nitro-image'}" for root in bitwig_install_roots()
        )
        print(
            "error: could not locate a nitro-image in any known install "
            "location.\nSearched:\n"
            f"{searched}\n"
            "Pass --image PATH, or set $BITWIG_NITRO_IMAGE (the image file) or "
            "$BITWIG_NITRO_LIBRARY (its parent Library directory).",
            file=sys.stderr,
        )
        return 2
    if not Path(image_path).is_file():
        print(
            f"error: nitro-image is not a file (or does not exist): {image_path}",
            file=sys.stderr,
        )
        return 2

    try:
        entries = read_image(image_path)
    except MissingKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except NitroImageError as exc:
        print(f"error: could not read nitro-image {image_path}: {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    count = 0
    for name, plaintext in entries.items():
        dest = out_dir / Path(name).with_suffix(".dec")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(plaintext)
        count += 1

    print(f"decrypted {count} entrie(s) from {image_path}")
    print(f"  -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
