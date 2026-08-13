/**
 * Nitro Std Dump — a single-purpose Bitwig controller extension.
 *
 * On init() it reflects over the running Bitwig JVM to decrypt the *source*
 * standard library, `Library/nitro-std` — the ~121 `.nitro` files the compiled
 * `nitro-image` modules `import`. Unlike `nitro-image` (a self-inverse XOR/Dag
 * cipher that decrypts fully offline), each `nitro-std` member is wrapped in a
 * runtime PRNG stream cipher that is NOT reproducible offline, so decryption
 * must run inside Bitwig. This controller does exactly that and writes the
 * decrypted tree to YOUR disk.
 *
 * Mechanism: a `.nitro` source member decrypts via a static method on
 *   com.bitwig.nitro.NitroFile  of shape  (HmA, byte[]) -> java.io.Reader
 * (obfuscated name; `gGl` at time of writing) that runs the cipher and hands
 * back a Reader over the decrypted UTF-8 source. We discover it by SHAPE
 * (static, returns java.io.Reader, 2 params, 2nd is byte[]) and self-select the
 * required context object from NitroFile's static fields, so it survives the
 * per-release obfuscation renames. (The `(InputStream)->InputStream` cipher the
 * key-dump controller finds is the `.nitrobin` binary shape; it returns
 * *undecrypted* bytes for `.nitro` source, which is why this separate path
 * exists.)
 *
 * Output (both under ~/.bitwig-nitro, or their env overrides):
 *   $BITWIG_NITRO_STD_OUT   decrypted tree  (default ~/.bitwig-nitro/nitro-std-decrypted/)
 *   $BITWIG_NITRO_STD_DUMP  manifest JSON   (default ~/.bitwig-nitro/nitro-std-dump.json)
 *
 * Input: $BITWIG_NITRO_STD (full path to the nitro-std file) wins; else the
 * common per-OS install locations are tried.
 *
 * It ships NO keys and NO decrypted content — it reads YOUR own licensed
 * install and writes to YOUR own disk. Add it in Bitwig once, let it run,
 * remove it again. Everything runs inside try/catch: failures produce a
 * status:"error" manifest with a diagnostic, never an uncaught throw. No MIDI,
 * no networking, no poll loop.
 *
 * Bitwig's controller JS engine is a Java-hosted Nashorn/GraalJS runtime, so
 * this uses `var` declarations and `java.*` reflection throughout.
 */

loadAPI(18);

host.defineController(
    "bitwig-nitro-tools",
    "Nitro Std Dump",
    "1.0",
    "7C3A9F2E-4B18-4D0A-9E6C-2A1B5F8C7D34",
    "bitwig-nitro-tools"
);

host.defineMidiPorts(0, 0);

var Modifier = java.lang.reflect.Modifier;

// -------------------------------------------------------------------------
// Locate nitro-std
// -------------------------------------------------------------------------

// Candidate nitro-std locations, env override first, then per-OS defaults.
function nitroStdCandidates() {
    var out = [];
    var env = java.lang.System.getenv("BITWIG_NITRO_STD");
    if (env !== null && ("" + env).length > 0) out.push("" + env);
    var home = java.lang.System.getProperty("user.home");
    var flatpakUser = home + "/.local/share/flatpak/app/com.bitwig.BitwigStudio/current/active/files/Library/nitro-std";
    out.push("/opt/bitwig-studio/Library/nitro-std");
    out.push("/usr/lib/bitwig-studio/Library/nitro-std");
    out.push("/var/lib/flatpak/app/com.bitwig.BitwigStudio/current/active/files/Library/nitro-std");
    out.push(flatpakUser);
    out.push("/Applications/Bitwig Studio.app/Contents/Resources/Library/nitro-std");
    out.push("C:/Program Files/Bitwig Studio/Library/nitro-std");
    return out;
}

function findNitroStd() {
    var cands = nitroStdCandidates();
    for (var i = 0; i < cands.length; i++) {
        var f = new java.io.File(cands[i]);
        if (f.exists() && f.isFile()) return f;
    }
    return null;
}

// -------------------------------------------------------------------------
// Decrypt (shape-based reflection)
// -------------------------------------------------------------------------

// The static (context, byte[]) -> java.io.Reader decrypt method on NitroFile.
function findBytesToReaderMethod(cls) {
    var ms = cls.getDeclaredMethods();
    for (var i = 0; i < ms.length; i++) {
        var m = ms[i];
        if (!Modifier.isStatic(m.getModifiers())) continue;
        if (m.getReturnType().getName() !== "java.io.Reader") continue;
        var pts = m.getParameterTypes();
        if (pts.length === 2 && pts[1].getName() === "[B") {
            m.setAccessible(true);
            return m;
        }
    }
    return null;
}

// Static fields of the given type on `cls`, as candidate context objects, plus
// a trailing null (last resort: no context).
function contextCandidates(cls, ctxType) {
    var out = [];
    var fs = cls.getDeclaredFields();
    for (var i = 0; i < fs.length; i++) {
        var f = fs[i];
        if (!Modifier.isStatic(f.getModifiers())) continue;
        if (!f.getType().equals(ctxType)) continue;
        f.setAccessible(true);
        try { out.push(f.get(null)); } catch (e) {}
    }
    out.push(null);
    return out;
}

