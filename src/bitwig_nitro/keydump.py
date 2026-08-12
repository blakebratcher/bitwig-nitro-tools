"""Parse a runtime key-dump and select the nitro-image key from it.

Static recovery of the nitro-image key is impossible: on current Bitwig the
99-byte key is materialized purely at runtime and appears nowhere in the
shipped jars (raw / hex / base64) or native binaries. The only proven recovery
is reading it out of a *running* Bitwig JVM with a controller extension. The
bundled controller (``controller/BitwigNitroKeyDump.control.js``) dumps the
cipher chain's byte / small-int fields to a JSON file on your own disk; this
module reads that file and picks the key out of it.

Handshake (controller writes, this module reads):

  * Dump path: ``$BITWIG_NITRO_KEYDUMP`` else
    ``~/.bitwig-nitro/nitro-key-dump.json`` (see :func:`default_dump_path`).
  * Dump JSON: ``{"tool","version","status","message","nitro_image":{...}}``
    where ``nitro_image.transforms`` is a list of ``{"index","class",
    "byte_fields":{name:hex},"int_fields":{name:int}}``.

:func:`parse_dump` is tolerant of two shapes: the current shape-based dump
above, and an older named-field dump whose transforms carry the fields inline
(a byte array as ``{"length":N,"hex":...}`` or a bare hex string, and the IV
size as a bare int). Nothing here hardcodes an obfuscated field name; selection
is structural.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .nitro_image import NitroImage, decrypt_entry
from .nitrobin_decompiler import decompile_nitrobin

__all__ = [
    "DUMP_PATH_ENV",
    "MIN_KEY_LEN",
    "IV_SIZE_MARKER",
    "Transform",
    "KeyDumpResult",
    "default_dump_path",
    "DEFAULT_DUMP_PATH",
    "parse_dump",
    "select_image_key",
    "validate_image_key",
]

DUMP_PATH_ENV = "BITWIG_NITRO_KEYDUMP"
"""Environment override for the controller <-> CLI dump-file location."""

MIN_KEY_LEN = 16
"""Byte arrays shorter than this are never treated as a candidate key."""

IV_SIZE_MARKER = 198
"""The per-entry IV length; the transform whose int field equals it holds the
image key (matches :data:`bitwig_nitro.nitro_image.IV_SIZE`)."""

# Keys that describe a transform's identity rather than a dumped field value;
# ignored when normalizing an inline (old-shape) transform.
_STRUCTURAL_KEYS = frozenset({"index", "class", "full_class"})


def default_dump_path() -> Path:
    """Resolve the key-dump file path (mirrors the controller's logic).

    ``$BITWIG_NITRO_KEYDUMP`` wins when set; otherwise
    ``~/.bitwig-nitro/nitro-key-dump.json``.
    """
    override = os.environ.get(DUMP_PATH_ENV, "")
    if override:
        return Path(override)
    return Path.home() / ".bitwig-nitro" / "nitro-key-dump.json"


# Exposed under the spec's public name as well as the snake_case function.
DEFAULT_DUMP_PATH = default_dump_path


@dataclass
class Transform:
    """One entry from the cipher transform chain, normalized across shapes."""

    index: int | None = None
    cls: str | None = None
    byte_fields: dict[str, bytes] = field(default_factory=dict)
    int_fields: dict[str, int] = field(default_factory=dict)

    def has_iv_marker(self) -> bool:
        """True when any int field equals the IV-size marker (198)."""
        return any(v == IV_SIZE_MARKER for v in self.int_fields.values())


@dataclass
class KeyDumpResult:
    """A parsed key dump plus, after :func:`select_image_key`, its candidates."""

    tool: str | None = None
    version: int | None = None
    status: str | None = None
    message: str | None = None
    chain_class: str | None = None
    chain_length: int | None = None
    transforms: list[Transform] = field(default_factory=list)
    candidates: list[bytes] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True unless the controller explicitly reported ``status:"error"``."""
        return self.status != "error"


def _hex_to_bytes(text: str) -> bytes | None:
    """Decode a hex string to bytes, or ``None`` if it is not clean hex."""
    cleaned = text.strip().replace(" ", "").replace(":", "")
    if not cleaned:
        return None
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        return None


def _coerce_bytes(value: object) -> bytes | None:
    """Interpret a dumped field value as a byte array, or ``None``.

    Accepts a hex string, or a ``{"hex": ...}`` object (the old named-field
    ``{"length":N,"hex":...}`` shape).
    """
    if isinstance(value, dict):
        raw = value.get("hex")
        return _hex_to_bytes(raw) if isinstance(raw, str) else None
    if isinstance(value, str):
        return _hex_to_bytes(value)
    return None


def _parse_transform(entry: object) -> Transform | None:
    if not isinstance(entry, dict):
        return None
    index = entry.get("index")
    cls = entry.get("class") or entry.get("full_class")
    tf = Transform(
        index=index if isinstance(index, int) and not isinstance(index, bool) else None,
        cls=cls if isinstance(cls, str) else None,
    )

    if "byte_fields" in entry or "int_fields" in entry:
        # Current shape: fields already split into two named maps.
        for name, value in (entry.get("byte_fields") or {}).items():
            data = _coerce_bytes(value)
            if data is not None:
                tf.byte_fields[str(name)] = data
        for name, value in (entry.get("int_fields") or {}).items():
            if isinstance(value, int) and not isinstance(value, bool):
                tf.int_fields[str(name)] = value
    else:
        # Old shape: byte arrays and ints live inline on the transform.
        for name, value in entry.items():
            if name in _STRUCTURAL_KEYS:
                continue
            data = _coerce_bytes(value)
            if data is not None:
                tf.byte_fields[str(name)] = data
            elif isinstance(value, int) and not isinstance(value, bool):
                tf.int_fields[str(name)] = value
    return tf


def parse_dump(data: dict) -> KeyDumpResult:
    """Parse a key-dump JSON object into a :class:`KeyDumpResult`.

    Tolerant of both the current shape-based dump (``nitro_image.transforms``
    with ``byte_fields`` / ``int_fields``) and the older named-field dump (a
    top-level ``transforms`` list with fields inline). Never raises on a
    surprising shape; missing pieces simply yield empty fields.
    """
    if not isinstance(data, dict):
        return KeyDumpResult()

    result = KeyDumpResult(
        tool=data.get("tool") if isinstance(data.get("tool"), str) else None,
        version=data.get("version")
        if isinstance(data.get("version"), int) and not isinstance(data.get("version"), bool)
        else None,
        status=data.get("status") if isinstance(data.get("status"), str) else None,
        message=data.get("message") if isinstance(data.get("message"), str) else None,
    )

    image = data.get("nitro_image")
    if isinstance(image, dict):
        # Current shape.
        cc = image.get("chain_class")
        cl = image.get("chain_length")
        result.chain_class = cc if isinstance(cc, str) else None
        result.chain_length = (
            cl if isinstance(cl, int) and not isinstance(cl, bool) else None
        )
        raw_transforms = image.get("transforms")
    else:
        # Old shape: transforms and chain_length at top level.
        cl = data.get("chain_length")
        result.chain_length = (
            cl if isinstance(cl, int) and not isinstance(cl, bool) else None
        )
        raw_transforms = data.get("transforms")

    if isinstance(raw_transforms, list):
        for entry in raw_transforms:
            tf = _parse_transform(entry)
            if tf is not None:
                result.transforms.append(tf)
    return result


def _is_candidate(data: bytes) -> bool:
    """A byte array is a plausible key: long enough and not all-zero."""
    return len(data) >= MIN_KEY_LEN and any(data)


def _dedup(arrays: list[bytes]) -> list[bytes]:
    """De-duplicate byte arrays, preserving first-seen order."""
    seen: set[bytes] = set()
    unique: list[bytes] = []
    for arr in arrays:
        if arr not in seen:
            seen.add(arr)
            unique.append(arr)
    return unique


def _candidate_arrays(transform: Transform) -> list[bytes]:
    return [b for b in transform.byte_fields.values() if _is_candidate(b)]


def select_image_key(result: KeyDumpResult) -> bytes | None:
    """Pick the nitro-image key out of a parsed dump.

    Rules, in order:

      1. **IV marker** — if any transform carries an int field equal to 198,
         take the byte array(s) on those transforms. A single distinct value
         wins.
      2. **Shared value** — otherwise, if every candidate byte array across all
         transforms de-duplicates to exactly one value, take it.

    All-zero and ``< 16``-byte arrays are ignored, and identical hex values are
    de-duplicated. When more than one distinct candidate remains the result is
    ambiguous: this returns ``None`` and populates ``result.candidates`` for the
    caller to report. On success ``result.candidates`` is cleared.
    """
    result.candidates = []

    marked = _dedup(
        [b for tf in result.transforms if tf.has_iv_marker() for b in _candidate_arrays(tf)]
    )
    if len(marked) == 1:
        return marked[0]
    if len(marked) > 1:
        result.candidates = marked
        return None

    shared = _dedup([b for tf in result.transforms for b in _candidate_arrays(tf)])
    if len(shared) == 1:
        return shared[0]
    if len(shared) > 1:
        result.candidates = shared
    return None


def validate_image_key(key: bytes, image_path: str | os.PathLike | None) -> bool:
    """Confirm ``key`` decrypts the installed nitro-image.

    Decrypts ONE member of the image at ``image_path`` (or the resolved default
    when ``None``) with ``key`` and requires a clean decompile of the result.
    Returns ``False`` on any failure, including "no image present" — the caller
    treats an absent image as *unvalidated*, not as a hard failure.
    """
    try:
        path = Path(image_path) if image_path is not None else None
        img = NitroImage.load(path)
        names = img.names()
        if not names:
            return False
        raw = img.stored_payload(names[0])
        _version, _iv, plaintext = decrypt_entry(raw, key=key)
        decompile_nitrobin(plaintext)
        return True
    except Exception:
        return False
