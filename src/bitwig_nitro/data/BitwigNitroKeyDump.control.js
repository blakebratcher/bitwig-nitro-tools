/**
 * Nitro Key Dump — a single-purpose Bitwig controller extension.
 *
 * On init() it reflects over the running Bitwig JVM to locate the nitro-image
 * stream-cipher chain, dumps each transform's byte[] key material and small
 * int fields (e.g. the IV size), writes the result as JSON to
 *   $BITWIG_NITRO_KEYDUMP  (else ~/.bitwig-nitro/nitro-key-dump.json)
 * and shows a popup with the path. That JSON is then read by
 * `nitro-extract-keys --live` on the Python side.
 *
 * It ships NO keys. It reads YOUR own licensed install and writes the recovered
 * key to YOUR own disk. Add it in Bitwig once, let it run, remove it again.
 *
 * Everything runs inside a try/catch: reflection failures produce a
 * status:"error" dump with a diagnostic (the NitroFile static-field roster),
 * never an uncaught throw. No MIDI, no networking, no poll loop.
 *
 * Bitwig's controller JS engine is a Java-hosted Nashorn/GraalJS runtime, so
 * this uses `var` declarations and `java.*` reflection throughout.
 */

// API generation 18 is the current Bitwig extension API (6.0.x). Reflection is
// version-agnostic; only the boilerplate below touches the API surface.
loadAPI(18);

host.defineController(
    "bitwig-nitro-tools",
    "Nitro Key Dump",
    "1.0",
    "D50E1D65-10D3-4AB3-8A21-0F5A32E9797C",
    "bitwig-nitro-tools"
);

host.defineMidiPorts(0, 0);

var Modifier = java.lang.reflect.Modifier;
var Reflect = java.lang.reflect.Array;

// -------------------------------------------------------------------------
// Reflection helpers (ported logic; shape-based, no obfuscated name is
// hardcoded so they survive per-release renames).
// -------------------------------------------------------------------------

// Given an object, find a method on its class taking one input type and
// returning a type (both matched by getName()). Returns the accessible Method
// or null.
function findMethodBySig(obj, paramTypeName, returnTypeName) {
    var ms = obj.getClass().getMethods();
    for (var i = 0; i < ms.length; i++) {
        var m = ms[i];
        var pts = m.getParameterTypes();
        if (pts.length === 1 && pts[0].getName() === paramTypeName &&
            m.getReturnType().getName() === returnTypeName) {
            m.setAccessible(true);
            return m;
        }
    }
    return null;
}

// Discover a stream-cipher chain on a class via its static fields: the first
// static field whose value's class exposes (InputStream)->InputStream (the
// decrypt method). Returns that value (the cipher chain object) or null.
function findStaticStreamCipher(cls) {
    var fs = cls.getDeclaredFields();
    for (var i = 0; i < fs.length; i++) {
        var f = fs[i];
        if (!Modifier.isStatic(f.getModifiers())) continue;
        f.setAccessible(true);
        var v;
        try { v = f.get(null); } catch (e1) { continue; }
        if (v === null) continue;
        var inM = findMethodBySig(v, "java.io.InputStream", "java.io.InputStream");
        if (inM === null) continue;
        return v;
    }
    return null;
}

// Convert a byte[] to a lowercase hex string, capped at 256 bytes (key
// material is on the order of 99 bytes, so the cap never truncates a key).
function bytesToHex(arr) {
    var len = Reflect.getLength(arr);
    var h = "";
    var cap = Math.min(len, 256);
    for (var k = 0; k < cap; k++) {
        var b = Reflect.getByte(arr, k) & 0xFF;
        h += (b < 16 ? "0" : "") + b.toString(16);
    }
    return h;
}

