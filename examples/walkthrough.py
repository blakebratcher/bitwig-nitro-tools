#!/usr/bin/env python3
"""End-to-end, fully-offline walkthrough of bitwig-nitro-tools.

Runs the whole author -> serialize -> decompile -> pretty-print -> edit ->
re-serialize pipeline against a synthetic module. It uses **no** Bitwig
content and **no** cipher keys, so it works anywhere the package is installed.

    python examples/walkthrough.py

The committed ``examples/synthetic_module.nitrobin`` is exactly the bytes this
script's ``build()`` produces.
"""
from __future__ import annotations

from pathlib import Path

from bitwig_nitro import nitro_builder as nb
from bitwig_nitro import (
    decompile_nitrobin,
    find_constants_in_bytes,
    mutate_constant_bytes,
    pretty_print,
    serialize_nitrobin,
)

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "synthetic_module.nitrobin"


def build():
    """A minimal two-port value component with one tunable float default."""
    return nb.nitro_file(
        [
            nb.component_declaration(
                "synthetic_gain",
                [
                    nb.port_declaration(
                        nb.input_value_port_type(nb.f32()), "in", None, "", None, None
                    ),
                    nb.port_declaration(
                        nb.output_value_port_type(nb.f32()),
                        "out",
                        None,
                        "",
                        None,
                        nb.float_lit(0.5),
                    ),
                ],
            )
        ]
    )


def main() -> None:
    # 1. Author + serialize.
    data = serialize_nitrobin(build())
    print(f"# built synthetic_gain -> {len(data)} bytes")

    # 2. Decompile + pretty-print.
    ast = decompile_nitrobin(data)
    print("\n# pretty_print():\n")
    print(pretty_print(ast), end="")

    # 3. Address the one float constant and same-size mutate it.
    ref = next(
        c
        for c in find_constants_in_bytes(data)
        if c.node_kind == "FloatValueExpression"
    )
    print(f"\n# constant at path {ref.path_str}: {ref.value}")
    mutated = mutate_constant_bytes(data, ref.path, 0.25)
    print(f"# mutated 0.5 -> 0.25; same size: {len(mutated) == len(data)}")

    # The committed fixture is exactly build()'s bytes.
    if MODULE_PATH.exists():
        same = MODULE_PATH.read_bytes() == data
        print(f"# matches committed {MODULE_PATH.name}: {same}")


if __name__ == "__main__":
    main()
