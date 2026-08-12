"""Read / write Bitwig's ``Library/nitro-image`` DSP archive.

``<install>/Library/nitro-image`` is a **plain ZIP** (513 deflated members
named e.g. ``filter/SallenKey.nitrobin``). Each member's *stored payload* is
individually encrypted with the Dag cipher::

    [version:1 = 0x02][iv:198][ciphertext]
    plaintext = dag_decrypt(ciphertext, key=<nitro-image key>, iv=iv)

The nitro-image key (99-byte, ``iv_size`` = 198) is not shipped with this
package; it is resolved at call time via :func:`bitwig_nitro.keys.resolve_nitro_image_key`
(see ``docs/KEY_EXTRACTION.md``). The Dag cipher is a keystream XOR and
therefore self-inverse; encryption is the same call as decryption, so
``encrypt_entry(decrypt_entry(raw)) == raw`` exactly.

What this module gives you, entirely offline (no bridge, no running Bitwig):

    from bitwig_nitro.nitro_image import NitroImage, read_image, write_image

    img = NitroImage.load()                     # installed image
    pinch = img.plaintext("grid/level/grid_level_pinch.nitrobin")
    entries = img.to_dict()                     # name -> decrypted .nitrobin
    entries[name] = mutated_bytes
    write_image(entries, "/tmp/nitro-image", source=img)

Repack fidelity (measured, not assumed):

  * Untouched entries reuse their **original stored (deflated) bytes**, their
    original local header, data descriptor and central-directory record, so an
    identity repack is **byte-identical to the source file**. Recompressing
    instead would NOT be byte-identical: 17 of the 513 members do not reproduce
    under ``zlib`` level 6, so raw-copy is load-bearing.
  * Mutated entries are re-deflated (level 6) and re-encrypted **with the
    entry's original IV**, so only that entry's bytes change; every other
    entry's payload stays identical (its file offset may shift if the mutated
    member's compressed size changes, that is normal ZIP behaviour and the
    central directory is patched accordingly).

Nothing in this module has been verified against a *running* Bitwig: whether
the loader accepts a repacked image is an open question that only a live probe
settles.

Safety: :func:`write_image` refuses to write anywhere inside a Bitwig install
root unless the caller passes ``allow_install_overwrite=True`` *and* a
``backup_path`` outside the install, which is created and sha256-verified
first.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .dag_cipher import dag_decrypt
from .keys import resolve_nitro_image_key

__all__ = [
    "NITRO_IMAGE_FILENAME",
    "ENTRY_VERSION",
    "IV_SIZE",
    "NitroImageError",
    "NitroImage",
    "WriteReport",
    "backup_existing",
    "decrypt_entry",
    "encrypt_entry",
    "is_inside_install",
    "nitro_image_path",
    "nitro_key",
    "read_entry",
    "read_image",
    "write_image",
]


NITRO_IMAGE_FILENAME = "nitro-image"
"""Bitwig's DSP archive filename, under ``<install>/Library/``."""

ENTRY_VERSION = 0x02
"""Leading version byte on every encrypted entry payload."""

IV_SIZE = 198
"""The per-entry IV length in bytes."""


class NitroImageError(Exception):
    """Raised for malformed images, guard violations, and repack failures."""


# --------------------------------------------------------------------------
# path + key resolution
# --------------------------------------------------------------------------


def nitro_image_path(install_root: Path | str | None = None) -> Path:
    """Resolve ``<install>/Library/nitro-image``.

    Resolution order:

    1. ``$BITWIG_NITRO_IMAGE`` (full path to the image file or directory);
    2. ``install_root/Library/nitro-image`` when ``install_root`` is given;
    3. the first existing candidate from
       :func:`bitwig_nitro.paths.nitro_image_install_path`, else the first
       platform install root's ``nitro-image``.

    The path is returned whether or not it exists, so callers can construct
    and report it. Bitwig's loader accepts either a FILE (ZIP) or a DIRECTORY
    at this path; this module only handles the ZIP form.
    """
    override = os.environ.get("BITWIG_NITRO_IMAGE", "")
    if override:
        return Path(override)
    if install_root is not None:
        return Path(install_root) / "Library" / NITRO_IMAGE_FILENAME
    found = paths.nitro_image_install_path()
    if found is not None:
        return found
    roots = paths.bitwig_install_roots()
    if roots:
        return roots[0] / NITRO_IMAGE_FILENAME
    return Path(NITRO_IMAGE_FILENAME)


