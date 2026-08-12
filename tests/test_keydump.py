"""Offline tests for the runtime key-dump parser, selector, and --live CLI.

Every key value here is a SYNTHETIC fake, never a real Bitwig secret. The
tests prove the parse/select mechanics and the CLI's dump -> keys.json flow;
they never require a Bitwig install (validation-against-image is exercised only
by monkeypatching the image lookup away).
"""
from __future__ import annotations

import json

import pytest

from bitwig_nitro import (
    DEFAULT_DUMP_PATH,
    MissingKeyError,
    default_dump_path,
    parse_dump,
    select_image_key,
    write_keys_file,
)
from bitwig_nitro.cli import extract_keys

# Synthetic, obviously-fake keys. >= 16 bytes and non-zero so they pass the
# candidate filter. NONE of these is a real key.
FAKE_KEY = bytes.fromhex("de" * 20)          # 20 bytes of 0xde
FAKE_KEY_B = bytes.fromhex("ab" * 20)        # a different 20-byte value
ALL_ZERO = bytes(20)                          # rejected: all-zero
TOO_SHORT = bytes.fromhex("de" * 8)          # 8 bytes: rejected (< 16)


# --------------------------------------------------------------------------
# parse_dump: both handler shapes
# --------------------------------------------------------------------------


def _new_shape_dump(key: bytes = FAKE_KEY, iv: int = 198) -> dict:
    """The current shape-based controller dump."""
    return {
        "tool": "bitwig-nitro-keydump",
        "version": 1,
        "status": "ok",
        "message": "ok",
        "nitro_image": {
            "chain_class": "com.example.obf.Chain",
            "chain_length": 3,
            "transforms": [
                {"index": 0, "class": "com.example.obf.Head"},
                {
                    "index": 2,
                    "class": "com.example.obf.Cipher",
                    "byte_fields": {"k": key.hex()},
                    "int_fields": {"iv": iv},
                },
            ],
        },
    }


def _old_shape_dump(key: bytes = FAKE_KEY) -> dict:
    """The older named-field dump: fields inline on each transform."""
    return {
        "chain_length": 3,
        "transforms": [
            {"index": 0, "class": "Head", "full_class": "Head", "meta": "Head@1"},
            {
                "index": 1,
                "class": "Cipher",
                "key_field": {"length": len(key), "hex": key.hex()},
                "iv_size_field": 0,
                "flag": "true",
            },
            {
                "index": 2,
                "class": "Cipher",
                "key_field": {"length": len(key), "hex": key.hex()},
                "iv_size_field": 198,
                "flag": "true",
            },
        ],
    }


def test_parse_new_shape() -> None:
    result = parse_dump(_new_shape_dump())
    assert result.status == "ok"
    assert result.ok is True
    assert result.chain_class == "com.example.obf.Chain"
    assert result.chain_length == 3
    assert len(result.transforms) == 2
    cipher = result.transforms[1]
    assert cipher.byte_fields["k"] == FAKE_KEY
    assert cipher.int_fields["iv"] == 198
    assert cipher.has_iv_marker() is True
    # Structural head transform carries no byte/int payload fields.
    assert result.transforms[0].byte_fields == {}


def test_parse_old_shape() -> None:
    result = parse_dump(_old_shape_dump())
    assert result.chain_length == 3
    assert len(result.transforms) == 3
    # "meta" ("Head@1") and "flag" ("true") are not hex and not ints -> dropped.
    assert result.transforms[0].byte_fields == {}
    assert result.transforms[0].int_fields == {}
    t2 = result.transforms[2]
    assert t2.byte_fields["key_field"] == FAKE_KEY
    assert t2.int_fields["iv_size_field"] == 198
    assert t2.has_iv_marker() is True
    # index 1 shares the key but its iv int is 0.
    assert result.transforms[1].has_iv_marker() is False


def test_parse_tolerates_non_dict() -> None:
    result = parse_dump(["not", "a", "dict"])  # type: ignore[arg-type]
    assert result.transforms == []
    assert result.status is None


# --------------------------------------------------------------------------
# select_image_key
# --------------------------------------------------------------------------


def test_select_prefers_iv_marker() -> None:
    """The iv==198 transform's key wins over an unmarked, different key."""
    result = parse_dump(
        {
            "nitro_image": {
                "transforms": [
                    {"byte_fields": {"a": FAKE_KEY_B.hex()}, "int_fields": {"iv": 0}},
                    {"byte_fields": {"b": FAKE_KEY.hex()}, "int_fields": {"iv": 198}},
                ]
            }
        }
    )
    assert select_image_key(result) == FAKE_KEY
    assert result.candidates == []


