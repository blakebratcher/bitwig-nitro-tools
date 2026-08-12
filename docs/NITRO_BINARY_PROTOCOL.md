# The `.nitrobin` binary protocol

`.nitrobin` is the on-disk form of a compiled Nitro DSP module: a serialized
abstract syntax tree (AST), not machine code. Each module in Bitwig's
`nitro-image` archive is one `.nitrobin` payload. This document describes the
byte-level container so you can read one without any further reverse
engineering.

The format was recovered from Bitwig's own serializer bytecode. It is enough
to write a faithful parser, and `bitwig_nitro` ships one:
`parse_nitrobin` / `decompile_nitrobin` read it, `serialize_nitrobin` writes
it back.

The corresponding AST grammar tables live in the package data directory
(`bitwig_nitro/data/nitro_ast_tags.json` and `nitro_ast_class_methods.json`);
this file covers only the framing that wraps them. For the node model itself,
see [NITRO_AST.md](NITRO_AST.md).

## The model in one sentence

A `.nitrobin` file is a single tagged root node (`NitroFile`) whose children
are a list of imports followed by a list of top-level declarations, encoded as
a depth-first stream of framed nodes, node-lists, strings, and scalars.

There are no length prefixes on the file as a whole and no offset table. You
parse it by walking frame markers.

## Frame markers

Every structural element in the stream begins with a one-byte marker. This is
the complete set:

| Byte | Name | Meaning |
|------|------|---------|
| `0x01` | Node | start of an AST node; followed by a 2-byte big-endian tag, then the node's content |
| `0x02` | NodeEnd | end of an AST node |
| `0x03` | Null | a nullable child that is absent (appears where a `0x01` node would otherwise be) |
| `0x04` | NodeList | start of a `List<Node>`; followed by a u32 count, then the elements |
| `0x05` | NodeListEnd | end of a NodeList |
| `0x06` | StringDefinition | an inline string: u32 length + UTF-8 bytes; also appended to the file's string table |
| `0x07` | StringReference | a back-reference to an earlier interned string: u32 index into the string table |
| `0x08` | StringArray | a `String[]`: u32 count, then that many strings |
| `0x09` | True | inline boolean true |
| `0x0A` | False | inline boolean false |
| `0x0B` | Int64 | inline signed 64-bit integer: 8 bytes big-endian |
| `0x0C` | Double | inline IEEE-754 double: 8 bytes big-endian |

Two details trip up a naive parser:

**Null is `0x03`, node-end is `0x02`.** An older third-party interpretation
labelled `0x03` as an "end of scope" marker. It is not. `0x02` closes a node;
`0x03` stands in for a null child. You can walk the stream successfully while
ignoring `0x02` (by scanning for the next `0x01` node start), which is why the
mistake survived, but it will drop null children and misalign nullable fields.

**Strings are interned.** The first time a string appears it is written with
`0x06` and added to a per-file table. Every later occurrence of the same
string is written with `0x07` plus its table index. A parser that only handles
`0x06` silently loses every repeated string, which in practice is most
identifiers and type names in a module.

## String table

The interning table is per file and built as you read. Whenever you consume a
`0x06 StringDefinition`, append its value to the table. Whenever you consume a
`0x07 StringReference`, resolve it by index into the table you have built so
far. Indices are assigned in definition order starting at 0.

`StringArray` (`0x08`) elements are ordinary strings and may themselves be
either definitions or references.

## Scalars and endianness

All multi-byte integers and floats in `.nitrobin` are **big-endian**. String
lengths and list counts are unsigned 32-bit big-endian. `Int64` is a signed
64-bit big-endian long; `Double` is an 8-byte big-endian IEEE-754 value
(`struct.unpack('>d', ...)`).

Bitwig's underlying I/O layer also has little-endian readers, but they are
used by other formats (wavetables, for example). Nothing in `.nitrobin` uses
them.

## Node structure

A node is:

```
0x01                    node-start marker
<u16 be tag>            AST class tag (see nitro_ast_tags.json)
<content>               fields, in the class's fixed deserialize order
0x02                    node-end marker
```

The tag selects the AST class, and the class determines exactly what content
follows: which scalars, strings, child nodes, and child lists to read, and in
what order. Those per-class read sequences are enumerated in
`nitro_ast_class_methods.json`. Most classes (the stateful ones) read their
fields through a constructor sequence; a handful are singletons (mostly type
tokens like the primitive types) that carry no content and are recovered from
the tag alone.

A **child node** may be nullable. Where a class expects an optional child, the
stream carries either a `0x01` node (present) or a `0x03` Null (absent). A
non-nullable child is always a `0x01` node.

A **child list** is a NodeList:

```
0x04                    list-start marker
<u32 be count>          preallocation hint
<element>*              elements until the end marker
0x05                    list-end marker
```

The u32 count is a size hint, not the authoritative bound. The real terminator
is the `0x05` NodeListEnd marker. Read elements until you hit `0x05`; do not
trust the count to tell you when to stop. (This is the one place where trusting
the count instead of the end marker will desynchronize the parser on some
modules.)

## File frame

The whole file is one `NitroFile` node with a small fixed preamble:

```
0x0A                    source-location flag (False = no per-node source spans)
0x01                    node-start
0x00 0x6F               u16 be tag = NitroFile (0x6F)
0x04                    NodeList start (the file's children)
<u32 be import_count>
<node>*                 imports, then top-level declarations
...
```

The leading byte is a boolean that records whether the file was serialized
with per-node source-location spans. In the shipped image it is `0x0A` (False),
so no source spans are present; a parser must still read and honour the flag,
because when it is `0x09` (True) each node is followed by a source-location
triple (filename string, line, column).

After the flag, the file is a single `NitroFile` node whose body is a NodeList
of the module's imports followed by its declarations (structs, functions, and
so on).

## Declaration tails

Declaration nodes (struct, function, and other declaration subclasses) carry
one extra list *after* their `0x02` node-end: a trailing NodeList of nested
declarations. This is a quirk of how Bitwig serializes declaration scopes. A
parser that stops at `0x02` for every node will miss nested declarations on
exactly these classes. The class-method table marks which classes have this
tail.

## Round-trip guarantee

The parser and serializer in `bitwig_nitro` are spec-driven: there are no
hand-written per-class routines, only the tables plus this framing. Against the
full shipped image, every module parses to clean EOF and re-serializes
byte-for-byte identically to its decrypted input. A clean-EOF parse proves the
stream is grammatically well formed; it does not, on its own, prove Bitwig's
loader would accept a *modified* stream. See
[NITRO_LOAD_MECHANISM.md](NITRO_LOAD_MECHANISM.md) for where that line sits.

## Source classes (provenance)

For anyone verifying this against their own Bitwig build, the framing comes
from these classes in `bitwig.jar`, package `com.bitwig.nitro` (names are
obfuscated and shift between releases):

- a top-level reader that wraps the input stream and owns the string table,
- a frame-marker enum (the `0x01` to `0x0C` table above),
- a tag-to-class dispatch registry,
- a tag enum listing every AST class and its byte,
- an AST node base class,
- and a set of primitive byte readers (big- and little-endian variants) in the
  base I/O package.

You do not need any of that to use the format. It is listed so the byte-level
claims here are checkable.