def nitro_key(path: Path | None = None) -> bytes:
    """Return the key used for nitro-image entry payloads.

    Resolved via :func:`bitwig_nitro.keys.resolve_nitro_image_key` (env var or
    ``keys.json``; see ``docs/KEY_EXTRACTION.md``). The ``path`` argument is
    accepted for backward compatibility and ignored.
    """
    return resolve_nitro_image_key()


# --------------------------------------------------------------------------
# entry cipher
# --------------------------------------------------------------------------


def decrypt_entry(raw: bytes, key: bytes | None = None) -> tuple[int, bytes, bytes]:
    """Split + decrypt one stored entry payload.

    Args:
        raw: The member's stored bytes: ``[version][iv:198][ciphertext]``.
        key: BIa key override (default: :func:`nitro_key`).

    Returns:
        ``(version, iv, plaintext)``: ``plaintext`` is the ``.nitrobin``
        byte stream that ``nitrobin_decompiler`` consumes.
    """
    if len(raw) < 1 + IV_SIZE:
        raise NitroImageError(
            f"entry payload too short: {len(raw)} B (need > {1 + IV_SIZE})"
        )
    version = raw[0]
    if version != ENTRY_VERSION:
        raise NitroImageError(
            f"unexpected entry version 0x{version:02X} (expected 0x{ENTRY_VERSION:02X})"
        )
    iv = raw[1 : 1 + IV_SIZE]
    ciphertext = raw[1 + IV_SIZE :]
    return version, iv, dag_decrypt(ciphertext, key=key or nitro_key(), iv=iv)


def encrypt_entry(
    plaintext: bytes,
    iv: bytes,
    version: int = ENTRY_VERSION,
    key: bytes | None = None,
) -> bytes:
    """Build a full stored entry payload from plaintext + IV.

    The Dag cipher is a keystream XOR, so this is literally the same
    transform as :func:`decrypt_entry`; re-encrypting an unmodified plaintext
    with its original IV reproduces the original bytes exactly.
    """
    if len(iv) != IV_SIZE:
        raise NitroImageError(f"iv must be {IV_SIZE} bytes (got {len(iv)})")
    ciphertext = dag_decrypt(plaintext, key=key or nitro_key(), iv=iv)
    return bytes([version]) + bytes(iv) + ciphertext


# --------------------------------------------------------------------------
# minimal ZIP container (raw-preserving)
# --------------------------------------------------------------------------

_LH_SIG = b"PK\x03\x04"
_CD_SIG = b"PK\x01\x02"
_DD_SIG = b"PK\x07\x08"
_EOCD_SIG = b"PK\x05\x06"

_FLAG_DATA_DESCRIPTOR = 0x0008
_FLAG_UTF8 = 0x0800


@dataclass
class _RawEntry:
    """One ZIP member, kept as verbatim byte regions so a repack can be exact."""

    name: str
    flag: int
    method: int
    crc: int
    compress_size: int
    file_size: int
    local_header: bytes  # 30 + namelen + extralen, verbatim from source
    compressed: bytes  # deflated (or stored) member data, verbatim
    descriptor: bytes  # b"" when flag bit 3 is clear
    central: bytes  # 46 + namelen + extralen + commentlen, verbatim

    def decompressed(self) -> bytes:
        if self.method == 0:
            return self.compressed
        if self.method == 8:
            return zlib.decompress(self.compressed, -15)
        raise NitroImageError(f"{self.name}: unsupported compression method {self.method}")


