"""Shared pytest configuration for the bitwig-nitro-tools suite.

The whole suite is offline and key-free. This conftest:

  * makes the ``tests/`` directory importable so ``fixtures.builders`` resolves
    regardless of pytest's import mode, and
  * exposes the synthetic module (built from the public DSL) as fixtures.

No test in this suite requires a Bitwig installation, decrypted DSP content,
or a real cipher key. The one test that touches an installed ``nitro-image``
skips cleanly when none is present.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make ``tests/`` importable so ``from fixtures.builders import ...`` works
# under any pytest import mode.
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from fixtures import builders  # noqa: E402


@pytest.fixture
def synthetic_ast():
    """A freshly built ``synthetic_gain`` AST (``NitroFile`` root)."""
    return builders.build_synthetic_module()


@pytest.fixture
def synthetic_bytes() -> bytes:
    """The synthetic module serialized to ``.nitrobin`` bytes (built fresh)."""
    return builders.build_synthetic_module_bytes()


@pytest.fixture
def fixtures_dir() -> Path:
    """Directory holding committed synthetic fixtures."""
    return builders.FIXTURE_DIR


@pytest.fixture(autouse=True)
def _clear_key_env(monkeypatch):
    """Ensure no ambient key configuration leaks into a test.

    A developer running the suite may have real keys exported. Every test
    starts from a clean slate; tests that need a key set their own dummy one.
    """
    for var in (
        "BITWIG_NITRO_DAG_KEY",
        "BITWIG_NITRO_IMAGE_KEY",
        "BITWIG_NITRO_KEYS",
    ):
        monkeypatch.delenv(var, raising=False)