// Read a Reader fully into a JS string.
function readAll(reader) {
    var sb = new java.lang.StringBuilder();
    var ch;
    while ((ch = reader.read()) !== -1) sb.append(String.fromCharCode(ch));
    return sb.toString();
}

// Heuristic: decrypted .nitro source is overwhelmingly printable ASCII.
function looksLikeSource(s) {
    var scan = Math.min(s.length, 400);
    if (scan === 0) return false;
    var printable = 0;
    for (var i = 0; i < scan; i++) {
        var c = s.charCodeAt(i);
        if (c === 9 || c === 10 || c === 13 || (c >= 32 && c < 127)) printable++;
    }
    return printable / scan > 0.9;
}

// Decrypt one member's raw bytes -> UTF-8 byte[], or null on failure.
function decryptMember(readerMethod, ctxs, bytes) {
    for (var i = 0; i < ctxs.length; i++) {
        try {
            var reader = readerMethod.invoke(null, ctxs[i], bytes);
            var text = readAll(reader);
            if (looksLikeSource(text)) {
                return new java.lang.String(text).getBytes("UTF-8");
            }
        } catch (e) { /* try next context */ }
    }
    return null;
}

// -------------------------------------------------------------------------
// Output paths
// -------------------------------------------------------------------------

function outDir() {
    var env = java.lang.System.getenv("BITWIG_NITRO_STD_OUT");
    if (env !== null && ("" + env).length > 0) return "" + env;
    return java.lang.System.getProperty("user.home") + "/.bitwig-nitro/nitro-std-decrypted";
}

function manifestPath() {
    var env = java.lang.System.getenv("BITWIG_NITRO_STD_DUMP");
    if (env !== null && ("" + env).length > 0) return "" + env;
    return java.lang.System.getProperty("user.home") + "/.bitwig-nitro/nitro-std-dump.json";
}

// -------------------------------------------------------------------------
// Run
// -------------------------------------------------------------------------

function decryptNitroStd() {
    var manifest = {
        tool: "bitwig-nitro-stddump",
        version: 1,
        status: "error",
        message: "",
        nitro_std: null,
        out_dir: null,
        files: []
    };

    var stdFile = findNitroStd();
    if (stdFile === null) {
        manifest.message = "nitro-std not found. Set $BITWIG_NITRO_STD to its full path.";
        return manifest;
    }
    manifest.nitro_std = "" + stdFile.getAbsolutePath();

    var nfCls;
    try {
        nfCls = java.lang.Class.forName("com.bitwig.nitro.NitroFile");
    } catch (e) {
        manifest.message = "NitroFile class not found: " + e.message;
        return manifest;
    }

    var readerMethod = findBytesToReaderMethod(nfCls);
    if (readerMethod === null) {
        manifest.message = "No static (ctx, byte[]) -> java.io.Reader decrypt method on " +
            "com.bitwig.nitro.NitroFile (source-decrypt path moved).";
        return manifest;
    }
    var ctxType = readerMethod.getParameterTypes()[0];
    var ctxs = contextCandidates(nfCls, ctxType);

    var dir = outDir();
    manifest.out_dir = dir;
    var ok = 0, fail = 0;
    var failed = [];

    var zf = new java.util.zip.ZipFile(stdFile);
    try {
        var entries = zf.entries();
        while (entries.hasMoreElements()) {
            var entry = entries.nextElement();
            if (entry.isDirectory()) continue;
            var name = "" + entry.getName();
            var is = zf.getInputStream(entry);
            var raw;
            try { raw = is.readAllBytes(); } finally { is.close(); }

            var plain = decryptMember(readerMethod, ctxs, raw);
            if (plain === null) { fail++; failed.push(name); continue; }

            var dest = new java.io.File(dir, name);
            var parent = dest.getParentFile();
            if (parent !== null) parent.mkdirs();
            java.nio.file.Files.write(dest.toPath(), plain);
            manifest.files.push({ path: name, size: java.lang.reflect.Array.getLength(plain) });
            ok++;
        }
    } finally {
        zf.close();
    }

    manifest.status = fail === 0 ? "ok" : "partial";
    manifest.message = "Decrypted " + ok + " nitro-std member(s)" +
        (fail > 0 ? ("; " + fail + " failed: " + JSON.stringify(failed.slice(0, 8))) : "") +
        " -> " + dir;
    return manifest;
}

function writeManifest(path, manifest) {
    var outFile = new java.io.File(path);
    var parent = outFile.getParentFile();
    if (parent !== null) parent.mkdirs();
    var writer = new java.io.FileWriter(outFile);
    try { writer.write(JSON.stringify(manifest, null, 2)); } finally { writer.close(); }
}

function init() {
    var path = manifestPath();
    var manifest;
    try {
        manifest = decryptNitroStd();
    } catch (e) {
        manifest = {
            tool: "bitwig-nitro-stddump",
            version: 1,
            status: "error",
            message: "Unexpected failure: " + e,
            nitro_std: null,
            out_dir: null,
            files: []
        };
    }
    try {
        writeManifest(path, manifest);
        if (manifest.status === "ok") {
            host.showPopupNotification("Nitro std dump: " + manifest.files.length +
                " files -> " + manifest.out_dir);
        } else {
            host.showPopupNotification("Nitro std dump " + manifest.status +
                " (see " + path + "): " + manifest.message);
        }
    } catch (e2) {
        try { host.showPopupNotification("Nitro std dump could not write " + path + ": " + e2); } catch (e3) {}
    }
}

function flush() {}

function exit() {}