def test_select_shared_value_fallback() -> None:
    """No iv marker anywhere: the single shared byte[] is taken."""
    result = parse_dump(
        {
            "nitro_image": {
                "transforms": [
                    {"byte_fields": {"a": FAKE_KEY.hex()}, "int_fields": {"iv": 0}},
                    {"byte_fields": {"b": FAKE_KEY.hex()}, "int_fields": {"iv": 0}},
                ]
            }
        }
    )
    assert select_image_key(result) == FAKE_KEY
    assert result.candidates == []


def test_select_dedups_identical_hex() -> None:
    """Repeated identical values collapse to one and select cleanly."""
    result = parse_dump(
        {
            "nitro_image": {
                "transforms": [
                    {
                        "byte_fields": {
                            "a": FAKE_KEY.hex(),
                            "b": FAKE_KEY.hex(),
                            "c": FAKE_KEY.hex(),
                        }
                    }
                ]
            }
        }
    )
    assert select_image_key(result) == FAKE_KEY


def test_select_ambiguous_returns_none_with_candidates() -> None:
    """Two distinct unmarked candidates -> None + both exposed as candidates."""
    result = parse_dump(
        {
            "nitro_image": {
                "transforms": [
                    {"byte_fields": {"a": FAKE_KEY.hex()}},
                    {"byte_fields": {"b": FAKE_KEY_B.hex()}},
                ]
            }
        }
    )
    assert select_image_key(result) is None
    assert set(result.candidates) == {FAKE_KEY, FAKE_KEY_B}


def test_select_rejects_all_zero_and_short() -> None:
    """All-zero and <16-byte arrays are ignored; a real one still wins."""
    result = parse_dump(
        {
            "nitro_image": {
                "transforms": [
                    {"byte_fields": {"z": ALL_ZERO.hex(), "s": TOO_SHORT.hex()}},
                    {"byte_fields": {"k": FAKE_KEY.hex()}},
                ]
            }
        }
    )
    assert select_image_key(result) == FAKE_KEY


def test_select_rejects_all_invalid() -> None:
    """When nothing passes the filter, select returns None and no candidates."""
    result = parse_dump(
        {
            "nitro_image": {
                "transforms": [
                    {"byte_fields": {"z": ALL_ZERO.hex(), "s": TOO_SHORT.hex()}}
                ]
            }
        }
    )
    assert select_image_key(result) is None
    assert result.candidates == []


def test_select_on_old_shape() -> None:
    """The real old-shape dump selects the shared key via the iv marker."""
    result = parse_dump(_old_shape_dump())
    assert select_image_key(result) == FAKE_KEY


# --------------------------------------------------------------------------
# default_dump_path
# --------------------------------------------------------------------------


def test_default_dump_path_env_override(monkeypatch, tmp_path) -> None:
    target = tmp_path / "somewhere" / "dump.json"
    monkeypatch.setenv("BITWIG_NITRO_KEYDUMP", str(target))
    assert default_dump_path() == target
    assert DEFAULT_DUMP_PATH() == target  # exported alias


def test_default_dump_path_default(monkeypatch) -> None:
    monkeypatch.delenv("BITWIG_NITRO_KEYDUMP", raising=False)
    p = default_dump_path()
    assert p.name == "nitro-key-dump.json"
    assert p.parent.name == ".bitwig-nitro"


# --------------------------------------------------------------------------
# write_keys_file: image-only / dag-only / both-none
# --------------------------------------------------------------------------


def test_write_keys_file_image_only(tmp_path) -> None:
    dest = write_keys_file(image_key_hex=FAKE_KEY.hex(), path=tmp_path / "k.json")
    doc = json.loads(dest.read_text())
    assert doc == {"nitro_image_key": FAKE_KEY.hex()}
    assert "dag_key" not in doc


def test_write_keys_file_dag_only(tmp_path) -> None:
    dest = write_keys_file(dag_key_hex="deadbeef", path=tmp_path / "k.json")
    doc = json.loads(dest.read_text())
    assert doc == {"dag_key": "deadbeef"}


