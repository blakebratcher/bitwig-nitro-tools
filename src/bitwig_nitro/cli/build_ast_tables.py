"""``nitro-build-ast-tables``: regenerate the packaged AST grammar tables.

Rebuilds the two JSON grammar tables shipped in the package data directory
from a Bitwig ``bitwig.jar``:

  * ``nitro_ast_tags.json``, the ``tag_byte -> AST class`` dispatch table,
    joined from the ``xSQ`` dispatch registrations and the ``Fzt`` tag enum.
  * ``nitro_ast_class_methods.json``, each AST class's deserialize signature
    (``<init>(AHs)`` constructor read sequence, or singleton).

**Prerequisite:** this is an advanced/optional tool. It shells out to the JDK
``javap`` disassembler, so you need ``javap`` on ``PATH`` and a readable
``bitwig.jar`` on disk. Nothing here decrypts or ships any Bitwig content; it
only reads bytecode structure.

Usage::

    nitro-build-ast-tables --jar /path/to/bitwig.jar
    nitro-build-ast-tables --jar /path/to/bitwig.jar --out-dir ./src/bitwig_nitro/data
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from bitwig_nitro import packaged_data_dir

_XSQ_CLASS = "com/bitwig/nitro/xSQ.class"
_FZT_CLASS = "com/bitwig/nitro/Fzt.class"

_TAGS_FILENAME = "nitro_ast_tags.json"
_METHODS_FILENAME = "nitro_ast_class_methods.json"


# --------------------------------------------------------------------------
# javap disassembly
# --------------------------------------------------------------------------


def _disassemble(jar_path: Path, internal: str, td: Path) -> str:
    """Extract ``internal`` from ``jar_path`` and run ``javap -p -c`` on it."""
    with zipfile.ZipFile(jar_path) as z:
        z.extract(internal, td)
    result = subprocess.run(
        ["javap", "-p", "-c", str(td / internal)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


# --------------------------------------------------------------------------
# nitro_ast_tags.json, tag_byte -> AST class
# --------------------------------------------------------------------------

# javap operand patterns: iconst_N (0-5), iconst_m1, bipush N, sipush N, ldc #X
_INT_OP = re.compile(
    r"^\s*\d+:\s+"
    r"(?:iconst_(m1|[0-5])|bipush\s+(-?\d+)|sipush\s+(-?\d+)|ldc\s+#\d+\s+//\s+int\s+(-?\d+))"
    r"\s*$"
)


def _decode_int(line: str) -> int | None:
    m = _INT_OP.match(line)
    if not m:
        return None
    if m.group(1) is not None:
        return -1 if m.group(1) == "m1" else int(m.group(1))
    if m.group(2) is not None:
        return int(m.group(2))
    if m.group(3) is not None:
        return int(m.group(3))
    return int(m.group(4))


def parse_xsq(text: str) -> list[tuple[str, str]]:
    """Return ``(ast_class_name, fzt_field_name)`` pairs registered by ``xSQ``."""
    lines = text.splitlines()
    pairs: list[tuple[str, str]] = []

    ldc_class = re.compile(
        r"^\s*\d+:\s+ldc\s+#\d+\s+//\s+class\s+com/bitwig/nitro/(\w+)\s*$"
    )
    getstatic_fzt = re.compile(
        r"^\s*\d+:\s+getstatic\s+#\d+\s+//\s+Field\s+"
        r"com/bitwig/nitro/Fzt\.(\w+):Lcom/bitwig/nitro/Fzt;\s*$"
    )
    invoke_register = re.compile(
        r"^\s*\d+:\s+invokestatic\s+#\d+\s+//\s+Method\s+"
        r"mCB:\(Ljava/lang/Class;Lcom/bitwig/nitro/Fzt;\)V\s*$"
    )

    for i in range(len(lines) - 2):
        m1 = ldc_class.match(lines[i])
        m2 = getstatic_fzt.match(lines[i + 1])
        m3 = invoke_register.match(lines[i + 2])
        if m1 and m2 and m3:
            pairs.append((m1.group(1), m2.group(1)))
    return pairs


def parse_fzt(text: str) -> dict[str, tuple[str, int, int]]:
    """Return ``fzt_field_name -> (human_name, ordinal, tag_int)``."""
    lines = text.splitlines()
    out: dict[str, tuple[str, int, int]] = {}

    new_fzt = re.compile(r"^\s*\d+:\s+new\s+#\d+\s+//\s+class\s+com/bitwig/nitro/Fzt\s*$")
    ldc_str = re.compile(r"^\s*\d+:\s+ldc\s+#\d+\s+//\s+String\s+(\S+)\s*$")
    invoke_init = re.compile(
        r'^\s*\d+:\s+invokespecial\s+#\d+\s+//\s+Method\s+'
        r'"<init>":\(Ljava/lang/String;II\)V\s*$'
    )
    putstatic_fzt = re.compile(
        r"^\s*\d+:\s+putstatic\s+#\d+\s+//\s+Field\s+(\w+):Lcom/bitwig/nitro/Fzt;\s*$"
    )

    i = 0
    while i < len(lines) - 6:
        if not new_fzt.match(lines[i]):
            i += 1
            continue
        m_str = ldc_str.match(lines[i + 2])
        if not m_str:
            i += 1
            continue
        ord_val = _decode_int(lines[i + 3])
        tag_val = _decode_int(lines[i + 4])
        if ord_val is None or tag_val is None:
            i += 1
            continue
        if not invoke_init.match(lines[i + 5]):
            i += 1
            continue
        m_field = putstatic_fzt.match(lines[i + 6])
        if not m_field:
            i += 1
            continue
        out[m_field.group(1)] = (m_str.group(1), ord_val, tag_val)
        i += 7
    return out


def _categorize(class_name: str) -> str:
    if class_name.endswith("Expression"):
        return "expression"
    if class_name.endswith("Statement"):
        return "statement"
    if class_name.endswith("Declaration"):
        return "declaration"
    if class_name.endswith("Annotation"):
        return "annotation"
    if class_name.endswith("Specifier"):
        return "specifier"
    if class_name.endswith("Type") or class_name.endswith("PortType"):
        return "type"
    return "other"


def build_tags(jar_path: Path, td: Path) -> dict:
    """Build the ``nitro_ast_tags.json`` document from ``xSQ`` + ``Fzt``."""
    xsq_pairs = parse_xsq(_disassemble(jar_path, _XSQ_CLASS, td))
    fzt_map = parse_fzt(_disassemble(jar_path, _FZT_CLASS, td))

    print(f"  xSQ: {len(xsq_pairs)} (Class, Fzt-field) pairs")
    print(f"  Fzt: {len(fzt_map)} enum values")

    by_tag: dict[int, dict] = {}
    seen_fields: set[str] = set()

    for ast_class, fzt_field in xsq_pairs:
        seen_fields.add(fzt_field)
        if fzt_field not in fzt_map:
            print(f"  WARN: xSQ references Fzt.{fzt_field}, absent from Fzt enum")
            continue
        human_name, ordinal, tag_int = fzt_map[fzt_field]
        by_tag[tag_int] = {
            "tag_byte": f"0x{tag_int:02X}",
            "tag_int": tag_int,
            "fzt_field": fzt_field,
            "fzt_human_name": human_name,
            "fzt_ordinal": ordinal,
            "ast_class": ast_class,
            "java_path": f"com/bitwig/nitro/{ast_class}.class",
            "ast_category": _categorize(ast_class),
            "names_consistent": human_name == ast_class,
        }

    unregistered: list[dict] = []
    for fzt_field, (human_name, ordinal, tag_int) in fzt_map.items():
        if fzt_field in seen_fields:
            continue
        unregistered.append({
            "tag_byte": f"0x{tag_int:02X}",
            "tag_int": tag_int,
            "fzt_field": fzt_field,
            "fzt_human_name": human_name,
            "fzt_ordinal": ordinal,
            "ast_class": None,
            "ast_category": _categorize(human_name),
            "note": "in Fzt enum but not registered in xSQ dispatch table",
        })

    registered = {f"0x{t:02X}": e for t, e in sorted(by_tag.items())}
    return {
        "_schema": {
            "tag_byte": "hex string, e.g. 0x35",
            "tag_int": "decimal int",
            "fzt_field": "obfuscated Fzt enum field name (used by xSQ dispatch)",
            "fzt_human_name": "human-readable AST class name from Fzt constructor",
            "fzt_ordinal": "Fzt enum ordinal (creation order; not the dispatch tag)",
            "ast_class": "AST class registered in xSQ, should match fzt_human_name",
            "java_path": "path inside bitwig.jar",
            "ast_category": "expression / statement / declaration / annotation "
            "/ specifier / type / other",
            "names_consistent": "Fzt human_name matches xSQ class name",
        },
        "_source": {
            "jar": str(jar_path),
            "xsq_class": _XSQ_CLASS,
            "fzt_class": _FZT_CLASS,
            "tool": "nitro-build-ast-tables",
        },
        "_stats": {
            "registered_in_xsq": len(by_tag),
            "fzt_enum_values_total": len(fzt_map),
            "fzt_unregistered_in_xsq": len(unregistered),
            "name_mismatches": sum(1 for e in by_tag.values() if not e["names_consistent"]),
        },
        "registered": registered,
        "unregistered_in_xsq": unregistered,
    }


# --------------------------------------------------------------------------
# nitro_ast_class_methods.json, per-class deserialize signature
# --------------------------------------------------------------------------

# AHs primitive reader API recognised in constructor bytecode.
AHS_API = {
    "mCB:()Lcom/bitwig/nitro/BxQ;": ("read_node", "BxQ"),
    "vE:()Lcom/bitwig/nitro/BxQ;": ("read_node_inner", "BxQ"),
    "cL1:()J": ("read_long", "long"),
    "agn:()D": ("read_double", "double"),
    "jUU:()Z": ("read_bool", "boolean"),
    "QOE:()Ljava/lang/String;": ("read_string", "String"),
    "KJT:()Ljava/lang/String;": ("read_string_v2", "String"),
    "it:()[Ljava/lang/String;": ("read_string_array", "String[]"),
    "fVP:()Ljava/util/List;": ("read_list", "List"),
    "zc2:()Ljava/util/List;": ("read_list_v2", "List"),
    "mCB:(S)Lcom/bitwig/nitro/BxQ;": ("dispatch_tag", "BxQ"),
}

_INVOKEVIRTUAL_AHS = re.compile(
    r"^\s*\d+:\s+invokevirtual\s+#\d+\s+//\s+Method\s+com/bitwig/nitro/AHs\.(\S+)\s*$"
)
_INVOKESTATIC_T3A = re.compile(
    r"^\s*\d+:\s+invokestatic\s+#\d+\s+//\s+Method\s+com/bitwig/base/io/t3a\.(\S+)\s*$"
)
_CHECKCAST = re.compile(r"^\s*\d+:\s+checkcast\s+#\d+\s+//\s+class\s+(\S+)\s*$")
_PUTFIELD = re.compile(r"^\s*\d+:\s+putfield\s+#\d+\s+//\s+Field\s+(\w+):(\S+)\s*$")
_INVOKESPECIAL_INIT = re.compile(
    r'^\s*\d+:\s+invokespecial\s+#\d+\s+//\s+Method\s+'
    r'(?:"<init>"|com/bitwig/nitro/(\w+)\.(?:"<init>"|<init>))\s*:\s*\((\S*)\)V\s*$'
)


def _find_constructor_block(disasm: str, class_name: str) -> tuple[list[str], str | None]:
    """Find the ``<ClassName>(com.bitwig.nitro.AHs)`` constructor body + superclass."""
    lines = disasm.splitlines()
    super_class: str | None = None
    in_ctor = False
    body: list[str] = []
    sig_pattern = re.compile(
        rf"^\s*public\s+com\.bitwig\.nitro\.{re.escape(class_name)}"
        r"\(com\.bitwig\.nitro\.AHs\);\s*$"
    )
    pkg_sig_pattern = re.compile(
        rf"^\s*com\.bitwig\.nitro\.{re.escape(class_name)}"
        r"\(com\.bitwig\.nitro\.AHs\);\s*$"
    )
    extends_pattern = re.compile(
        r"^\s*(?:public\s+|final\s+|abstract\s+)*(?:final\s+)?(?:class|abstract\s+class)\s+"
        rf"com\.bitwig\.nitro\.{re.escape(class_name)}\s+extends\s+(\S+)"
    )
    extends_short = re.compile(
        r"^\s*(?:public\s+|final\s+|abstract\s+)*(?:final\s+)?(?:class|abstract\s+class)\s+"
        rf"com\.bitwig\.nitro\.{re.escape(class_name)}(?:\s|\{{|$)"
    )
    for ln in lines:
        if super_class is None:
            m = extends_pattern.match(ln)
            if m:
                super_class = m.group(1)
            elif extends_short.match(ln):
                super_class = "java.lang.Object"
        if sig_pattern.match(ln) or pkg_sig_pattern.match(ln):
            in_ctor = True
            continue
        if in_ctor:
            if ln.startswith("  ") and ln.strip() == "":
                if body and any(l.strip() for l in body):
                    break
                continue
            if not ln.startswith(" "):
                break
            if (
                re.match(
                    r"^\s*(public|private|protected|static|final|com\.|java\.|"
                    r"void|int|long|double|boolean|byte|short|float)",
                    ln,
                )
                and "<init>" not in ln
                and "Code:" not in ln
                and not re.match(r"^\s+\d+:", ln)
            ):
                break
            body.append(ln)
    return body, super_class


def _has_singleton_factory(disasm: str, class_name: str) -> bool:
    pattern = re.compile(
        rf"^\s*public\s+static\s+com\.bitwig\.nitro\.{re.escape(class_name)}"
        r"\s+getInstance\(\);\s*$"
    )
    return any(pattern.match(ln) for ln in disasm.splitlines())


def _parse_ctor_body(body: list[str]) -> list[dict]:
    seq: list[dict] = []
    last_call: dict | None = None
    for ln in body:
        m = _INVOKEVIRTUAL_AHS.match(ln)
        if m:
            sig = m.group(1)
            op_info = AHS_API.get(sig)
            if op_info:
                op_name, ret_type = op_info
            else:
                op_name, ret_type = ("ahs_" + sig.split(":")[0], "?")
            entry = {"op": op_name, "ahs_method": sig, "ret_type": ret_type}
            seq.append(entry)
            last_call = entry
            continue
        m = _INVOKESTATIC_T3A.match(ln)
        if m:
            sig = m.group(1)
            entry = {"op": "t3a_" + sig.split(":")[0], "t3a_method": sig}
            seq.append(entry)
            last_call = entry
            continue
        m = _CHECKCAST.match(ln)
        if m:
            cls = m.group(1).replace("/", ".")
            if last_call is not None and last_call.get("ret_type") in ("BxQ", "?"):
                last_call["expected_type"] = cls
            continue
        m = _PUTFIELD.match(ln)
        if m:
            fname, fdesc = m.group(1), m.group(2)
            if last_call is not None:
                last_call["stored_to"] = {"field": fname, "type": fdesc}
            else:
                seq.append({"op": "store_field", "field": fname, "type": fdesc})
            continue
        m = _INVOKESPECIAL_INIT.match(ln)
        if m:
            seq.append({"op": "super_init", "args_descriptor": m.group(2)})
            continue
    return seq


def build_class_methods(jar_path: Path, tags_doc: dict, td: Path) -> dict:
    """Build the ``nitro_ast_class_methods.json`` document from the tag table."""
    classes = sorted(
        {e["ast_class"] for e in tags_doc["registered"].values() if e.get("ast_class")}
    )
    print(f"  inspecting {len(classes)} AST classes ...")

    out_classes: dict[str, dict] = {}
    singleton_count = ctor_count = unknown_count = total_ops = 0

    with zipfile.ZipFile(jar_path) as jar:
        for ast_class in classes:
            internal = f"com/bitwig/nitro/{ast_class}.class"
            try:
                jar.extract(internal, td)
                disasm = subprocess.run(
                    ["javap", "-p", "-c", str(td / internal)],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout
            except Exception as exc:  # missing class, javap failure, etc.
                out_classes[ast_class] = {"error": str(exc)}
                unknown_count += 1
                continue

            body, super_class = _find_constructor_block(disasm, ast_class)
            is_singleton = _has_singleton_factory(disasm, ast_class)
            if body:
                seq = _parse_ctor_body(body)
                out_classes[ast_class] = {
                    "deserialize_via": "constructor_AHs",
                    "super_class": super_class,
                    "read_sequence": seq,
                    "is_also_singleton": is_singleton,
                    "ctor_ops_count": len(seq),
                }
                ctor_count += 1
                total_ops += len(seq)
            elif is_singleton:
                out_classes[ast_class] = {
                    "deserialize_via": "singleton_getInstance",
                    "super_class": super_class,
                    "read_sequence": [],
                    "is_also_singleton": True,
                }
                singleton_count += 1
            else:
                out_classes[ast_class] = {
                    "deserialize_via": "unknown",
                    "super_class": super_class,
                    "read_sequence": [],
                }
                unknown_count += 1

    tag_for_class = {
        e["ast_class"]: tag_hex
        for tag_hex, e in tags_doc["registered"].items()
        if e.get("ast_class")
    }
    for c, info in out_classes.items():
        info["tag_byte"] = tag_for_class.get(c)

    return {
        "_schema": {
            "tag_byte": "from nitro_ast_tags.json",
            "deserialize_via": "constructor_AHs | singleton_getInstance | unknown",
            "super_class": "fully qualified Java superclass",
            "read_sequence": "ordered list of read operations",
            "is_also_singleton": "constructor classes that ALSO expose getInstance()",
            "ctor_ops_count": "number of operations in the constructor body",
        },
        "_source": {
            "jar": str(jar_path),
            "tool": "nitro-build-ast-tables",
            "ahs_api_assumption": {k: list(v) for k, v in AHS_API.items()},
        },
        "_stats": {
            "total_classes_inspected": len(classes),
            "constructor_ahs": ctor_count,
            "singleton_get_instance": singleton_count,
            "unknown": unknown_count,
            "total_read_ops": total_ops,
            "avg_ops_per_ctor": round(total_ops / ctor_count, 2) if ctor_count else 0,
        },
        "classes": out_classes,
    }


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nitro-build-ast-tables",
        description="Regenerate the packaged AST grammar tables "
        "(nitro_ast_tags.json + nitro_ast_class_methods.json) from a bitwig.jar. "
        "Requires the JDK 'javap' on PATH.",
    )
    parser.add_argument(
        "--jar",
        required=True,
        help="path to bitwig.jar (e.g. /opt/bitwig-studio/bin/bitwig.jar)",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="output directory for the two JSON tables "
        "(default: the packaged bitwig_nitro/data directory)",
    )
    args = parser.parse_args(argv)

    if shutil.which("javap") is None:
        print(
            "error: 'javap' not found on PATH. Install a JDK and retry "
            "(this tool disassembles bitwig.jar bytecode).",
            file=sys.stderr,
        )
        return 2

    jar_path = Path(args.jar)
    if not jar_path.is_file():
        print(f"error: bitwig.jar not found: {jar_path}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else packaged_data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td_str:
        td = Path(td_str)
        print(f"disassembling AST grammar from {jar_path} ...")
        tags_doc = build_tags(jar_path, td)
        methods_doc = build_class_methods(jar_path, tags_doc, td)

    tags_path = out_dir / _TAGS_FILENAME
    methods_path = out_dir / _METHODS_FILENAME
    tags_path.write_text(json.dumps(tags_doc, indent=2))
    methods_path.write_text(json.dumps(methods_doc, indent=2))

    print(f"\nwrote {tags_path}")
    print(f"  registered tags : {tags_doc['_stats']['registered_in_xsq']}")
    print(f"wrote {methods_path}")
    for k, v in methods_doc["_stats"].items():
        print(f"  {k:26s} {v}")

    if tags_doc["_stats"]["registered_in_xsq"] == 0:
        print(
            "\nwarning: zero tags registered, the jar's obfuscated class/field "
            "names likely differ from the expected xSQ/Fzt layout.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
