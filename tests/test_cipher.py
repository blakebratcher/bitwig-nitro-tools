"""Dag cipher: XOR keystream is self-inverse.

The cipher is a keystream XOR, so decryption and encryption are the same
operation: applying it twice with the same key + IV returns the input. These
tests prove that property with random data and a random DUMMY key, no real
Bitwig cipher key is used or needed.
"""
from __future__ import annotations

import os
import random

import pytest

from bitwig_nitro import dag_decrypt


def _dummy_key(n: int = 40) -> bytes:
    """A random key of at least 17 bytes (the cipher's minimum)."""
    return os.urandom(n)


@pytest.mark.parametrize("size", [0, 1, 16, 17, 63, 256, 4096])
def test_dag_decrypt_is_xor_self_inverse(size: int) -> None:
    """dag_decrypt(dag_decrypt(x)) == x for a fixed key + IV."""
    rng = random.Random(size)  # deterministic per size
    x = bytes(rng.getrandbits(8) for _ in range(size))
    key = bytes(rng.getrandbits(8) for _ in range(40))
    iv = bytes(rng.getrandbits(8) for _ in range(16))

    once = dag_decrypt(x, key=key, iv=iv)
    twice = dag_decrypt(once, key=key, iv=iv)

    assert twice == x


def test_self_inverse_holds_across_random_keys() -> None:
    """The involution holds for many random keys, ivs, and payloads."""
    for _ in range(50):
        key = _dummy_key(random.randint(17, 80))
        iv = os.urandom(16)
        x = os.urandom(random.randint(1, 500))
        assert dag_decrypt(dag_decrypt(x, key=key, iv=iv), key=key, iv=iv) == x


def test_nontrivial_transform() -> None:
    """A single pass actually changes the data (it is not a no-op XOR)."""
    key = b"\x11" * 17 + b"\x22" * 8
    iv = b"\x33" * 16
    x = b"\x00" * 64  # zeros so output == keystream
    out = dag_decrypt(x, key=key, iv=iv)
    assert out != x
    assert len(out) == len(x)


def test_iv_extraction_when_iv_omitted() -> None:
    """With no explicit IV, the first 16 bytes are consumed as the IV.

    The result is 16 bytes shorter than the input, and re-prepending the same
    IV bytes and decrypting again recovers the tail (self-inverse over the
    same IV).
    """
    key = _dummy_key()
    iv = os.urandom(16)
    payload = os.urandom(64)
    framed = iv + payload

    decoded = dag_decrypt(framed, key=key)  # IV taken from framed[:16]
    assert len(decoded) == len(payload)

    # Same transform with the IV supplied explicitly must match.
    decoded_explicit = dag_decrypt(payload, key=key, iv=iv)
    assert decoded == decoded_explicit


def test_short_key_rejected() -> None:
    """Keys shorter than 17 bytes are rejected."""
    with pytest.raises(ValueError):
        dag_decrypt(b"some data here!!", key=b"tooShort", iv=b"\x00" * 16)