def _parse_zip(data: bytes) -> tuple[list[_RawEntry], bytes]:
    """Parse a ZIP into verbatim member regions + the EOCD record."""
    eocd_off = data.rfind(_EOCD_SIG)
    if eocd_off < 0:
        raise NitroImageError("not a ZIP archive (no end-of-central-directory record)")
    (_sig, _disk, _cd_disk, _n_disk, n_total, _cd_size, cd_off, comment_len) = struct.unpack(
        "<IHHHHIIH", data[eocd_off : eocd_off + 22]
    )
    if n_total == 0xFFFF or cd_off == 0xFFFFFFFF:
        raise NitroImageError("ZIP64 images are not supported")
    eocd = data[eocd_off : eocd_off + 22 + comment_len]

    entries: list[_RawEntry] = []
    pos = cd_off
    for _ in range(n_total):
        if data[pos : pos + 4] != _CD_SIG:
            raise NitroImageError(f"bad central-directory signature at {pos}")
        (
            _s, _cver, _xver, flag, method, _t, _d, crc, csize, usize,
            nlen, elen, clen, _dstart, _iattr, _eattr, loff,
        ) = struct.unpack("<IHHHHHHIIIHHHHHII", data[pos : pos + 46])
        rec_len = 46 + nlen + elen + clen
        central = data[pos : pos + rec_len]
        raw_name = data[pos + 46 : pos + 46 + nlen]
        name = raw_name.decode("utf-8" if flag & _FLAG_UTF8 else "cp437")
        pos += rec_len

        if data[loff : loff + 4] != _LH_SIG:
            raise NitroImageError(f"{name}: bad local-header signature at {loff}")
        l_nlen, l_elen = struct.unpack("<HH", data[loff + 26 : loff + 30])
        lh_len = 30 + l_nlen + l_elen
        local_header = data[loff : loff + lh_len]
        dstart = loff + lh_len
        compressed = data[dstart : dstart + csize]
        if len(compressed) != csize:
            raise NitroImageError(f"{name}: truncated member data")
        descriptor = b""
        if flag & _FLAG_DATA_DESCRIPTOR:
            p = dstart + csize
            descriptor = data[p : p + 16] if data[p : p + 4] == _DD_SIG else data[p : p + 12]

        entries.append(
            _RawEntry(
                name=name, flag=flag, method=method, crc=crc,
                compress_size=csize, file_size=usize,
                local_header=local_header, compressed=compressed,
                descriptor=descriptor, central=central,
            )
        )
    return entries, eocd


def _rebuild_descriptor(template: bytes, crc: int, csize: int, usize: int) -> bytes:
    """Re-emit a data descriptor matching the source's signature convention."""
    if not template:
        return b""
    body = struct.pack("<III", crc, csize, usize)
    return (_DD_SIG + body) if template[:4] == _DD_SIG else body


def _patch_local_header(header: bytes, flag: int, crc: int, csize: int, usize: int) -> bytes:
    """Patch crc/sizes into a local header (no-ops in data-descriptor mode)."""
    if flag & _FLAG_DATA_DESCRIPTOR:
        # Streaming mode: the local header carries zeros and the real values
        # live in the trailing descriptor. Leave it exactly as the source had it.
        return header
    out = bytearray(header)
    out[14:26] = struct.pack("<III", crc, csize, usize)
    return bytes(out)


def _patch_central(record: bytes, crc: int, csize: int, usize: int, offset: int) -> bytes:
    out = bytearray(record)
    out[16:28] = struct.pack("<III", crc, csize, usize)
    out[42:46] = struct.pack("<I", offset)
    return bytes(out)


def _patch_eocd(eocd: bytes, count: int, cd_size: int, cd_offset: int) -> bytes:
    out = bytearray(eocd)
    out[8:10] = struct.pack("<H", count)
    out[10:12] = struct.pack("<H", count)
    out[12:16] = struct.pack("<I", cd_size)
    out[16:20] = struct.pack("<I", cd_offset)
    return bytes(out)


