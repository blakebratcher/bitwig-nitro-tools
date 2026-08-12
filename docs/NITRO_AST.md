# The Nitro AST

Nitro is Bitwig's in-house DSP programming language. Every native Bitwig audio
device and every Grid module is written in it. A compiled module is stored as a
serialized syntax tree (`.nitrobin`), and this document describes that tree: the
node model, the grammar tables that drive parsing, and the pipeline that turns
raw bytes into readable pseudo-source.

For the byte framing that carries the tree, see
[NITRO_BINARY_PROTOCOL.md](NITRO_BINARY_PROTOCOL.md).

## What the language looks like

Nitro is a systems language shaped for audio. The AST reflects that. It has:

- **A full expression and statement hierarchy**: arithmetic and logical
  expressions, calls, assignments, blocks, `if` / `switch`, loops, `break`,
  and so on.
- **A rich type system**: `f32`, `f64`, integer widths (`i16`/`i32`/`i64`
  and unsigned), complex (`c32`/`c64`), `bool`, `void`, plus `struct`, `enum`,
  vector, pointer, and array types.
- **A port model**: audio, value, and event ports, each with an input and
  output variant. This is how a module declares its I/O surface.
- **Lifecycle code blocks**: `process`, `pre_process`, `post_process`,
  `init`, `start`, `stop`, `reset`, `should_process`, `post_events`. The
  `process` block is the per-sample (or per-block) DSP.
- **Annotations**: `@name`, `@desc`, `@min`, `@max`, `@range`, `@inline`,
  `@parallel`, `@packed`, `@strictly_mono`, and more, attached to declarations
  and parameters.

When you decompile and pretty-print a module you get pseudo-source that mirrors
this structure: struct declarations with typed fields, port declarations, the
lifecycle blocks, and the expression trees inside them.

## The node model

Every node in the recovered tree is one small record:

```python
@dataclass
class AstNode:
    kind: str            # human name of the AST class, e.g. "CallExpression"
    tag: int             # the 2-byte class tag from the stream
    children: list       # positional children: AstNodes, strings, ints, floats, bools
    is_singleton: bool   # True for content-free type/marker nodes
```

Children are **positional**. Field names are not stored in the binary (they
would require tracing constructor argument names in Bitwig's bytecode), so a
node's children are recovered in serialization order. For reading DSP that
order is enough: an `AddExpression` has its left operand then its right; a
`CallExpression` has its callee then its argument list; an `IfStatement` has
its condition, then-block, and optional else. The pretty-printer knows the
shape of each class and renders accordingly.

Leaf values (`Int64`, `Double`, `True`/`False`, strings) appear directly in a
node's `children` list as Python `int`, `float`, `bool`, and `str`.

## The grammar tables

Two JSON tables ship in the package data directory
(`bitwig_nitro/data/`). They are the entire grammar; the parser is
data-driven from them.

**`nitro_ast_tags.json`**: the tag map. It joins each class's byte tag to its
human name and a category. There are 148 AST classes. Representative entries:

| Tag | Class |
|-----|-------|
| `0x6F` | `NitroFile` (the file root) |
| `0x5D` | `IdentifierExpression` |
| `0x3E` | `CallExpression` |
| `0x35` | `AssignStatement` |
| `0x30` | `BlockStatement` |
| `0x3A` | `IfStatement` |
| `0x3C` | `SwitchStatement` |

(The tags matter because an earlier third-party table mislabelled several of
them; for example `0x30` is a block, not an `if`, and the real `if` is `0x3A`.
The shipped table was rebuilt directly from Bitwig's dispatch and tag enums, so
it is authoritative for the build it was extracted from.)

**`nitro_ast_class_methods.json`**: the per-class deserialize sequence. For
each of the 148 classes it records how to read the node's content: the ordered
list of primitive reads and child reads, whether the class is a stateful node
(reads its fields through a constructor sequence, 133 of them) or a content-free
singleton (15 of them, mostly primitive type tokens recovered from the tag
alone), its super-class, and whether it carries a trailing nested-declaration
list. The parser follows this sequence for each node it meets. There are no
per-class hand-written routines anywhere in the reader.

### Regenerating the tables

Bitwig's obfuscation reshuffles the class and field names between releases,
but the AST class *names* and their byte tags are stable. If you upgrade Bitwig
and want to re-derive the tables against the new jar, use the
`nitro-build-ast-tables` command (see
[REGENERATING_THE_CORPUS.md](REGENERATING_THE_CORPUS.md)). It disassembles the
tag enum and dispatch registry from `bitwig.jar` and rebuilds both JSON files.

## The pipeline

Four modules take you from a decrypted module to readable source:

```
.nitrobin bytes
   │  decompile_nitrobin(data)        (nitrobin_decompiler)
   ▼
AstNode tree
   │  pretty_print(ast)               (nitro_pretty)
   ▼
Nitro pseudo-source (text)
```

with two more for the round trip and for edits:

```
AstNode tree
   │  serialize_nitrobin(root)        (nitrobin_writer)
   ▼
.nitrobin bytes   (byte-identical to input if unchanged)

AstNode tree
   │  find_constants / set_constant   (nitro_edit)
   ▼
mutated tree, same wire size
```

`decompile_nitrobin` and `parse_nitrobin` both read the binary; the decompiler
produces the analysis-friendly `AstNode` model used by the pretty-printer and
the editor, while the parser/serializer pair is the low-level round-trip core.

## Coverage and fidelity

Against the full shipped image, every module (all of them) parses to a clean
EOF and re-serializes byte-for-byte identically to its decrypted input. That
covers the whole range of module complexity, from tiny utility modules up to
the largest device sub-circuits.

Because the decompiler recovers full expression trees, the `process` blocks of
the interesting device-specific filters come through with their DSP intact.
State-variable filters render with their integrator coefficients and feedback
clipping; the Sallen-Key model renders as its low-pass/high-pass one-pole
cascade with the two nonlinearity modes; the Moog-style ladder renders as its
per-stage saturating one-pole chain with the flavor selector. You can read what
each stage actually computes, not just its name.

> This toolchain ships no decrypted Bitwig source. The examples above describe
> what you can recover *from your own installation* once you have supplied the
> keys. The repository contains the tools and these format notes, nothing
> extracted from Bitwig.

## The pretty-printer

`pretty_print(ast)` walks the tree and emits Nitro pseudo-source. It knows the
render shape of every AST class, so there are no unrendered node kinds: type
declarations, port declarations, annotations, lifecycle blocks, and every
expression and statement class have a printed form. The output is meant for
reading and diffing, not for feeding back into a compiler; the authoritative
representation is always the AST and the bytes.
