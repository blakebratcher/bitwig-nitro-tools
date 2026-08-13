"""bitwig-nitro-tools: offline tools for Bitwig's Nitro DSP binary format.

Standard-library-only tools and format specs for reading, decompiling,
editing, and repacking Bitwig's Nitro DSP archive and encoding-0004 files.

This package ships **no** cipher keys or decrypted Bitwig content. Decryption
requires keys you extract from your own Bitwig installation, see
``docs/KEY_EXTRACTION.md`` and :mod:`bitwig_nitro.keys`.
"""
from __future__ import annotations

__version__ = "0.2.0"

# dag_cipher
from .dag_cipher import dag_decrypt, decrypt_0004, read_encrypted_btwg

# keys
from .keys import (
    MissingKeyError,
    resolve_dag_key,
    resolve_nitro_image_key,
    write_keys_file,
)

# keydump
from .keydump import (
    DEFAULT_DUMP_PATH,
    KeyDumpResult,
    Transform,
    default_dump_path,
    parse_dump,
    select_image_key,
    validate_image_key,
)

# paths
from .paths import (
    bitwig_install_roots,
    keys_search_paths,
    local_output_dir,
    nitro_image_install_path,
    packaged_data_dir,
)

# nitro_image
from .nitro_image import (
    NitroImage,
    NitroImageError,
    decrypt_entry,
    encrypt_entry,
    nitro_image_path,
    nitro_key,
    read_entry,
    read_image,
    write_image,
)

# nitrobin_decompiler
from .nitrobin_decompiler import (
    AstNode,
    NitroParseError,
    decompile_nitrobin,
    decompile_nitrobin_file,
)

# nitro_pretty
from .nitro_pretty import pretty_print

# nitrobin_parser
from .nitrobin_parser import (
    NitroBinModule,
    parse_all,
    parse_nitrobin,
    parse_nitrobin_file,
)

# nitrobin_writer
from .nitrobin_writer import (
    NitroSerializeError,
    serialize_nitrobin,
    serialize_nitrobin_file,
)

# nitro_edit
from .nitro_edit import (
    ConstantRef,
    NitroEditError,
    assert_same_size,
    decompile_bytes,
    find_constants,
    find_constants_in_bytes,
    format_path,
    get_constant,
    mutate_constant_bytes,
    parse_path,
    set_constant,
)

# nitro_builder
from .nitro_builder import (
    add,
    block,
    bool_lit,
    builder_doc,
    div,
    float_lit,
    id_,
    int_lit,
    list_builders,
    mul,
    str_lit,
    sub,
)

__all__ = [
    "__version__",
    # dag_cipher
    "dag_decrypt",
    "decrypt_0004",
    "read_encrypted_btwg",
    # keys
    "MissingKeyError",
    "resolve_dag_key",
    "resolve_nitro_image_key",
    "write_keys_file",
    # keydump
    "DEFAULT_DUMP_PATH",
    "default_dump_path",
    "parse_dump",
    "select_image_key",
    "validate_image_key",
    "KeyDumpResult",
    "Transform",
    # paths
    "packaged_data_dir",
    "bitwig_install_roots",
    "nitro_image_install_path",
    "keys_search_paths",
    "local_output_dir",
    # nitro_image
    "read_image",
    "read_entry",
    "write_image",
    "decrypt_entry",
    "encrypt_entry",
    "nitro_key",
    "nitro_image_path",
    "NitroImage",
    "NitroImageError",
    # nitrobin_decompiler
    "decompile_nitrobin",
    "decompile_nitrobin_file",
    "AstNode",
    "NitroParseError",
    # nitro_pretty
    "pretty_print",
    # nitrobin_parser
    "parse_nitrobin",
    "parse_nitrobin_file",
    "parse_all",
    "NitroBinModule",
    # nitrobin_writer
    "serialize_nitrobin",
    "serialize_nitrobin_file",
    "NitroSerializeError",
    # nitro_edit
    "find_constants",
    "find_constants_in_bytes",
    "get_constant",
    "set_constant",
    "mutate_constant_bytes",
    "decompile_bytes",
    "ConstantRef",
    "NitroEditError",
    "format_path",
    "parse_path",
    "assert_same_size",
    # nitro_builder
    "int_lit",
    "float_lit",
    "bool_lit",
    "str_lit",
    "id_",
    "block",
    "add",
    "sub",
    "mul",
    "div",
    "list_builders",
    "builder_doc",
]