def _synth_entry(name: str, template: _RawEntry, payload: bytes, compresslevel: int) -> _RawEntry:
    """Build a brand-new member, copying the template's framing conventions."""
    enc = name.encode("utf-8")
    flag = template.flag | _FLAG_UTF8
    method = template.method
    if method == 8:
        c = zlib.compressobj(compresslevel, zlib.DEFLATED, -15)
        compressed = c.compress(payload) + c.flush()
    else:
        compressed = payload
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    t_lh = template.local_header
    xver, _flag, _method, dtime, ddate = struct.unpack("<HHHHH", t_lh[4:14])
    streaming = bool(flag & _FLAG_DATA_DESCRIPTOR)
    lh = _LH_SIG + struct.pack(
        "<HHHHHIIIHH",
        xver, flag, method, dtime, ddate,
        0 if streaming else crc,
        0 if streaming else len(compressed),
        0 if streaming else len(payload),
        len(enc), 0,
    ) + enc
    t_cd = template.central
    cver = struct.unpack("<H", t_cd[4:6])[0]
    cd = _CD_SIG + struct.pack(
        "<HHHHHHIIIHHHHHII",
        cver, xver, flag, method, dtime, ddate,
        crc, len(compressed), len(payload),
        len(enc), 0, 0, 0, 0, 0, 0,
    ) + enc
    return _RawEntry(
        name=name, flag=flag, method=method, crc=crc,
        compress_size=len(compressed), file_size=len(payload),
        local_header=lh, compressed=compressed,
        descriptor=_rebuild_descriptor(template.descriptor, crc, len(compressed), len(payload)),
        central=cd,
    )


# --------------------------------------------------------------------------
# public image API
# --------------------------------------------------------------------------


@dataclass
class NitroImage:
    """A parsed nitro-image: verbatim ZIP members + lazy per-entry decryption."""

    path: Path | None
    raw: bytes
    _entries: list[_RawEntry] = field(repr=False, default_factory=list)
    _eocd: bytes = field(repr=False, default=b"")
    _plain: dict[str, bytes] = field(repr=False, default_factory=dict)
    _ivs: dict[str, bytes] = field(repr=False, default_factory=dict)

    # -- construction --

    @classmethod
    def load(cls, path: Path | str | None = None) -> "NitroImage":
        p = Path(path) if path is not None else nitro_image_path()
        if not p.is_file():
            raise NitroImageError(f"nitro-image not found (or is a directory): {p}")
        return cls.from_bytes(p.read_bytes(), path=p)

    @classmethod
    def from_bytes(cls, data: bytes, path: Path | None = None) -> "NitroImage":
        entries, eocd = _parse_zip(data)
        return cls(path=path, raw=data, _entries=entries, _eocd=eocd)

    # -- accessors --

    def names(self) -> list[str]:
        """Member names in central-directory order."""
        return [e.name for e in self._entries]

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: object) -> bool:
        return any(e.name == name for e in self._entries)

    def _entry(self, name: str) -> _RawEntry:
        for e in self._entries:
            if e.name == name:
                return e
        raise KeyError(name)

    def stored_payload(self, name: str) -> bytes:
        """The member's decompressed-but-still-encrypted payload."""
        return self._entry(name).decompressed()

    def _decrypt(self, name: str) -> None:
        _version, iv, pt = decrypt_entry(self.stored_payload(name))
        self._ivs[name] = iv
        self._plain[name] = pt

    def plaintext(self, name: str) -> bytes:
        """The member's decrypted ``.nitrobin`` bytes."""
        if name not in self._plain:
            self._decrypt(name)
        return self._plain[name]

    def iv(self, name: str) -> bytes:
        """The member's stored 198-byte IV (reused when re-encrypting)."""
        if name not in self._ivs:
            self._decrypt(name)
        return self._ivs[name]

    def to_dict(self) -> dict[str, bytes]:
        """``{member name: decrypted plaintext}`` in central-directory order."""
        return {name: self.plaintext(name) for name in self.names()}


def read_image(path: Path | str | None = None) -> dict[str, bytes]:
    """Decrypt an entire nitro-image to ``{name: plaintext}``."""
    return NitroImage.load(path).to_dict()