def test_write_keys_file_requires_at_least_one(tmp_path) -> None:
    with pytest.raises(MissingKeyError):
        write_keys_file(path=tmp_path / "k.json")
    assert not (tmp_path / "k.json").exists()


# --------------------------------------------------------------------------
# CLI --live
# --------------------------------------------------------------------------


def test_cli_live_writes_keys(tmp_path, monkeypatch, capsys) -> None:
    """--live reads a synthetic dump and writes keys.json with the fake key."""
    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps(_new_shape_dump()))
    monkeypatch.setenv("BITWIG_NITRO_KEYDUMP", str(dump))
    # No image on this machine (or ignore the real one): force "unvalidated".
    monkeypatch.setattr(extract_keys, "nitro_image_install_path", lambda: None)

    out = tmp_path / "keys.json"
    rc = extract_keys.main(["--live", "--out", str(out)])
    assert rc == 0

    doc = json.loads(out.read_text())
    assert doc == {"nitro_image_key": FAKE_KEY.hex()}
    assert "not validated" in capsys.readouterr().out


def test_cli_live_missing_dump_exit_2(tmp_path, monkeypatch, capsys) -> None:
    """--live with no dump present exits 2 with install instructions."""
    missing = tmp_path / "nope.json"
    monkeypatch.setenv("BITWIG_NITRO_KEYDUMP", str(missing))
    rc = extract_keys.main(["--live"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "no key dump found" in err
    assert "--install-controller" in err


def test_cli_live_ambiguous_dump_exit_2(tmp_path, monkeypatch, capsys) -> None:
    """A dump with two distinct unmarked keys is user-actionable (exit 2)."""
    dump = tmp_path / "dump.json"
    dump.write_text(
        json.dumps(
            {
                "status": "ok",
                "nitro_image": {
                    "transforms": [
                        {"byte_fields": {"a": FAKE_KEY.hex()}},
                        {"byte_fields": {"b": FAKE_KEY_B.hex()}},
                    ]
                },
            }
        )
    )
    monkeypatch.setenv("BITWIG_NITRO_KEYDUMP", str(dump))
    monkeypatch.setattr(extract_keys, "nitro_image_install_path", lambda: None)
    rc = extract_keys.main(["--live", "--out", str(tmp_path / "keys.json")])
    assert rc == 2
    assert "no nitro-image key found" in capsys.readouterr().err


def test_cli_live_error_status_exit_2(tmp_path, monkeypatch, capsys) -> None:
    """A controller-reported failure surfaces as exit 2 with its message."""
    dump = tmp_path / "dump.json"
    dump.write_text(
        json.dumps(
            {"status": "error", "message": "static field roster: ...", "nitro_image": None}
        )
    )
    monkeypatch.setenv("BITWIG_NITRO_KEYDUMP", str(dump))
    rc = extract_keys.main(["--live"])
    assert rc == 2
    assert "static field roster" in capsys.readouterr().err


# --------------------------------------------------------------------------
# CLI: --from-jar is gone; manual + no-op paths
# --------------------------------------------------------------------------


def test_cli_from_jar_removed() -> None:
    """--from-jar no longer exists (argparse rejects the unknown flag)."""
    with pytest.raises(SystemExit):
        extract_keys.main(["--from-jar", "/some/bitwig.jar"])


def test_cli_no_args_exit_2(capsys) -> None:
    rc = extract_keys.main([])
    assert rc == 2
    assert "nothing to do" in capsys.readouterr().err


def test_cli_manual_image_only(tmp_path, capsys) -> None:
    out = tmp_path / "keys.json"
    rc = extract_keys.main(["--image-key", FAKE_KEY.hex(), "--out", str(out)])
    assert rc == 0
    doc = json.loads(out.read_text())
    assert doc == {"nitro_image_key": FAKE_KEY.hex()}


def test_cli_install_controller(tmp_path, capsys) -> None:
    """--install-controller copies the bundled script into --controllers-dir."""
    dest_dir = tmp_path / "Controller Scripts"
    rc = extract_keys.main(
        ["--install-controller", "--controllers-dir", str(dest_dir)]
    )
    # The bundled controller ships in the repo checkout; if absent this returns
    # 1. In a source tree it must succeed and place the file.
    if rc == 0:
        assert (dest_dir / extract_keys.CONTROLLER_FILENAME).is_file()
        assert "Installed controller" in capsys.readouterr().out
    else:
        assert rc == 1
        assert "bundled controller not found" in capsys.readouterr().err
