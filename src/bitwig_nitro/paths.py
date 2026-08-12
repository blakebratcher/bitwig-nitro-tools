"""Filesystem path resolution for bitwig-nitro-tools.

Two kinds of paths live here:

  * **Packaged data**: the AST grammar tables shipped inside the wheel
    (``nitro_ast_tags.json`` / ``nitro_ast_class_methods.json``), resolved via
    :mod:`importlib.resources` so they work from an installed package, a zip,
    or a source checkout.
  * **Bitwig install locations**: the standard per-OS ``Library`` roots where
    a Bitwig Studio installation keeps its ``nitro-image`` DSP archive. Nothing
    here reads or writes those locations; they are only *candidate* paths a
    caller can probe.

All Bitwig-install lookups are best-effort: the roots are returned whether or
not they exist so callers can construct and report them.
"""
from __future__ import annotations

import os
from importlib import resources
from pathlib import Path

__all__ = [
    "packaged_data_dir",
    "bitwig_install_roots",
    "nitro_image_install_path",
    "user_config_dir",
    "keys_search_paths",
    "local_output_dir",
]

NITRO_IMAGE_FILENAME = "nitro-image"
"""Bitwig's DSP archive filename, found under ``<root>/nitro-image``."""

_ON_WINDOWS = os.name == "nt"


def packaged_data_dir() -> Path:
    """Return the directory of packaged data files shipped with the package.

    Resolves ``bitwig_nitro/data`` via :mod:`importlib.resources`, so it works
    whether the package is installed as a wheel, run from a source checkout, or
    imported from a zip.
    """
    return Path(resources.files("bitwig_nitro") / "data")


def _windows_install_roots() -> list[Path]:
    """Candidate Bitwig ``Library`` roots on Windows, from the environment.

    Covers a machine-wide install under Program Files on whatever drive Windows
    actually uses (``%ProgramW6432%`` / ``%PROGRAMFILES%`` /
    ``%PROGRAMFILES(X86)%``) and a per-user install under
    ``%LOCALAPPDATA%\\Programs``, then falls back to the conventional
    ``C:\\Program Files`` path so the list is never empty off-Windows.
    """
    subdir = Path("Bitwig Studio") / "Library"
    roots: list[Path] = []
    for var in ("ProgramW6432", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(var, "")
        if base:
            roots.append(Path(base) / subdir)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        roots.append(Path(local) / "Programs" / subdir)
    roots.append(Path("C:/Program Files") / subdir)
    return roots


def bitwig_install_roots() -> list[Path]:
    """Return candidate Bitwig ``Library`` roots across the supported platforms.

    A ``Library`` directory is the parent of the ``nitro-image`` archive. The
    candidates cover the common install layouts:

      * **Linux**: ``/opt/bitwig-studio/Library`` (deb/rpm/AUR) and the
        Flatpak data path, both system-wide (``/var/lib/flatpak``) and per-user
        (``~/.local/share/flatpak``).
      * **macOS**: ``Bitwig Studio.app/Contents/Resources/Library`` under both
        ``/Applications`` and ``~/Applications``.
      * **Windows**: ``Bitwig Studio\\Library`` under Program Files (on the
        real system drive) and under ``%LOCALAPPDATA%\\Programs`` for a per-user
        install.

    ``$BITWIG_NITRO_LIBRARY`` (a Library directory) is prepended when set. Every
    candidate is returned whether or not it exists, de-duplicated with order
    preserved; callers probe for the first one that exists.
    """
    roots: list[Path] = []
    override = os.environ.get("BITWIG_NITRO_LIBRARY", "")
    if override:
        roots.append(Path(override))

    home = Path.home()
    flatpak_rel = "app/com.bitwig.BitwigStudio/current/active/files/Library"
    mac_rel = "Bitwig Studio.app/Contents/Resources/Library"
    roots += [
        # Linux (native package)
        Path("/opt/bitwig-studio/Library"),
        # Linux (Flatpak: system-wide, then per-user)
        Path("/var/lib/flatpak") / flatpak_rel,
        home / ".local/share/flatpak" / flatpak_rel,
        # macOS (system, then per-user Applications)
        Path("/Applications") / mac_rel,
        home / "Applications" / mac_rel,
    ]
    roots += _windows_install_roots()

    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        if root not in seen:
            seen.add(root)
            unique.append(root)
    return unique


def nitro_image_install_path() -> Path | None:
    """Return the first existing ``<root>/nitro-image``, or ``None``.

    ``$BITWIG_NITRO_IMAGE`` (a full path to the image file or directory) wins
    when set and existing.
    """
    override = os.environ.get("BITWIG_NITRO_IMAGE", "")
    if override:
        p = Path(override)
        return p if p.exists() else None
    for root in bitwig_install_roots():
        candidate = root / NITRO_IMAGE_FILENAME
        if candidate.exists():
            return candidate
    return None


def user_config_dir() -> Path:
    """Return the per-user config directory for bitwig-nitro.

    ``$BITWIG_NITRO_CONFIG`` wins when set. Otherwise the platform convention is
    used: ``%APPDATA%\\bitwig-nitro`` on Windows, then
    ``$XDG_CONFIG_HOME/bitwig-nitro`` where set, falling back to
    ``~/.config/bitwig-nitro`` everywhere else.
    """
    override = os.environ.get("BITWIG_NITRO_CONFIG", "")
    if override:
        return Path(override)
    if _ON_WINDOWS:
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "bitwig-nitro"
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg) / "bitwig-nitro"
    return Path.home() / ".config" / "bitwig-nitro"


def keys_search_paths() -> list[Path]:
    """Return the ordered ``keys.json`` search locations.

    In order: ``$BITWIG_NITRO_KEYS`` (if set), ``./keys.json`` in the current
    directory, ``<user config dir>/keys.json`` (see :func:`user_config_dir`),
    and finally ``~/.config/bitwig-nitro/keys.json`` as a portable fallback so a
    file written under one convention is still found under another.
    """
    paths: list[Path] = []
    override = os.environ.get("BITWIG_NITRO_KEYS", "")
    if override:
        paths.append(Path(override))
    paths.append(Path.cwd() / "keys.json")
    paths.append(user_config_dir() / "keys.json")
    legacy = Path.home() / ".config" / "bitwig-nitro" / "keys.json"
    if legacy not in paths:
        paths.append(legacy)
    return paths


def local_output_dir() -> Path:
    """Return the base directory for generated corpus/atlas output.

    Defaults to the current working directory; overridable via
    ``$BITWIG_NITRO_OUT``.
    """
    override = os.environ.get("BITWIG_NITRO_OUT", "")
    return Path(override) if override else Path.cwd()
