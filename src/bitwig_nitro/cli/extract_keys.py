"""``nitro-extract-keys``: recover / persist the nitro-image cipher key.

Static recovery of the key from ``bitwig.jar`` is **impossible** — on current
Bitwig the key is materialized purely at runtime and appears nowhere in the
shipped jars or binaries. The proven route is a bundled controller extension
you run against your OWN Bitwig install; it dumps the cipher chain to a JSON
file on your disk, and this tool reads that dump and writes a ``keys.json``.

Typical flow::

    nitro-extract-keys --install-controller     # copy the controller in
    # then: Bitwig -> Settings -> Controllers -> Add the "Nitro Key Dump"
    nitro-extract-keys --live                    # read the dump, write keys.json

You can still persist keys you obtained by other means::

    nitro-extract-keys --image-key <hex>
    nitro-extract-keys --dag-key <hex> --image-key <hex> --out ./keys.json

Exit codes: ``0`` success; ``2`` a user-actionable condition (no dump, no key
found, validation failed, bad arguments); ``1`` an unexpected error.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from bitwig_nitro import (
    MissingKeyError,
    nitro_image_install_path,
    write_keys_file,
)
from bitwig_nitro.keydump import (
    default_dump_path,
    parse_dump,
    select_image_key,
    validate_image_key,
)
from bitwig_nitro.paths import bitwig_controller_scripts_dir

_DOCS = "docs/KEY_EXTRACTION.md"

CONTROLLER_FILENAME = "BitwigNitroKeyDump.control.js"
"""The bundled controller script copied by ``--install-controller``."""


def bundled_controller_path() -> Path:
    """Locate the bundled controller, shipped as package data.

    Resolves to ``bitwig_nitro/data/<name>`` for wheel and editable/source
    installs alike (the package's data directory is on disk in either case).
    """
    return Path(__file__).resolve().parent.parent / "data" / CONTROLLER_FILENAME


def _print_live_next_steps(stream=sys.stderr) -> None:
    """Print the 'now add it in Bitwig, then run --live' instructions."""
    print(
        "Next steps:\n"
        "  1. In Bitwig: Settings -> Controllers -> Add controller, choose\n"
        '     "bitwig-nitro-tools" / "Nitro Key Dump".\n'
        "  2. It runs once, shows a popup, and writes the key dump to\n"
        f"     {default_dump_path()}\n"
        "  3. Then run:  nitro-extract-keys --live\n"
        "  4. You can remove the controller in Bitwig afterward.",
        file=stream,
    )


def _install_controller(args: argparse.Namespace) -> int:
    src = bundled_controller_path()
    if not src.is_file():
        print(
            f"error: bundled controller not found at {src}. Reinstall the "
            "package; the controller ships as package data.",
            file=sys.stderr,
        )
        return 1
    dest_dir = bitwig_controller_scripts_dir(args.controllers_dir)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
    except OSError as exc:
        print(f"error: could not install controller: {exc}", file=sys.stderr)
        return 1
    print(f"Installed controller to {dest}")
    _print_live_next_steps(sys.stdout)
    return 0


def _resolve_image_path(args: argparse.Namespace) -> tuple[Path | None, int | None]:
    """Return ``(image_path, exit_code)``.

    ``exit_code`` is non-None only for the hard error (an explicit ``--image``
    that does not exist). A ``None`` path means "no image to validate against".
    """
    if args.image:
        p = Path(args.image)
        if not p.exists():
            print(f"error: --image path does not exist: {p}", file=sys.stderr)
            return None, 2
        return p, None
    return nitro_image_install_path(), None


def _run_live(args: argparse.Namespace) -> int:
    dump_path = default_dump_path()
    if not dump_path.is_file():
        print(f"error: no key dump found at {dump_path}.", file=sys.stderr)
        print(
            "Install and run the controller first:\n"
            "  nitro-extract-keys --install-controller",
            file=sys.stderr,
        )
        _print_live_next_steps(sys.stderr)
        return 2

    try:
        data = json.loads(dump_path.read_text())
    except (OSError, ValueError) as exc:
        print(
            f"error: could not read key dump {dump_path}: {exc}. Re-run the "
            "controller to regenerate it.",
            file=sys.stderr,
        )
        return 2

    result = parse_dump(data)
    if not result.ok:
        print(
            f"error: the controller reported a failure: {result.message}",
            file=sys.stderr,
        )
        return 2

    key = select_image_key(result)
    if key is None:
        print(
            f"error: no nitro-image key found in {dump_path}.", file=sys.stderr
        )
        if result.candidates:
            print(
                "  the dump was ambiguous; multiple candidate byte arrays were "
                f"found ({len(result.candidates)}). This build may differ from "
                "the controller's assumptions; please file an issue.",
                file=sys.stderr,
            )
        return 2

    image_path, err = _resolve_image_path(args)
    if err is not None:
        return err

    validated: bool | None = None
    if image_path is not None:
        validated = validate_image_key(key, image_path)
        if not validated:
            if not args.force:
                print(
                    f"error: the recovered key failed to validate against "
                    f"{image_path}. The dump may be from a different Bitwig "
                    "version. Re-run the controller, or pass --force to write "
                    "it anyway.",
                    file=sys.stderr,
                )
                return 2
            print(
                "warning: writing an unvalidated key (--force).",
                file=sys.stderr,
            )

    try:
        dest = write_keys_file(image_key_hex=key.hex(), path=args.out)
    except MissingKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if validated is True:
        status = "validated against the installed nitro-image"
    elif validated is False:
        status = "NOT validated (written under --force)"
    else:
        status = "not validated (no nitro-image found to check against)"
    print(f"wrote nitro-image key to {dest} ({status}).")
    return 0


def _run_manual(args: argparse.Namespace) -> int:
    if not args.dag_key and not args.image_key:
        print(
            "error: nothing to do. Use --live (after --install-controller) to "
            "recover the key from a running Bitwig, or pass --dag-key/--image-"
            "key to persist keys you already have.",
            file=sys.stderr,
        )
        print(f"See {_DOCS} for the full procedure.", file=sys.stderr)
        return 2
    try:
        dest = write_keys_file(args.dag_key, args.image_key, args.out)
    except MissingKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote keys.json to {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nitro-extract-keys",
        description="Recover the nitro-image key from your own running Bitwig "
        "(via a bundled controller extension), or persist keys you already "
        "have, into a keys.json the other tools can resolve.",
    )
    parser.add_argument(
        "--install-controller",
        action="store_true",
        help="copy the bundled key-dump controller into Bitwig's Controller "
        "Scripts directory, then print how to enable it and run --live",
    )
    parser.add_argument(
        "--controllers-dir",
        help="override Bitwig's Controller Scripts directory for "
        "--install-controller (default: the per-OS user location)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="read the controller's key dump and write a keys.json with the "
        "recovered nitro-image key",
    )
    parser.add_argument(
        "--image",
        help="path to a nitro-image to validate the recovered key against "
        "(default: your installed image, if any)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --live, write the recovered key even if it fails validation",
    )
    parser.add_argument("--dag-key", help="Dag cipher key as a hex string (manual)")
    parser.add_argument(
        "--image-key", help="nitro-image key as a hex string (manual)"
    )
    parser.add_argument(
        "--out",
        help="destination keys.json (default: keys.json in your per-user "
        "config dir: $BITWIG_NITRO_CONFIG when set, else the APPDATA folder "
        "on Windows, otherwise $XDG_CONFIG_HOME/bitwig-nitro or "
        "~/.config/bitwig-nitro)",
    )
    args = parser.parse_args(argv)

    try:
        if args.install_controller:
            return _install_controller(args)
        if args.live:
            return _run_live(args)
        return _run_manual(args)
    except Exception as exc:  # pragma: no cover - unexpected internal failure
        print(f"unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
