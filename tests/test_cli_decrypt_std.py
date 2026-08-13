"""Tests for the nitro-std decrypt CLI + path resolvers (all offline).

The decrypt itself runs in a live Bitwig JVM (nitro-std uses a runtime PRNG
cipher), so these cover the plumbing: path resolution, controller install, and
the manifest report/verify logic — never the JVM step.
"""
from __future__ import annotations

import json

from bitwig_nitro import paths
from bitwig_nitro.cli import decrypt_std


# --------------------------------------------------------------------------- #
# Path resolvers                                                              #
# --------------------------------------------------------------------------- #

def test_nitro_std_install_path_env_override(tmp_path, monkeypatch):
    std = tmp_path / "nitro-std"
    std.write_bytes(b"PK\x03\x04")
    monkeypatch.setenv("BITWIG_NITRO_STD", str(std))
    assert paths.nitro_std_install_path() == std


def test_nitro_std_install_path_missing_override(tmp_path, monkeypatch):
    monkeypatch.setenv("BITWIG_NITRO_STD", str(tmp_path / "nope"))
    assert paths.nitro_std_install_path() is None


def test_nitro_std_output_and_manifest_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BITWIG_NITRO_STD_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("BITWIG_NITRO_STD_DUMP", str(tmp_path / "m.json"))
    assert paths.nitro_std_output_dir() == tmp_path / "out"
    assert paths.nitro_std_manifest_path() == tmp_path / "m.json"


def test_nitro_std_defaults(monkeypatch):
    for var in ("BITWIG_NITRO_STD_OUT", "BITWIG_NITRO_STD_DUMP"):
        monkeypatch.delenv(var, raising=False)
    assert paths.nitro_std_output_dir().name == "nitro-std-decrypted"
    assert paths.nitro_std_manifest_path().name == "nitro-std-dump.json"


# --------------------------------------------------------------------------- #
# Bundled controller                                                          #
# --------------------------------------------------------------------------- #

def test_bundled_controller_ships():
    p = decrypt_std.bundled_controller_path()
    assert p.is_file(), f"controller not packaged at {p}"
    text = p.read_text()
    assert "Nitro Std Dump" in text
    # the decrypt path must target the (ctx, byte[]) -> Reader source method
    assert "java.io.Reader" in text and "[B" in text


def test_install_controller_copies(tmp_path):
    rc = decrypt_std.main(["--install-controller", "--controllers-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / decrypt_std.CONTROLLER_FILENAME).is_file()


# --------------------------------------------------------------------------- #
# Report / verify                                                             #
# --------------------------------------------------------------------------- #

def _write_manifest(tmp_path, files, status="ok", make_files=True):
    out_dir = tmp_path / "decrypted"
    out_dir.mkdir(exist_ok=True)
    if make_files:
        for f in files:
            dest = out_dir / f["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x" * f["size"])
    manifest = {
        "tool": "bitwig-nitro-stddump",
        "version": 1,
        "status": status,
        "message": "ok",
        "nitro_std": "/opt/bitwig-studio/Library/nitro-std",
        "out_dir": str(out_dir),
        "files": files,
    }
    mpath = tmp_path / "nitro-std-dump.json"
    mpath.write_text(json.dumps(manifest))
    return mpath


def test_report_missing_manifest(tmp_path, capsys):
    rc = decrypt_std.main(["--manifest", str(tmp_path / "none.json")])
    assert rc == 1
    assert "has not run yet" in capsys.readouterr().err


def test_report_ok(tmp_path, capsys):
    files = [{"path": "math.nitro", "size": 10}, {"path": "filters/svf.nitro", "size": 20}]
    mpath = _write_manifest(tmp_path, files)
    rc = decrypt_std.main(["--manifest", str(mpath)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "2/2 present" in out


def test_report_missing_files_fails(tmp_path, capsys):
    files = [{"path": "math.nitro", "size": 10}, {"path": "gone.nitro", "size": 5}]
    mpath = _write_manifest(tmp_path, files, make_files=False)
    # create only the first
    (tmp_path / "decrypted" / "math.nitro").write_bytes(b"x" * 10)
    rc = decrypt_std.main(["--manifest", str(mpath)])
    assert rc == 1
    assert "missing" in capsys.readouterr().err


def test_report_error_status(tmp_path, capsys):
    mpath = _write_manifest(tmp_path, [], status="error")
    rc = decrypt_std.main(["--manifest", str(mpath)])
    assert rc == 1
    assert "did not succeed" in capsys.readouterr().err
