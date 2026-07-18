# -*- coding: utf-8 -*-
"""One-time (resumable) bulk decompile of every vault .class into the cache.

Procyon rebuilds the full package tree from the bytecode itself, so we feed
LARGE batches of classes from anywhere in one JVM launch into a single flat
cache root (cache/src/<package>/<Class>.java). This is ~100x faster than one
JVM launch per file.

Resumable: manifest records content hashes; re-runs skip unchanged classes.
Usage:  python scripts/build_cache.py [--limit N] [--batch N]
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
VAULT = Path(r"E:\mcp-obsidian")
CACHE = BASE / "cache"
SRC = CACHE / "src"                    # flat decompiled-source root
MANIFEST = CACHE / "manifest.json"
PROCYON = BASE / "bin" / "procyon-decompiler-0.6.0.jar"
LOG = BASE / "build_cache.log"

JAVA = r"C:\Program Files\Microsoft\jdk-11.0.16.101-hotspot\bin\java.exe"
if not Path(JAVA).is_file():
    JAVA = "java"

BATCH = 400                            # classes per JVM launch

def sha1(p: Path) -> str:
    h = hashlib.sha1(); h.update(p.read_bytes()); return h.hexdigest()

def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def main() -> None:
    args = sys.argv
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 0
    batch_n = int(args[args.index("--batch") + 1]) if "--batch" in args else BATCH

    SRC.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {}

    log("scanning vault ...")
    todo = []
    total = 0
    for cls in VAULT.rglob("*.class"):
        total += 1
        rel = str(cls.relative_to(VAULT))
        ent = manifest.get(rel)
        if ent and ent.get("sha1") == sha1(cls):
            continue
        todo.append(cls)
    if limit:
        todo = todo[:limit]
    log(f"total classes: {total}; to decompile: {len(todo)} (batch {batch_n})")

    t0 = time.time()
    processed = ok = fail = 0
    for i in range(0, len(todo), batch_n):
        batch = todo[i:i + batch_n]
        # Pass class paths via a Procyon @argfile — avoids the Windows ~32KB
        # command-line length limit (WinError 206) on deep package paths.
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as af:
            af.write("\n".join(str(c) for c in batch))
            argfile = af.name
        try:
            subprocess.run(
                [JAVA, "-jar", str(PROCYON), "-o", str(SRC), "@" + argfile],
                capture_output=True, text=True, timeout=1800,
            )
        finally:
            Path(argfile).unlink(missing_ok=True)
        for cls in batch:
            # Procyon derives the package path from the constant pool, not the
            # input path; match by class name anywhere under SRC.
            stem = cls.stem.split("$")[0]
            found = sorted(SRC.rglob(f"{stem}.java"))
            good = bool(found)
            manifest[str(cls.relative_to(VAULT))] = {
                "sha1": sha1(cls),
                "java": str(found[0].relative_to(CACHE)) if good else None,
                "ok": good,
            }
            ok += good; fail += (not good)
        processed += len(batch)
        rate = processed / max(0.1, time.time() - t0)
        eta = (len(todo) - processed) / max(0.1, rate)
        log(f"  {processed}/{len(todo)}  ({rate:.0f}/s, eta {eta/60:.1f}m)")
        MANIFEST.write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    log(f"DONE: processed={processed} ok={ok} failed={fail} "
        f"elapsed={(time.time()-t0)/60:.1f}m")

if __name__ == "__main__":
    main()