// Recursively collect every byte[] field reachable from `root` (descending
// `maxDepth` levels into nested non-java object fields) as {fieldPath: hex}.
function dumpByteFields(root, maxDepth) {
    var out = {};
    function walk(obj, prefix, depth) {
        if (obj === null || depth < 0) return;
        var cls = obj.getClass();
        while (cls !== null && cls.getName().indexOf("java.lang") !== 0) {
            var fs = cls.getDeclaredFields();
            for (var i = 0; i < fs.length; i++) {
                var f = fs[i];
                f.setAccessible(true);
                var v;
                try { v = f.get(obj); } catch (e1) { continue; }
                if (v === null) continue;
                var ft = f.getType();
                if (ft.isArray() && ft.getComponentType().getName() === "byte") {
                    out[prefix + f.getName()] = bytesToHex(v);
                } else if (depth > 0 && !ft.isPrimitive() && !ft.isArray() &&
                           ft.getName().indexOf("java.") !== 0) {
                    walk(v, prefix + f.getName() + ".", depth - 1);
                }
            }
            cls = cls.getSuperclass();
        }
    }
    walk(root, "", maxDepth === undefined ? 1 : maxDepth);
    return out;
}

// Recursively collect small non-negative integer scalar fields (int/short/
// long/byte) reachable from `root` as {fieldPath: value}. Walks the same
// nested-object structure as dumpByteFields so the IV-size field that sits
// next to the key is captured. The range filter keeps the dump focused on
// size-like values (the nitro-image IV size is 198) instead of hashes.
function dumpIntFields(root, maxDepth) {
    var out = {};
    function readInt(f, obj) {
        var tn = f.getType().getName();
        try {
            if (tn === "int")   return f.getInt(obj);
            if (tn === "short") return f.getShort(obj);
            if (tn === "byte")  return f.getByte(obj) & 0xFF;
            if (tn === "long")  return f.getLong(obj);
        } catch (e) {}
        return null;
    }
    function walk(obj, prefix, depth) {
        if (obj === null || depth < 0) return;
        var cls = obj.getClass();
        while (cls !== null && cls.getName().indexOf("java.lang") !== 0) {
            var fs = cls.getDeclaredFields();
            for (var i = 0; i < fs.length; i++) {
                var f = fs[i];
                f.setAccessible(true);
                var ft = f.getType();
                if (ft.isPrimitive() && ft.getName() !== "boolean" &&
                    ft.getName() !== "char" && ft.getName() !== "float" &&
                    ft.getName() !== "double") {
                    var val = readInt(f, obj);
                    if (val !== null && val >= 0 && val <= 65535) {
                        out[prefix + f.getName()] = val;
                    }
                } else if (depth > 0 && !ft.isPrimitive() && !ft.isArray() &&
                           ft.getName().indexOf("java.") !== 0) {
                    var v;
                    try { v = f.get(obj); } catch (e1) { continue; }
                    if (v !== null) walk(v, prefix + f.getName() + ".", depth - 1);
                }
            }
            cls = cls.getSuperclass();
        }
    }
    walk(root, "", maxDepth === undefined ? 1 : maxDepth);
    return out;
}

// -------------------------------------------------------------------------
// Extraction
// -------------------------------------------------------------------------

// Build the roster of NitroFile static fields (name + type) for the error
// diagnostic when no cipher chain is found.
function staticFieldRoster(cls) {
    var roster = [];
    try {
        var fs = cls.getDeclaredFields();
        for (var i = 0; i < fs.length; i++) {
            var f = fs[i];
            if (Modifier.isStatic(f.getModifiers())) {
                roster.push({ name: f.getName(), type: f.getType().getName() });
            }
        }
    } catch (e) {}
    return roster;
}

