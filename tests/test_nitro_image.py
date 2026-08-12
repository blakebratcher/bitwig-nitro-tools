"""nitro-image entry cipher + ZIP repack, all with a DUMMY key.

The nitro-image entry payload is ``[version][iv:198][ciphertext]`` where the
ciphertext is a keystream XOR, so encryption and decryption are the same
transform and a round-trip is exact. These tests build a tiny *synthetic* ZIP
whose members are encrypted with a random dummy key; no Bitwig image, no real
key. The single real-install test skips when no image is present.
"""
from __future__ import annotations

import io
import os
import zipfile

import pytest

from bitwig_nitro import (
    NitroImage,
    NitroImageError,
    decrypt_entry,
    encrypt_entry,
    nitro_image_install_path,
    read_entry,
    write_image,
)
from bitwig_nitro import nitro_image as ni


def _dummy_image_key() -> bytes:
    return os.urandom(120)  # >= 17 bytes


def test_entry_cipher_roundtrip() -> None:
    """encrypt_entry then decrypt_entry recovers version, iv, and plaintext."""
    key = _dummy_image_key()
    iv = os.urandom(ni.IV_SIZE)
    plaintext = os.urandom(400)

    raw = encrypt_entry(plaintext, iv, key=key)
    assert len(raw) == 1 + ni.IV_SIZE + len(plaintext)
    assert raw[0] == ni.ENTRY_VERSION

    version, iv_out, pt_out = decrypt_entry(raw, key=key)
    assert version == ni.ENTRY_VERSION
    assert iv_out == iv
    assert pt_out == plaintext


def test_entry_cipher_is_self_inverse_on_plaintext() -> None:
    """The XOR transform means re-encrypting a decrypted payload is identity."""
    key = _dummy_image_key()
    iv = os.urandom(ni.IV_SIZE)
    plaintext = os.urandom(200)

    raw = encrypt_entry(plaintext, iv, key=key)
    _v, _iv, pt = decrypt_entry(raw, key=key)
    raw_again = encrypt_entry(pt, iv, key=key)
    assert raw_again == raw


def test_encrypt_entry_rejects_wrong_iv_size() -> None:
    with pytest.raises(NitroImageError):
        encrypt_entry(b"x" * 10, iv=b"tooshort", key=_dummy_image_key())


def test_decrypt_entry_rejects_short_payload() -> None:
    with pytest.raises(NitroImageError):
        decrypt_entry(b"\x02" + b"\x00" * 10, key=_dummy_image_key())


def _build_synthetic_image(key: bytes, names_payloads: dict[str, bytes]) -> bytes:
    """Build a minimal nitro-image-shaped ZIP encrypted with ``key``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in names_payloads.items():
            stored = encrypt_entry(payload, os.urandom(ni.IV_SIZE), key=key)
            zf.writestr(name, stored)
    return buf.getvalue()


def test_synthetic_image_decrypts(monkeypatch) -> None:
    """A synthetic ZIP of encrypted entries decrypts back to the plaintexts."""
    key = _dummy_image_key()
    payloads = {
        "filter/one.nitrobin": os.urandom(64),
        "grid/level/two.nitrobin": os.urandom(97),
    }
    monkeypatch.setenv("BITWIG_NITRO_IMAGE_KEY", key.hex())

    img = NitroImage.from_bytes(_build_synthetic_image(key, payloads))
    assert set(img.names()) == set(payloads)
    assert len(img) == len(payloads)
    assert "filter/one.nitrobin" in img

    decrypted = img.to_dict()
    assert decrypted == payloads


def test_synthetic_image_identity_repack(tmp_path, monkeypatch) -> None:
    """An unmodified repack is byte-identical to the source image."""
    key = _dummy_image_key()
    payloads = {
        "a/one.nitrobin": os.urandom(80),
        "b/two.nitrobin": os.urandom(53),
        "c/three.nitrobin": os.urandom(120),
    }
    monkeypatch.setenv("BITWIG_NITRO_IMAGE_KEY", key.hex())

    source_bytes = _build_synthetic_image(key, payloads)
    img = NitroImage.from_bytes(source_bytes)

    out = tmp_path / "nitro-image"
    report = write_image(img.to_dict(), out, source=img)

    assert report.byte_identical is True
    assert report.entry_count == len(payloads)
    assert report.changed == []
    assert out.read_bytes() == source_bytes


def test_synthetic_image_mutated_entry_repack(tmp_path, monkeypatch) -> None:
    """Changing one entry's plaintext repacks a loadable, re-decryptable image."""
    key = _dummy_image_key()
    payloads = {
        "a/one.nitrobin": os.urandom(80),
        "b/two.nitrobin": os.urandom(64),
    }
    monkeypatch.setenv("BITWIG_NITRO_IMAGE_KEY", key.hex())

    img = NitroImage.from_bytes(_build_synthetic_image(key, payloads))
    entries = img.to_dict()
    new_two = b"\xAB" * 64  # same length -> same-size mutation
    entries["b/two.nitrobin"] = new_two

    out = tmp_path / "nitro-image"
    report = write_image(entries, out, source=img, require_same_size=True)
    assert report.byte_identical is False
    assert report.changed == ["b/two.nitrobin"]

    # Re-read the repacked image and confirm both entries decrypt correctly.
    monkeypatch.setenv("BITWIG_NITRO_IMAGE_KEY", key.hex())
    assert read_entry(out, "a/one.nitrobin") == payloads["a/one.nitrobin"]
    assert read_entry(out, "b/two.nitrobin") == new_two


@pytest.mark.skipif(
    nitro_image_install_path() is None,
    reason="no installed Bitwig nitro-image present (offline-only environment)",
)
def test_real_install_image_parses() -> None:
    """When a real image exists, it parses as a ZIP (no decryption attempted).

    Decryption needs a real key which the suite never carries, so this only
    asserts the container is recognized and non-empty.
    """
    img = NitroImage.load()
    assert len(img) > 0
    assert all(name for name in img.names())