def read_entry(path: Path | str | None, name: str) -> bytes:
    """Decrypt a single member. Raises ``KeyError`` when the name is absent."""
    return NitroImage.load(path).plaintext(name)


@dataclass
class WriteReport:
    """Accounting for one :func:`write_image` call."""

    out_path: Path
    entry_count: int
    changed: list[str]
    added: list[str]
    removed: list[str]
    byte_identical: bool
    backup_sha256: str | None = None

    def summary(self) -> str:
        parts = [
            f"{self.entry_count} entries -> {self.out_path}",
            f"changed={len(self.changed)}",
            f"added={len(self.added)}",
            f"removed={len(self.removed)}",
            "byte-identical" if self.byte_identical else "differs from source",
        ]
        return "  ".join(parts)


# -- guards ----------------------------------------------------------------


def _install_roots() -> list[Path]:
    resolved = []
    for r in paths.bitwig_install_roots():
        try:
            resolved.append(r.resolve())
        except OSError:  # pragma: no cover
            resolved.append(r)
    return resolved


def is_inside_install(path: Path | str) -> bool:
    """True when ``path`` lies under any known Bitwig program-install root.

    Used by the write guard and by callers choosing a backup location, a
    backup inside the install is destroyed by the next package update.
    """
    return _is_inside_install(Path(path))


def _is_inside_install(path: Path) -> bool:
    try:
        target = path.resolve()
    except OSError:  # pragma: no cover
        target = path
    for root in _install_roots():
        if target == root or root in target.parents:
            return True
    return False


def backup_existing(src: Path | str, dst: Path | str) -> str:
    """Copy ``src`` to ``dst`` and verify the copy by sha256.

    Returns the shared hex digest. Refuses to overwrite an existing ``dst``
    (a backup that silently clobbers an older backup is not a backup).
    """
    src_p, dst_p = Path(src), Path(dst)
    if not src_p.is_file():
        raise NitroImageError(f"nothing to back up at {src_p}")
    if dst_p.exists():
        raise NitroImageError(f"backup target already exists: {dst_p}")
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_p, dst_p)
    src_digest = hashlib.sha256(src_p.read_bytes()).hexdigest()
    dst_digest = hashlib.sha256(dst_p.read_bytes()).hexdigest()
    if src_digest != dst_digest:  # pragma: no cover, would mean a bad copy
        raise NitroImageError(
            f"backup verification FAILED: {src_p} {src_digest} != {dst_p} {dst_digest}"
        )
    return src_digest


def _check_write_guard(
    out_path: Path, allow_install_overwrite: bool, backup_path: Path | str | None
) -> str | None:
    """Enforce the install-overwrite policy. Returns the backup digest, if any."""
    if not _is_inside_install(out_path):
        return None
    if not allow_install_overwrite:
        raise NitroImageError(
            f"refusing to write inside the Bitwig install: {out_path}. "
            "Pass allow_install_overwrite=True AND backup_path=<path outside the "
            "install> if you really mean it."
        )
    if backup_path is None:
        raise NitroImageError(
            f"writing to {out_path} requires an explicit backup_path outside the "
            "Bitwig install (a sha256-verified copy is made before the write)."
        )
    backup = Path(backup_path)
    if _is_inside_install(backup):
        raise NitroImageError(
            f"backup_path must be outside the Bitwig install (got {backup}); "
            "a backup inside the install is destroyed by a package update."
        )
    return backup_existing(out_path, backup)


# -- writer ----------------------------------------------------------------


