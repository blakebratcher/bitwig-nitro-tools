# Nitro Key Dump controller

A minimal, single-purpose Bitwig controller extension that recovers the
**nitro-image cipher key** from your own running Bitwig Studio and writes it to
your own disk. It ships no keys and no Bitwig content; it reads the licensed
install already on your machine.

Both Bitwig cipher keys are materialized at runtime — neither is present in
`bitwig.jar` in any form (checked raw, hex, and base64 on 6.0.11). Reading the
key out of a running JVM is the only proven recovery path, and that is all this
controller does. On startup it reflects over Bitwig's `com.bitwig.nitro.NitroFile`
cipher chain, dumps each transform's key material, writes a small JSON handshake
file, and shows a popup with the path. Then the Python CLI reads that file.

## What it writes

On `init()` the controller writes JSON to:

- `$BITWIG_NITRO_KEYDUMP` if that environment variable is set, otherwise
- `~/.bitwig-nitro/nitro-key-dump.json`

Shape:

```json
{
  "tool": "bitwig-nitro-keydump",
  "version": 1,
  "status": "ok",
  "message": "Recovered 3 cipher transform(s) from the nitro-image chain.",
  "nitro_image": {
    "chain_class": "com.bitwig.nitro.<obfuscated>",
    "chain_length": 3,
    "transforms": [
      {
        "index": 2,
        "class": "com.bitwig.nitro.<obfuscated>",
        "byte_fields": { "<field>": "<hex>" },
        "int_fields":  { "<field>": 198 }
      }
    ]
  }
}
```

Each transform reports **both** its reachable `byte[]` fields (as hex) and its
small integer fields (such as the IV size, `198` for the nitro-image entry), so
the dump is self-describing and the CLI can pick the right key by rule rather
than by guessing. If reflection cannot find the cipher chain, the controller
writes `status: "error"` with a diagnostic (the `NitroFile` static-field roster)
instead of throwing — Bitwig keeps running normally either way.

The controller does no MIDI, no networking, and no polling. It runs the
extraction exactly once, at load.

## Install

1. Find your Bitwig **Controller Scripts** directory:
   - Linux: `~/Bitwig Studio/Controller Scripts/`
   - macOS / Windows: `~/Documents/Bitwig Studio/Controller Scripts/`
2. Let the CLI place the bundled script there for you (it ships inside the
   `bitwig_nitro` package, so this works from a wheel or a source checkout):

   ```bash
   nitro-extract-keys --install-controller
   ```

   To copy it by hand instead, the script is at
   `src/bitwig_nitro/data/BitwigNitroKeyDump.control.js` in the repo (or in the
   installed package's `data/` directory).

## Enable and run

1. In Bitwig: **Settings → Controllers → Add Controller**.
2. Choose vendor **bitwig-nitro-tools**, product **Nitro Key Dump**, and add it.
   (You can also use **Detect available controllers**.)
3. On add/enable, `init()` runs immediately. Watch for the popup:
   `Nitro key dump written to …`.
4. Hand the dump to the Python side:

   ```bash
   nitro-extract-keys --live
   ```

   That reads the dump, selects the nitro-image key (preferring the transform
   whose IV size is `198`), and — if your install's `nitro-image` is present —
   validates the key by decrypting one member before writing `keys.json`.

## Remove it afterward

The controller is only needed for the one-time dump. Once `keys.json` is
written you can remove it in **Settings → Controllers** and delete the script.
Deleting the dump file (`~/.bitwig-nitro/nitro-key-dump.json`) afterward is also
fine; it holds your own key material extracted from your own install, so keep it
local and out of version control, exactly like `keys.json`.

## Honest status

The reflection sequence this controller uses is verified **live on Bitwig
6.0.11**: driven through the same calls, it recovers the correct nitro-image key
(confirmed by decrypting a real module with it). It matches the cipher chain by
shape, with no obfuscated name hardcoded, so it is meant to survive per-release
renames. What is still yours to confirm is that this standalone controller
*loads and runs* inside your Bitwig — enable it, check the popup, and run
`nitro-extract-keys --live`. A valid key decrypts and parses a module cleanly,
and the CLI tells you whether validation passed. If the dump comes back
`status: "error"`, the roster in `message` is the starting point for adapting
the shape-match to your build.
