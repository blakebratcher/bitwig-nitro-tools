"""Legacy structural parser for Bitwig .nitrobin compiled DSP binaries.

⚠️ DEPRECATED for new work. This parser extracts STRUCTURE ONLY (imports,
fields, function names, port counts, annotations) and CANNOT enter function
bodies. For full-AST recovery use `nitrobin_decompiler.decompile_nitrobin_file()`.

Kept for backward compatibility with existing callers (e.g. modules that
only need port counts or function name lists). New code should prefer the
full-AST decompiler in `nitrobin_decompiler`.

Reads decrypted .nitrobin files and extracts the structural summary:
imports, fields, functions, annotations WITH VALUES, port declarations
(structural count by direction/type), and string references.

The .nitrobin format is a custom binary AST serialization:
  - Header: 0x0A 0x01 0x00 0x6F 0x04 <uint32 import_count>
  - Imports: 0x15 tag + string path + metadata
  - Body: Typed AST nodes with string symbols preserved
  - Strings: tag 0x06 + uint32 length + UTF-8
  - Annotations: 01 00 6B (name only) or 01 00 6C (name + value)
  - Ports: 01 00 <7A|7B|78|79|76|77|7C|4B|1B> (typed port declarations)

See the packaged nitro_ast_tags.json for the authoritative AST tag table.

Usage:
    from bitwig_nitro.nitrobin_parser import parse_nitrobin_file

    info = parse_nitrobin_file("reference/nitro-image-decrypted/grid/shaper/e_clip.dec")
    print(info.module_name)                    # "e_clip" (struct name)
    print(info.display_name)                   # "E-Clip"  (from @name annotation)
    print(info.port_counts)                    # {'audio_input': 2, ...}
    print(info.annotation_values)              # {'@name': 'E-Clip', '@block_optim': True}
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path


# Port tags: 0x01 0x00 <tag> introduces a typed port declaration
# in the AST body. The tag encodes the port direction and signal type.
PORT_TAGS = {
    0x7A: "audio_input",
    0x7B: "audio_output",
    0x78: "value_input",
    0x79: "value_output",
    0x76: "event_input",
    0x77: "event_output",
    0x7C: "display_output",
    0x4B: "midi_port",
    0x1B: "event_port",
}

# Annotation tags: 0x01 0x00 <tag> introduces an annotation
ANNO_PLAIN = 0x6B          # @flag (no value)
ANNO_NAME = 0x6C           # @key "string value"
ANNO_MAX = 0x6D            # @max <numeric>
ANNO_MIN = 0x6E            # @min <numeric>
ANNO_COMPRESS = 0x82       # @compress (standalone)

# String tag
STRING_TAG = 0x06


@dataclass
class NitroBinModule:
    """Parsed structure of a .nitrobin compiled DSP binary.

    Backward-compatible layout: the original fields (module_name, imports,
    fields, functions, annotations, references, total_strings, size,
    import_count) are preserved with their original types. New fields
    (annotation_values, display_name, description, icon, panel_display,
    port_counts) provide the richer structural view added in the
    exhaustive analysis pass.
    """

    module_name: str = ""
    import_count: int = 0
    imports: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    total_strings: int = 0
    size: int = 0

    # Enriched fields (populated by the structural extraction pass)
    annotation_values: dict[str, object] = field(default_factory=dict)
    display_name: str = ""
    description: str = ""
    icon: str = ""
    panel_display: str = ""
    port_counts: dict[str, int] = field(default_factory=dict)


def _extract_strings(data: bytes) -> list[tuple[int, str, int]]:
    """Find all strings in the binary (tag 0x06 + uint32 len + UTF-8).

    Returns list of (offset, string, pre_byte) tuples.
    """
    strings = []
    pos = 0
    while pos < len(data) - 5:
        if data[pos] == STRING_TAG:
            slen = struct.unpack_from(">I", data, pos + 1)[0]
            if 0 < slen < 500 and pos + 5 + slen <= len(data):
                try:
                    s = data[pos + 5:pos + 5 + slen].decode("utf-8")
                    if s.isprintable():
                        pre = data[pos - 1] if pos > 0 else -1
                        strings.append((pos, s, pre))
                        pos += 5 + slen
                        continue
                except UnicodeDecodeError:
                    pass
        pos += 1
    return strings


def _read_string_at(data: bytes, pos: int) -> tuple[str | None, int]:
    """Read a tag-0x06 string at `pos`. Returns (string, new_pos) or (None, pos)."""
    if pos + 5 > len(data) or data[pos] != STRING_TAG:
        return None, pos
    slen = struct.unpack_from(">I", data, pos + 1)[0]
    if slen > 1000 or pos + 5 + slen > len(data):
        return None, pos
    try:
        return data[pos + 5:pos + 5 + slen].decode("utf-8"), pos + 5 + slen
    except UnicodeDecodeError:
        return None, pos


def _extract_structural(data: bytes, info: NitroBinModule) -> None:
    """Second pass: walk 0x01 0x00 <tag> markers to extract annotations with
    values and count ports by direction/type.

    Populates info.annotation_values and info.port_counts in place.
    """
    pos = 5  # skip the 5-byte header
    while pos < len(data) - 2:
        if data[pos] == 0x01 and data[pos + 1] == 0x00:
            tag = data[pos + 2]

            # @name / @key "value", paired strings
            if tag == ANNO_NAME:
                p = pos + 3
                name, p = _read_string_at(data, p)
                value, p = _read_string_at(data, p)
                if name and name.startswith("@") and value is not None:
                    info.annotation_values[name] = value
                    pos = p
                    continue

            # Plain @flag, one string, no value
            elif tag == ANNO_PLAIN:
                p = pos + 3
                name, p = _read_string_at(data, p)
                if name and name.startswith("@"):
                    info.annotation_values[name] = True
                    pos = p
                    continue

            # @min / @max, numeric value (opaque to this parser)
            elif tag in (ANNO_MIN, ANNO_MAX):
                p = pos + 3
                name, p = _read_string_at(data, p)
                if name and name.startswith("@"):
                    info.annotation_values[name] = "(numeric)"
                    pos = p
                    continue

            # @compress standalone marker
            elif tag == ANNO_COMPRESS:
                info.annotation_values["@compress"] = True
                pos += 3
                continue

            # Port declaration, count by type/direction
            elif tag in PORT_TAGS:
                tag_name = PORT_TAGS[tag]
                info.port_counts[tag_name] = info.port_counts.get(tag_name, 0) + 1
                pos += 3
                continue

        pos += 1

    # Hoist common metadata annotations into dedicated fields
    av = info.annotation_values
    if isinstance(av.get("@name"), str):
        info.display_name = av["@name"]
    if isinstance(av.get("@desc"), str):
        info.description = av["@desc"]
    if isinstance(av.get("@icon"), str):
        info.icon = av["@icon"]
    if isinstance(av.get("@panel_display"), str):
        info.panel_display = av["@panel_display"]


def parse_nitrobin(data: bytes) -> NitroBinModule:
    """Parse a decrypted .nitrobin binary into a NitroBinModule.

    Performs two passes:
      1. Flat string extraction + classification by preceding context byte
         (preserves the original parse_nitrobin behavior for compatibility).
      2. Structural walk of 0x01 0x00 <tag> markers to extract annotation
         values (e.g. @name "Display Name") and port declarations by type.

    Args:
        data: Raw bytes of the decrypted .nitrobin file.

    Returns:
        NitroBinModule with extracted structure.
    """
    info = NitroBinModule(size=len(data))

    if len(data) < 10 or data[0] != 0x0A:
        return info

    info.import_count = struct.unpack_from(">I", data, 5)[0]

    # Pass 1: flat string classification (backward-compatible)
    strings = _extract_strings(data)
    info.total_strings = len(strings)

    for offset, s, pre in strings:
        if pre == 0x15:
            info.imports.append(s)
        elif pre == 0x02:
            info.fields.append(s)
        elif pre == 0x5D:
            info.references.append(s)
        elif pre == 0x06:
            info.functions.append(s)
        elif s.startswith("@"):
            info.annotations.append(s)

    info.annotations = list(set(info.annotations))
    info.references = list(set(info.references))

    # Module name: first non-import string that isn't an annotation
    for offset, s, pre in strings:
        if s not in info.imports and not s.startswith("@") and pre not in (0x02, 0x5D, 0x06, 0x15):
            info.module_name = s
            break

    # Pass 2: structural extraction of annotation values + port counts
    _extract_structural(data, info)

    return info


def parse_nitrobin_file(path: str | Path) -> NitroBinModule:
    """Parse a decrypted .nitrobin file from disk.

    Args:
        path: Path to a .dec file (decrypted .nitrobin).

    Returns:
        NitroBinModule with extracted structure.
    """
    data = Path(path).read_bytes()
    return parse_nitrobin(data)


def parse_all(directory: str | Path) -> dict[str, NitroBinModule]:
    """Parse all .dec files in a directory tree.

    Args:
        directory: Path to directory containing decrypted .nitrobin files.

    Returns:
        Dict mapping relative path to NitroBinModule.
    """
    directory = Path(directory)
    results = {}
    for f in sorted(directory.rglob("*.dec")):
        rel = str(f.relative_to(directory))
        results[rel] = parse_nitrobin_file(f)
    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python nitrobin_parser.py <file.dec|directory>")
        sys.exit(1)

    sys.stdout.reconfigure(encoding="utf-8")
    target = Path(sys.argv[1])

    if target.is_dir():
        modules = parse_all(target)
        print(f"Parsed {len(modules)} modules")
        named = sum(1 for m in modules.values() if m.display_name)
        print(f"  with @name display_name: {named}")
        for rel, mod in list(modules.items())[:10]:
            dn = f' display="{mod.display_name}"' if mod.display_name else ""
            print(f"  {rel}: {mod.module_name}{dn} "
                  f"(imports={len(mod.imports)}, ports={sum(mod.port_counts.values())})")
    else:
        mod = parse_nitrobin_file(target)
        print(f"Struct: {mod.module_name}")
        if mod.display_name:
            print(f"Display name: {mod.display_name}")
        if mod.description:
            print(f"Description: {mod.description}")
        if mod.icon:
            print(f"Icon: {mod.icon}")
        if mod.panel_display:
            print(f"Panel display: {mod.panel_display}")
        print(f"Annotations: {mod.annotation_values}")
        print(f"Port counts: {mod.port_counts}")
        print(f"Imports ({mod.import_count}): {mod.imports}")
        print(f"Fields ({len(mod.fields)}): {mod.fields[:20]}")
        print(f"Functions ({len(mod.functions)}): {mod.functions}")
        print(f"References: {mod.references[:20]}")
