"""Key resolution: env override, keys.json, and the MissingKeyError path.

No real key appears here. The values written are arbitrary dummy hex, the
tests only prove the *resolution mechanism*, never a Bitwig secret.
"""
from __future__ import annotations

import json

import pytest

from bitwig_nitro import (
    MissingKeyError,
    resolve_dag_key,
    resolve_nitro_image_key,
    write_keys_file,
)
from bitwig_nitro import keys as keys_mod
from bitwig_nitro import paths as paths_mod

DUMMY_DAG_HEX = "00112233445566778899aabbccddeeff0011"
DUMMY_IMAGE_HEX = "aabbccddeeff00112233"


def test_resolve_from_keys_json(tmp_path, monkeypatch) -> None:
    """A keys.json pointed at by $BITWIG_NITRO_KEYS resolves both keys."""
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(
        json.dumps({"dag_key": DUMMY_DAG_HEX, "nitro_image_key": DUMMY_IMAGE_HEX})
    )
    monkeypatch.setenv("BITWIG_NITRO_KEYS", str(keys_file))

    assert resolve_dag_key() == bytes.fromhex(DUMMY_DAG_HEX)
    assert resolve_nitro_image_key() == bytes.fromhex(DUMMY_IMAGE_HEX)


def test_env_override_beats_file(tmp_path, monkeypatch) -> None:
    """A direct env hex override wins over any keys.json."""
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(
        json.dumps({"dag_key": DUMMY_DAG_HEX, "nitro_image_key": DUMMY_IMAGE_HEX})
    )
    monkeypatch.setenv("BITWIG_NITRO_KEYS", str(keys_file))
    monkeypatch.setenv("BITWIG_NITRO_DAG_KEY", "deadbeef")

    assert resolve_dag_key() == bytes.fromhex("deadbeef")
    # image key still comes from the file
    assert resolve_nitro_image_key() == bytes.fromhex(DUMMY_IMAGE_HEX)


def test_missing_key_raises(tmp_path, monkeypatch) -> None:
    """With no env and no readable keys.json, MissingKeyError is raised."""
    # Point every search path at a file that does not exist, so a developer's
    # real ~/.config/bitwig-nitro/keys.json cannot satisfy the lookup.
    monkeypatch.setattr(
        paths_mod, "keys_search_paths", lambda: [tmp_path / "does_not_exist.json"]
    )

    with pytest.raises(MissingKeyError) as excinfo:
        resolve_dag_key()
    msg = str(excinfo.value)
    assert "BITWIG_NITRO_DAG_KEY" in msg
    assert "KEY_EXTRACTION.md" in msg

    with pytest.raises(MissingKeyError) as excinfo2:
        resolve_nitro_image_key()
    assert "BITWIG_NITRO_IMAGE_KEY" in str(excinfo2.value)


def test_write_keys_file_roundtrip(tmp_path, monkeypatch) -> None:
    """write_keys_file persists a keys.json that then resolves."""
    dest = write_keys_file(DUMMY_DAG_HEX, DUMMY_IMAGE_HEX, path=tmp_path / "k.json")
    assert dest.exists()
    doc = json.loads(dest.read_text())
    assert doc["dag_key"] == DUMMY_DAG_HEX
    assert doc["nitro_image_key"] == DUMMY_IMAGE_HEX

    monkeypatch.setenv("BITWIG_NITRO_KEYS", str(dest))
    assert resolve_dag_key() == bytes.fromhex(DUMMY_DAG_HEX)


def test_write_keys_file_rejects_bad_hex(tmp_path) -> None:
    """Malformed hex is rejected before anything is written."""
    with pytest.raises(MissingKeyError):
        write_keys_file("not-hex!", DUMMY_IMAGE_HEX, path=tmp_path / "bad.json")
    assert not (tmp_path / "bad.json").exists()


def test_malformed_keys_json_raises(tmp_path, monkeypatch) -> None:
    """A keys.json that is not valid JSON surfaces MissingKeyError."""
    keys_file = tmp_path / "keys.json"
    keys_file.write_text("{ this is not json")
    monkeypatch.setattr(paths_mod, "keys_search_paths", lambda: [keys_file])

    with pytest.raises(MissingKeyError):
        resolve_dag_key()


def test_no_default_key_embedded() -> None:
    """The keys module must not carry any embedded default key value."""
    # Guard against a real key being accidentally hardcoded: the module holds
    # only field/env-var name constants, never key bytes.
    src = keys_mod.__file__
    with open(src, "r", encoding="utf-8") as fh:
        text = fh.read()
    # No suspiciously long hex literal (>= 32 hex chars) should appear.
    import re

    long_hex = re.findall(r"[0-9a-fA-F]{32,}", text)
    assert not long_hex, f"unexpected long hex literal in keys.py: {long_hex}"
