"""``nitro-decrypt-std``: decrypt your own ``Library/nitro-std`` via Bitwig.

Unlike ``nitro-image`` (a self-inverse Dag/XOR cipher that decrypts fully
offline — see ``nitro-decrypt-corpus``), each ``nitro-std`` stdlib *source*
member is wrapped in a runtime PRNG stream cipher that cannot be reproduced
offline. Decryption therefore runs inside a live Bitwig JVM, via the bundled
``BitwigNitroStdDump`` controller, which decrypts the whole archive to a tree
on your disk and writes a manifest.

Flow (mirrors ``nitro-extract-keys --live``)::

    nitro-decrypt-std --install-controller   # copy the controller in
    # add "bitwig-nitro-tools" / "Nitro Std Dump" in Bitwig; it runs once
    nitro-decrypt-std                         # verify + report the result

It ships NO keys and NO decrypted content — it reads YOUR own licensed install
and writes to YOUR own disk.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from bitwig_nitro.paths import (
    bitwig_controller_scripts_dir,
    nitro_std_install_path,
    nitro_std_manifest_path,
    nitro_std_output_dir,
)

CONTROLLER_FILENAME = "BitwigNitroStdDump.control.js"
"""The bundled controller script copied by ``--install-controller``."""


def bundled_controller_path() -> Path:
    """Locate the bundled controller, shipped as package data."""
    return Path(__file__).resolve().parent.parent / "data" / CONTROLLER_FILENAME


def _print_next_steps(stream=sys.stderr) -> None:
    print(
        "Next steps:\n"
        "  1. In Bitwig: Settings -> Controllers -> Add controller, choose\n"
        '     "bitwig-nitro-tools" / "Nitro Std Dump".\n'
        "  2. It runs once, shows a popup, and writes the decrypted tree to\n"
        f"     {nitro_std_output_dir()}\n"
        f"     (manifest: {nitro_std_manifest_path()})\n"
        "  3. Then run:  nitro-decrypt-std   (to verify + report)\n"
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
    std = nitro_std_install_path()
    if std is not None:
        print(f"Detected nitro-std: {std}")
    else:
        print(
            "note: could not auto-locate nitro-std; set $BITWIG_NITRO_STD to its "
            "full path before launching Bitwig if the controller reports it missing."
        )
    _print_next_steps(sys.stdout)
    return 0


def _report(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest) if args.manifest else nitro_std_manifest_path()
    if not manifest_path.is_file():
        print(
            f"error: no manifest at {manifest_path}. The controller has not run "
            "yet.",
            file=sys.stderr,
        )
        _print_next_steps()
        return 1

    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read manifest {manifest_path}: {exc}", file=sys.stderr)
        return 1

    status = manifest.get("status")
    files = manifest.get("files") or []
    out_dir = Path(manifest.get("out_dir") or nitro_std_output_dir())

    if status not in ("ok", "partial"):
        print(
            f"nitro-std dump did not succeed (status={status!r}): "
            f"{manifest.get('message', '')}",
            file=sys.stderr,
        )
        return 1

    # Verify the decrypted tree actually exists on disk.
    present = sum(1 for f in files if (out_dir / f["path"]).is_file())
    total_bytes = sum(f.get("size", 0) for f in files)
    print(f"nitro-std source: {status}")
    print(f"  archive:  {manifest.get('nitro_std')}")
    print(f"  out dir:  {out_dir}")
    print(f"  files:    {present}/{len(files)} present on disk ({total_bytes:,} bytes)")
    if status == "partial":
        print(f"  note:     {manifest.get('message', '')}", file=sys.stderr)
    if present != len(files):
        print(
            f"  warning:  {len(files) - present} manifest entries missing from "
            f"{out_dir} — re-run the controller.",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nitro-decrypt-std",
        description="Decrypt your own Library/nitro-std stdlib source via the "
        "bundled Bitwig controller (nitro-std needs a live JVM; it is not "
        "offline-decryptable like nitro-image).",
    )
    parser.add_argument(
        "--install-controller",
        action="store_true",
        help="copy the bundled Nitro Std Dump controller into Bitwig's "
        "Controller Scripts directory, then print next steps",
    )
    parser.add_argument(
        "--controllers-dir",
        default=None,
        help="override Bitwig's Controller Scripts directory for "
        "--install-controller (default: the per-OS user location)",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="path to the controller's manifest JSON "
        "(default: $BITWIG_NITRO_STD_DUMP or ~/.bitwig-nitro/nitro-std-dump.json)",
    )
    args = parser.parse_args(argv)

    if args.install_controller:
        return _install_controller(args)
    return _report(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
