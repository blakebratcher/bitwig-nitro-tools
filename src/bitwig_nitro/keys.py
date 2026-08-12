"""Runtime resolution of the Bitwig Dag / nitro-image cipher keys.

This package ships **no** cipher keys. To decrypt Bitwig ``0004`` files or the
``nitro-image`` DSP archive you must supply the keys yourself, extracted from
your own Bitwig installation (see ``docs/KEY_EXTRACTION.md``).

Resolution order for each key (:func:`resolve_dag_key`,
:func:`resolve_nitro_image_key`):

  1. a direct hex override in an environment variable
     (``BITWIG_NITRO_DAG_KEY`` / ``BITWIG_NITRO_IMAGE_KEY``);
  2. a ``keys.json`` file, located via :func:`bitwig_nitro.paths.keys_search_paths`:
     ``$BITWIG_NITRO_KEYS``, then ``./keys.json``, then the per-user config
     directory (``$BITWIG_NITRO_CONFIG`` when set, else ``%APPDATA%\\bitwig-nitro``
     on Windows, otherwise ``$XDG_CONFIG_HOME`` or ``~/.config/bitwig-nitro``).

``keys.json`` schema::

    {"dag_key": "<hex>", "nitro_image_key": "<hex>"}

When neither source provides the requested key, :class:`MissingKeyError` is
raised with a message naming the environment variable and pointing at the key
extraction docs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import paths

__all__ = [
    "MissingKeyError",
    "resolve_dag_key",
    "resolve_nitro_image_key",
    "write_keys_file",
]

_DAG_ENV = "BITWIG_NITRO_DAG_KEY"
_IMAGE_ENV = "BITWIG_NITRO_IMAGE_KEY"

_DAG_FIELD = "dag_key"
_IMAGE_FIELD = "nitro_image_key"

_DOCS = "docs/KEY_EXTRACTION.md"


class MissingKeyError(Exception):
    """Raised when a required cipher key cannot be resolved."""


def _decode_hex(value: str, source: str) -> bytes:
    try:
        return bytes.fromhex(value.strip())
    except ValueError as exc:
        raise MissingKeyError(f"invalid hex key from {source}: {exc}") from exc


def _load_keys_file() -> tuple[dict, Path] | None:
    """Return ``(parsed_json, path)`` for the first readable keys.json."""
    for candidate in paths.keys_search_paths():
        try:
            text = Path(candidate).read_text()
        except OSError:
            continue
        try:
            doc = json.loads(text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise MissingKeyError(f"malformed keys file {candidate}: {exc}") from exc
        if not isinstance(doc, dict):
            raise MissingKeyError(f"keys file {candidate} is not a JSON object")
        return doc, Path(candidate)
    return None


def _resolve(env_var: str, field: str, human: str) -> bytes:
    env_val = os.environ.get(env_var, "")
    if env_val:
        return _decode_hex(env_val, f"${env_var}")
    loaded = _load_keys_file()
    if loaded is not None:
        doc, path = loaded
        raw = doc.get(field)
        if raw:
            return _decode_hex(str(raw), f"{path} [{field!r}]")
    raise MissingKeyError(
        f"no {human} available: set ${env_var} to a hex key, or provide a "
        f"keys.json with a {field!r} entry (searched: "
        f"{', '.join(str(p) for p in paths.keys_search_paths())}). "
        f"See {_DOCS} for how to extract keys from your own Bitwig install."
    )


def resolve_dag_key() -> bytes:
    """Return the Dag cipher key used for Bitwig ``0004`` files.

    Raises :class:`MissingKeyError` when no key is configured.
    """
    return _resolve(_DAG_ENV, _DAG_FIELD, "Dag cipher key")


def resolve_nitro_image_key() -> bytes:
    """Return the key used for ``nitro-image`` entry payloads.

    Raises :class:`MissingKeyError` when no key is configured.
    """
    return _resolve(_IMAGE_ENV, _IMAGE_FIELD, "nitro-image key")


def write_keys_file(
    dag_key_hex: str,
    image_key_hex: str,
    path: str | Path | None = None,
) -> Path:
    """Write a ``keys.json`` with the given hex keys.

    Args:
        dag_key_hex: Dag cipher key as a hex string.
        image_key_hex: nitro-image key as a hex string.
        path: Destination file. Defaults to ``keys.json`` in the per-user config
            directory (see :func:`bitwig_nitro.paths.user_config_dir`).

    Returns:
        The path written.
    """
    # Validate before writing so a malformed hex string can't be persisted.
    _decode_hex(dag_key_hex, "dag_key_hex argument")
    _decode_hex(image_key_hex, "image_key_hex argument")

    if path is not None:
        dest = Path(path)
    else:
        dest = paths.user_config_dir() / "keys.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {_DAG_FIELD: dag_key_hex.strip(), _IMAGE_FIELD: image_key_hex.strip()},
            indent=2,
        )
        + "\n"
    )
    return dest
