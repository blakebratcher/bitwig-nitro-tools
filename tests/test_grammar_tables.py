"""Packaged grammar tables load from the wheel's data directory.

Proves the ``bitwig_nitro/data/*.json`` package-data wiring: the decompiler
and builder both resolve their tag / class-method tables via
``packaged_data_dir()`` (importlib.resources), so this must work from a source
checkout, an editable install, or an installed wheel alike.
"""
from __future__ import annotations

import json

from bitwig_nitro import packaged_data_dir
from bitwig_nitro import nitro_builder as nb
from bitwig_nitro.nitrobin_decompiler import (
    _CLASS_SINGLETON,
    _CLASS_TO_SEQ,
    _TAG_TO_CLASS,
    _load_specs,
)


def test_packaged_data_dir_contains_grammar() -> None:
    data_dir = packaged_data_dir()
    assert (data_dir / "nitro_ast_tags.json").is_file()
    assert (data_dir / "nitro_ast_class_methods.json").is_file()


def test_grammar_json_is_wellformed() -> None:
    data_dir = packaged_data_dir()
    tags = json.loads((data_dir / "nitro_ast_tags.json").read_text())
    methods = json.loads((data_dir / "nitro_ast_class_methods.json").read_text())
    assert "registered" in tags
    assert "classes" in methods
    assert len(tags["registered"]) > 0
    assert len(methods["classes"]) > 0


def test_load_specs_populates_dispatch_tables() -> None:
    tag_to_class, class_to_seq, class_singleton, declarations = _load_specs()
    # Module-level tables are the same content _load_specs returns.
    assert tag_to_class == _TAG_TO_CLASS
    assert class_to_seq == _CLASS_TO_SEQ
    assert class_singleton == _CLASS_SINGLETON

    # Non-empty and internally consistent.
    assert len(tag_to_class) > 100
    assert len(class_to_seq) == len(tag_to_class)
    # Every tag maps to a class that has a deserialize spec.
    for cls in tag_to_class.values():
        assert cls in class_to_seq
    # NitroFile, the root class every module uses, must be present.
    assert "NitroFile" in class_to_seq
    # The declaration set is a non-trivial subset.
    assert declarations
    assert declarations.issubset(set(class_to_seq))


def test_builders_derived_from_same_tables() -> None:
    """nitro_builder exposes a builder for every tagged AST class."""
    builders = nb.list_builders()
    assert len(builders) == len(_TAG_TO_CLASS)
    # A couple of well-known builders exist and self-document.
    assert "component_declaration" in builders
    assert "nitro_file" in builders
    assert nb.builder_doc("component_declaration")
