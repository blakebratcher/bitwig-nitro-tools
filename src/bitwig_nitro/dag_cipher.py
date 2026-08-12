"""Dag cipher implementation for Bitwig encoding 0004 files.

Decrypts .bwmodule, .bwmodulator, .bwdevice, .bwclip, .bwcurve files.

This module ships no cipher key. The key is resolved at call time from your
own configuration (see :mod:`bitwig_nitro.keys` and ``docs/KEY_EXTRACTION.md``)
whenever ``dag_decrypt`` is called without an explicit ``key`` argument.

Usage:
    from bitwig_nitro.dag_cipher import decrypt_0004, read_encrypted_btwg

    # Decrypt raw bytes
    plaintext = decrypt_0004(encrypted_data)

    # Read and decrypt a BtWg file
    header, body = read_encrypted_btwg("path/to/file.bwdevice")
"""

from __future__ import annotations

from pathlib import Path


def _rotate_right(byte: int, amount: int) -> int:
    """Rotate a byte right by amount bits."""
    amount = amount % 8
    return ((byte >> amount) | (byte << (8 - amount))) & 0xFF


def dag_decrypt(data: bytes, key: bytes | None = None, iv: bytes | None = None) -> bytes:
    """Decrypt data using the Dag cipher.

    The Dag cipher XORs each byte with a key byte (SWC) and a rotated
    pad byte (azd). The rotation amount increments each time the SWC
    key wraps around. XOR is self-inverse so encrypt == decrypt.

    Algorithm (from Dag.class bytecode disassembly):
        if r3B >= len(SWC): r3B = 0; uEK += 1
        if Xzy >= len(azd): Xzy = 0
        output = input ^ SWC[r3B++] ^ rotate_right(azd[Xzy++], uEK & 7)

    Args:
        data: Ciphertext bytes (after IV extraction if applicable).
        key: Cipher key. When ``None``, it is resolved via
            :func:`bitwig_nitro.keys.resolve_dag_key` (see
            ``docs/KEY_EXTRACTION.md``).
        iv: 16-byte initialization vector. If None, first 16 bytes of data are used as IV.

    Returns:
        Decrypted plaintext bytes.
    """
    if key is None:
        from .keys import resolve_dag_key

        key = resolve_dag_key()
    if len(key) < 17:
        raise ValueError(f"Key must be at least 17 bytes (got {len(key)})")

    if iv is None:
        if len(data) < 16:
            if len(data) == 0:
                return b""
            raise ValueError(f"Data must be at least 16 bytes for IV extraction (got {len(data)})")
        iv = data[:16]
        data = data[16:]

    # Key setup (from Dag.<init> bytecode)
    swc = key[16:]          # SWC = Arrays.copyOfRange(key, 16, key.length)
    azd = bytearray(len(iv) + 16)  # azd = new byte[iv.length + 16]
    azd[:len(iv)] = iv              # System.arraycopy(iv, 0, azd, 0, iv.length)
    azd[len(iv):] = key[:16]       # System.arraycopy(key, 0, azd, iv.length, 16)

    r3b = 0   # SWC index
    xzy = 0   # azd index
    uek = 0   # rotation counter (increments when SWC wraps)

    result = bytearray(len(data))

    for i in range(len(data)):
        if r3b >= len(swc):
            r3b = 0
            uek += 1
        if xzy >= len(azd):
            xzy = 0

        # The rotation applies to the azd pad byte, not the input/output
        rotated_azd = _rotate_right(azd[xzy], uek & 7)
        result[i] = data[i] ^ swc[r3b] ^ rotated_azd

        r3b += 1
        xzy += 1

    return bytes(result)


def decrypt_0004(file_data: bytes) -> tuple[dict, bytes]:
    """Decrypt a BtWg encoding 0004 file.

    Args:
        file_data: Complete file contents (including 42-byte BtWg header).

    Returns:
        (header_info, decrypted_body) where header_info has
        'version', 'encoding', 'body_offset' keys.

    Raises:
        ValueError: If file is not a valid BtWg encoding 0004 file.
    """
    if len(file_data) < 43 or file_data[:4] != b"BtWg":
        raise ValueError("Not a BtWg file")

    encoding = file_data[8:12].decode("ascii")
    if encoding != "0004":
        raise ValueError(f"Not encoding 0004 (got {encoding})")

    body_offset = int(file_data[16:24], 16)

    header = {
        "version": file_data[4:8].decode("ascii"),
        "encoding": encoding,
        "body_offset": body_offset,
    }

    # Skip 42-byte header + 1-byte version prefix, then decrypt
    encrypted = file_data[43:]
    decrypted = dag_decrypt(encrypted)

    return header, decrypted


def read_encrypted_btwg(path: str | Path) -> tuple[dict, bytes]:
    """Read and decrypt a BtWg encoding 0004 file from disk.

    Args:
        path: Path to .bwmodule, .bwmodulator, .bwdevice, .bwclip, or .bwcurve file.

    Returns:
        (header_info, decrypted_body)
    """
    data = Path(path).read_bytes()
    return decrypt_0004(data)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python dag_cipher.py <file.bwcurve|.bwclip|.bwdevice|...>")
        sys.exit(1)

    path = sys.argv[1]
    header, body = read_encrypted_btwg(path)
    print(f"File: {path}")
    print(f"Header: {header}")
    print(f"Decrypted body: {len(body)} bytes")
    print(f"First 64 bytes (hex): {body[:64].hex()}")

    # Check if it looks like TLV
    if body[:4] == b"BtWg":
        print("Body starts with BtWg, nested container!")
    elif body[0:2] == b"\x00\x00":
        print("Body starts with 00 00, likely TLV separator")
    else:
        print(f"Body starts with: {body[:4].hex()}")