// Run the extraction once. Returns the completed dump object (status ok/error).
function extractNitroKeys() {
    var dump = {
        tool: "bitwig-nitro-keydump",
        version: 1,
        status: "error",
        message: "",
        nitro_image: null
    };

    var nfCls;
    try {
        nfCls = java.lang.Class.forName("com.bitwig.nitro.NitroFile");
    } catch (e) {
        dump.message = "NitroFile class not found: " + e.message +
            " (Bitwig version may have moved it).";
        return dump;
    }

    var cipher = findStaticStreamCipher(nfCls);
    if (cipher === null) {
        dump.message = "No static (InputStream)->InputStream cipher chain on " +
            "com.bitwig.nitro.NitroFile. Static-field roster: " +
            JSON.stringify(staticFieldRoster(nfCls));
        return dump;
    }

    var image = { chain_class: cipher.getClass().getName(), chain_length: 0, transforms: [] };

    // Find the chain's transform array (first non-primitive array field with
    // length > 0). Each element is a concrete cipher transform.
    var chainFields = cipher.getClass().getDeclaredFields();
    var transforms = null;
    for (var ci = 0; ci < chainFields.length; ci++) {
        var cf = chainFields[ci];
        if (!cf.getType().isArray()) continue;
        if (cf.getType().getComponentType().isPrimitive()) continue;
        cf.setAccessible(true);
        try {
            var arr = cf.get(cipher);
            if (arr !== null && Reflect.getLength(arr) > 0) {
                transforms = arr;
                break;
            }
        } catch (ae) {}
    }

    if (transforms !== null) {
        var len = Reflect.getLength(transforms);
        image.chain_length = len;
        for (var ti = 0; ti < len; ti++) {
            var t = Reflect.get(transforms, ti);
            if (t === null) continue;
            image.transforms.push({
                index: ti,
                "class": t.getClass().getName(),
                byte_fields: dumpByteFields(t, 1),
                int_fields: dumpIntFields(t, 1)
            });
        }
        dump.message = "Recovered " + image.transforms.length +
            " cipher transform(s) from the nitro-image chain.";
    } else {
        // No transform array — the key may be embedded directly on the chain
        // object. Emit it as a single synthetic transform so the CLI's
        // per-transform selection logic still applies.
        image.transforms.push({
            index: 0,
            "class": cipher.getClass().getName(),
            byte_fields: dumpByteFields(cipher, 2),
            int_fields: dumpIntFields(cipher, 2)
        });
        dump.message = "No transform array on the chain; dumped the chain " +
            "object's own byte[]/int fields (key may be embedded).";
    }

    dump.status = "ok";
    dump.nitro_image = image;
    return dump;
}

// -------------------------------------------------------------------------
// Output
// -------------------------------------------------------------------------

function dumpPath() {
    var env = java.lang.System.getenv("BITWIG_NITRO_KEYDUMP");
    if (env !== null && ("" + env).length > 0) return "" + env;
    return java.lang.System.getProperty("user.home") +
        "/.bitwig-nitro/nitro-key-dump.json";
}

function writeDump(path, dump) {
    var outFile = new java.io.File(path);
    var parent = outFile.getParentFile();
    if (parent !== null) parent.mkdirs();
    var writer = new java.io.FileWriter(outFile);
    try {
        writer.write(JSON.stringify(dump, null, 2));
    } finally {
        writer.close();
    }
}

// -------------------------------------------------------------------------
// Controller lifecycle
// -------------------------------------------------------------------------

function init() {
    var path = dumpPath();
    var dump;
    try {
        dump = extractNitroKeys();
    } catch (e) {
        // Absolute backstop — never let the extraction throw to the host.
        dump = {
            tool: "bitwig-nitro-keydump",
            version: 1,
            status: "error",
            message: "Unexpected extraction failure: " + e,
            nitro_image: null
        };
    }

    try {
        writeDump(path, dump);
        if (dump.status === "ok") {
            host.showPopupNotification("Nitro key dump written to " + path);
        } else {
            host.showPopupNotification("Nitro key dump FAILED (see " + path + "): " + dump.message);
        }
    } catch (e2) {
        // Could not write the file — surface it in the UI, still never throw.
        try {
            host.showPopupNotification("Nitro key dump could not write " + path + ": " + e2);
        } catch (e3) {}
    }
}

function flush() {}

function exit() {}