def write_image(
    entries: dict[str, bytes],
    out_path: Path | str,
    source: "NitroImage | Path | str | None" = None,
    *,
    allow_install_overwrite: bool = False,
    backup_path: Path | str | None = None,
    require_same_size: bool = False,
    compresslevel: int = 6,
) -> WriteReport:
    """Repack a nitro-image from ``{name: decrypted plaintext}``.

    Untouched entries (plaintext byte-equal to the source's) keep their
    original stored bytes, IV, local header, data descriptor and
    central-directory record, so an identity repack reproduces the source file
    byte-for-byte. Modified entries are re-encrypted with their **original
    IV** and re-deflated.

    Args:
        entries: Member name -> decrypted ``.nitrobin`` bytes.
        out_path: Destination file.
        source: The image the entries came from (``NitroImage``, a path, or
            ``None`` for the installed image). Required for IV reuse, a name
            absent from the source gets a fresh random IV.
        allow_install_overwrite: Required to write inside a Bitwig install.
        backup_path: sha256-verified backup of ``out_path``, made before the
            write. Required (and must be outside the install) when overwriting
            an installed image.
        require_same_size: Raise if any entry's plaintext length differs from
            the source's, the invariant a same-size constant mutation keeps.
        compresslevel: zlib level for re-deflated members.

    Returns:
        A :class:`WriteReport`.
    """
    out = Path(out_path)
    backup_digest = _check_write_guard(out, allow_install_overwrite, backup_path)

    src: NitroImage | None
    if isinstance(source, NitroImage):
        src = source
    elif source is None:
        src = NitroImage.load() if nitro_image_path().is_file() else None
    else:
        src = NitroImage.load(source)
    if src is None:
        raise NitroImageError(
            "write_image needs a source image (IVs and ZIP framing are copied "
            "from it); pass source=<NitroImage|path>."
        )

    src_names = src.names()
    src_set = set(src_names)
    ordered = [n for n in src_names if n in entries] + [
        n for n in entries if n not in src_set
    ]
    removed = [n for n in src_names if n not in entries]
    added = [n for n in entries if n not in src_set]
    changed: list[str] = []

    template = src._entries[0] if src._entries else None
    out_entries: list[_RawEntry] = []
    for name in ordered:
        payload = entries[name]
        if name in src_set:
            original = src.plaintext(name)
            if require_same_size and len(payload) != len(original):
                raise NitroImageError(
                    f"{name}: plaintext size changed {len(original)} -> {len(payload)} "
                    "(require_same_size=True)"
                )
            if payload == original:
                out_entries.append(src._entry(name))
                continue
            changed.append(name)
            stored = encrypt_entry(payload, src.iv(name))
            base = src._entry(name)
            if base.method == 8:
                c = zlib.compressobj(compresslevel, zlib.DEFLATED, -15)
                compressed = c.compress(stored) + c.flush()
            else:
                compressed = stored
            crc = zlib.crc32(stored) & 0xFFFFFFFF
            out_entries.append(
                _RawEntry(
                    name=name, flag=base.flag, method=base.method, crc=crc,
                    compress_size=len(compressed), file_size=len(stored),
                    local_header=_patch_local_header(
                        base.local_header, base.flag, crc, len(compressed), len(stored)
                    ),
                    compressed=compressed,
                    descriptor=_rebuild_descriptor(
                        base.descriptor, crc, len(compressed), len(stored)
                    ),
                    central=base.central,
                )
            )
        else:
            if template is None:  # pragma: no cover, empty source
                raise NitroImageError("cannot synthesize entries from an empty source image")
            stored = encrypt_entry(payload, os.urandom(IV_SIZE))
            out_entries.append(_synth_entry(name, template, stored, compresslevel))

    blob = bytearray()
    offsets: list[int] = []
    for e in out_entries:
        offsets.append(len(blob))
        blob += e.local_header
        blob += e.compressed
        blob += e.descriptor
    cd_offset = len(blob)
    for e, off in zip(out_entries, offsets):
        blob += _patch_central(e.central, e.crc, e.compress_size, e.file_size, off)
    cd_size = len(blob) - cd_offset
    blob += _patch_eocd(src._eocd, len(out_entries), cd_size, cd_offset)

    data = bytes(blob)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return WriteReport(
        out_path=out,
        entry_count=len(out_entries),
        changed=changed,
        added=added,
        removed=removed,
        byte_identical=(data == src.raw),
        backup_sha256=backup_digest,
    )
