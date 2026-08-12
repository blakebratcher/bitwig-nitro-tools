"""The core proof: author -> serialize -> decompile -> pretty -> edit.

Builds a synthetic module with the public DSL, serializes it, decompiles it
back, pretty-prints it, then addresses and same-width-mutates its one float
constant. Nothing here touches Bitwig content or any cipher key.
"""
from __future__ import annotations

from fixtures import builders

from bitwig_nitro import (
    assert_same_size,
    decompile_nitrobin,
    find_constants,
    find_constants_in_bytes,
    get_constant,
    mutate_constant_bytes,
    pretty_print,
    serialize_nitrobin,
    set_constant,
)


def test_build_serialize_is_deterministic(synthetic_bytes) -> None:
    """Two independent builds serialize to identical bytes."""
    again = builders.build_synthetic_module_bytes()
    assert synthetic_bytes == again
    assert len(synthetic_bytes) > 0


def test_committed_fixture_matches_builder(synthetic_bytes) -> None:
    """The committed .nitrobin fixture is exactly what the builder emits.

    Guards against silent drift between the checked-in binary and the grammar
    tables / builder that produced it.
    """
    assert builders.read_synthetic_module_bytes() == synthetic_bytes


def test_serialize_then_decompile_roundtrip(synthetic_ast) -> None:
    """serialize_nitrobin -> decompile_nitrobin recovers the module shape."""
    data = serialize_nitrobin(synthetic_ast)
    root = decompile_nitrobin(data)

    assert root.kind == "NitroFile"
    # NitroFile's single child is the declaration list; element 0 is the component.
    component = root.children[0][0]
    assert component.kind == "ComponentDeclaration"
    assert component.children[0] == builders.SYNTHETIC_MODULE_NAME


def test_reserialize_is_byte_stable(synthetic_bytes) -> None:
    """Decompiling then re-serializing reproduces the exact bytes."""
    root = decompile_nitrobin(synthetic_bytes)
    assert serialize_nitrobin(root) == synthetic_bytes


def test_pretty_print_is_nonempty_and_stable(synthetic_bytes) -> None:
    """pretty_print yields deterministic, non-empty pseudo-source."""
    root = decompile_nitrobin(synthetic_bytes)
    text_a = pretty_print(root)
    text_b = pretty_print(decompile_nitrobin(synthetic_bytes))

    assert text_a.strip(), "pretty_print produced empty output"
    assert text_a == text_b, "pretty_print is not stable across runs"
    assert f"struct {builders.SYNTHETIC_MODULE_NAME}" in text_a


def test_find_constants_locates_the_float(synthetic_bytes) -> None:
    """The module carries exactly one FloatValueExpression constant."""
    floats = [
        c
        for c in find_constants_in_bytes(synthetic_bytes)
        if c.node_kind == "FloatValueExpression"
    ]
    assert len(floats) == 1
    assert floats[0].value == builders.SYNTHETIC_DEFAULT_VALUE
    # The path is stable and round-trips through the string form.
    from bitwig_nitro import format_path, parse_path

    assert parse_path(format_path(floats[0].path)) == floats[0].path


def test_set_constant_in_place_then_same_size(synthetic_ast, synthetic_bytes) -> None:
    """set_constant to a new same-width value keeps the serialized size."""
    floats = [
        c for c in find_constants(synthetic_ast) if c.node_kind == "FloatValueExpression"
    ]
    assert len(floats) == 1
    ref = floats[0]

    before = serialize_nitrobin(synthetic_ast)
    previous = set_constant(synthetic_ast, ref.path, 0.25)
    assert previous == builders.SYNTHETIC_DEFAULT_VALUE
    assert get_constant(synthetic_ast, ref.path) == 0.25

    after = serialize_nitrobin(synthetic_ast)
    assert_same_size(before, after)  # must not raise

    # The new value is observable when we decompile the edited bytes.
    reread = [
        c
        for c in find_constants_in_bytes(after)
        if c.node_kind == "FloatValueExpression"
    ]
    assert reread[0].value == 0.25


def test_mutate_constant_bytes_end_to_end(synthetic_bytes) -> None:
    """The one-call byte mutator returns same-size bytes with the new value."""
    ref = next(
        c
        for c in find_constants_in_bytes(synthetic_bytes)
        if c.node_kind == "FloatValueExpression"
    )
    mutated = mutate_constant_bytes(synthetic_bytes, ref.path, 0.125)

    assert len(mutated) == len(synthetic_bytes)
    assert mutated != synthetic_bytes  # a value byte actually changed

    reread = next(
        c
        for c in find_constants_in_bytes(mutated)
        if c.node_kind == "FloatValueExpression"
    )
    assert reread.value == 0.125
