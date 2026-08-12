"""Builders for the synthetic, key-free test fixtures.

These construct a small but real Nitro module AST purely through the public
``bitwig_nitro`` DSL, so the whole test suite runs fully offline with zero
Bitwig content and no cipher keys.

The canonical module is ``synthetic_gain``: a two-port value component with a
single tunable float literal (the output port's default value). It exercises
the full author -> serialize -> decompile -> pretty-print -> edit pipeline and
carries exactly one ``FloatValueExpression`` constant, which the edit tests
address by path.
"""
from __future__ import annotations

from pathlib import Path

from bitwig_nitro import nitro_builder as nb
from bitwig_nitro.nitrobin_writer import serialize_nitrobin

FIXTURE_DIR = Path(__file__).resolve().parent

#: File name of the committed, byte-stable synthetic module.
SYNTHETIC_MODULE_FILENAME = "synthetic_module.nitrobin"

#: The component name embedded in the synthetic module.
SYNTHETIC_MODULE_NAME = "synthetic_gain"

#: The float literal the fixture ships with (output port default value).
SYNTHETIC_DEFAULT_VALUE = 0.5


def build_synthetic_module():
    """Return a freshly built ``synthetic_gain`` AST (``NitroFile`` root).

    A minimal value component with:
      * an ``f32`` input port ``in`` (no default), and
      * an ``f32`` output port ``out`` whose default value is the single
        tunable float literal in the module.

    Deterministic: two calls produce byte-identical serializations.
    """
    return nb.nitro_file(
        [
            nb.component_declaration(
                SYNTHETIC_MODULE_NAME,
                [
                    nb.port_declaration(
                        nb.input_value_port_type(nb.f32()),
                        "in",
                        None,
                        "",
                        None,
                        None,
                    ),
                    nb.port_declaration(
                        nb.output_value_port_type(nb.f32()),
                        "out",
                        None,
                        "",
                        None,
                        nb.float_lit(SYNTHETIC_DEFAULT_VALUE),
                    ),
                ],
            )
        ]
    )


def build_synthetic_module_bytes() -> bytes:
    """Serialize :func:`build_synthetic_module` to ``.nitrobin`` bytes."""
    return serialize_nitrobin(build_synthetic_module())


def synthetic_module_path() -> Path:
    """Path to the committed synthetic ``.nitrobin`` fixture."""
    return FIXTURE_DIR / SYNTHETIC_MODULE_FILENAME


def read_synthetic_module_bytes() -> bytes:
    """Read the committed synthetic ``.nitrobin`` fixture from disk."""
    return synthetic_module_path().read_bytes()


def _write_fixture() -> Path:
    """(Re)generate the committed fixture from the builder. Used by __main__."""
    path = synthetic_module_path()
    path.write_bytes(build_synthetic_module_bytes())
    return path


if __name__ == "__main__":  # pragma: no cover - regeneration helper
    p = _write_fixture()
    print(f"wrote {p} ({p.stat().st_size} bytes)")
