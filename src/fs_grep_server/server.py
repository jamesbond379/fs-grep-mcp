# -*- coding: utf-8 -*-
"""fs-grep MCP server.

Read-only, root-confined content search over the Obsidian vault (incl. 15k+
binary .class files) plus a Procyon decompile cache for semantic search.

Two search planes:
  raw   -> ripgrep (bundled rg.exe) over the vault, binary-as-text capable
  cache -> ripgrep over decompiled .java mirror (built by scripts/build_cache.py)

Security: no write tools; every path resolved and confined to ALLOWED_ROOTS.
"""
import json
import os
import re
import subprocess
import hashlib
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------- paths / config
HERE = Path(__file__).resolve().parent          # .../src/fs_grep_server
BASE = HERE.parent.parent                        # .../fs-grep-mcp
RG = BASE / "bin" / "rg.exe"
PROCYON = BASE / "bin" / "procyon-decompiler-0.6.0.jar"
CACHE = BASE / "cache"
MANIFEST = CACHE / "manifest.json"
SRC = CACHE / "src"                     # flat decompiled-source root (matches build_cache.py)

VAULT = Path(os.environ.get("FSGREP_VAULT", r"E:\mcp-obsidian")).resolve()
ALLOWED_ROOTS = [VAULT, CACHE.resolve()]

_JAVA_CANDIDATES = [
    r"C:\Program Files\Microsoft\jdk-11.0.16.101-hotspot\bin\java.exe",
    r"C:\Program Files\Eclipse Foundation\jdk-8.0.302.8-hotspot\bin\java.exe",
    "java",
]
def _java() -> str:
    for c in _JAVA_CANDIDATES:
        if c == "java" or Path(c).is_file():
            return c
    return "java"

MAX_SNIPPET = 400          # truncate long (binary-ish) matched lines
DEFAULT_MAX_RESULTS = 200

mcp = FastMCP("fs-grep")

# ---------------------------------------------------------------- helpers
def _confine(p: str) -> Path:
    """Resolve a path and require it to live under an allowed root."""
    rp = Path(p).resolve()
    for root in ALLOWED_ROOTS:
        try:
            rp.relative_to(root)
            return rp
        except ValueError:
            continue
    raise ValueError(f"path outside allowed roots: {p}")

def _rg(args: list, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(RG), "--no-ignore", "--hidden", "--no-messages", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )

def _grep_json(pattern: str, root: Path, glob: str | None, case_insensitive: bool,
               literal: bool, context: int, max_results: int,
               binary_as_text: bool) -> dict:
    args = ["--json"]
    if binary_as_text:
        args.append("-a")
    if case_insensitive:
        args.append("-i")
    if literal:
        args.append("-F")
    if context:
        args += ["-C", str(context)]
    if glob:
        args += ["-g", glob]
    args += ["-e", pattern, str(root)]
    cp = _rg(args)
    hits, files = [], set()
    truncated = False
    for line in cp.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t not in ("match", "context"):
            continue
        d = ev["data"]
        path = d["path"].get("text") or ""
        text = (d["lines"].get("text") or "").rstrip("\r\n")
        if len(text) > MAX_SNIPPET:
            text = text[:MAX_SNIPPET] + " …[truncated]"
        hits.append({
            "file": path,
            "line": d.get("line_number"),
            "kind": t,
            "text": text,
        })
        files.add(path)
        if len(hits) >= max_results:
            truncated = True
            break
    return {"pattern": pattern, "root": str(root), "hit_count": len(hits),
            "file_count": len(files), "truncated": truncated, "hits": hits}

def _load_manifest() -> dict:
    if MANIFEST.is_file():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {}

def _save_manifest(m: dict) -> None:
    CACHE.mkdir(exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=1), encoding="utf-8")

def _sha1(p: Path) -> str:
    h = hashlib.sha1()
    h.update(p.read_bytes())
    return h.hexdigest()

def _decompile_one(cls: Path) -> dict:
    """Decompile one .class into the flat cache/src root; update manifest.
    Procyon rebuilds the package path from the bytecode, so output goes to a
    single root and we locate the result by class name."""
    SRC.mkdir(parents=True, exist_ok=True)
    cp = subprocess.run(
        [_java(), "-jar", str(PROCYON), "-o", str(SRC), str(cls)],
        capture_output=True, text=True, timeout=120,
    )
    stem = cls.stem.split("$")[0]           # inner classes fold into outer
    found = sorted(SRC.rglob(f"{stem}.java"))
    java_rel = str(found[0].relative_to(CACHE)) if found else None
    m = _load_manifest()
    m[str(cls.relative_to(VAULT))] = {"sha1": _sha1(cls), "java": java_rel,
                                      "ok": bool(found)}
    _save_manifest(m)
    return {"class": str(cls), "java": java_rel, "ok": bool(found),
            "stderr_tail": cp.stderr[-300:] if cp.returncode else ""}

# ---------------------------------------------------------------- tools: raw plane
@mcp.tool()
def grep_content(pattern: str, path: str = "", glob: str = "",
                 case_insensitive: bool = False, literal: bool = False,
                 context: int = 0, max_results: int = DEFAULT_MAX_RESULTS,
                 binary_as_text: bool = True) -> str:
    """Search file CONTENTS in the vault (raw plane; binary-safe for .class).

    pattern: regex (or literal string if literal=True). glob e.g. "*.class",
    "*.md". path: optional subdir to narrow the search. Returns JSON with
    per-line hits {file, line, text}. For binary files, matched 'lines' are
    byte-runs around the match (string literals in .class constant pools).
    """
    root = _confine(path) if path else VAULT
    res = _grep_json(pattern, root, glob or None, case_insensitive, literal,
                     context, max(1, min(max_results, 20000)), binary_as_text)
    return json.dumps(res, ensure_ascii=False)

@mcp.tool()
def grep_files(pattern: str, path: str = "", glob: str = "",
               case_insensitive: bool = False, literal: bool = False,
               binary_as_text: bool = True, max_results: int = 20000) -> str:
    """FAST triage: list only the FILES whose contents match (no line detail).

    Ideal for 'which of 15k .class files mention X'. Same options as
    grep_content. Default cap (20000) exceeds the whole vault, so all matching
    files are normally returned. Returns JSON {total_matches, returned,
    truncated, files:[...]} - total_matches is the TRUE count even if the
    returned list was capped.
    """
    root = _confine(path) if path else VAULT
    args = ["-l"]
    if binary_as_text:
        args.append("-a")
    if case_insensitive:
        args.append("-i")
    if literal:
        args.append("-F")
    if glob:
        args += ["-g", glob]
    args += ["-e", pattern, str(root)]
    cp = _rg(args)
    all_files = [f for f in cp.stdout.splitlines() if f]
    files = all_files[:max_results]
    return json.dumps({"pattern": pattern,
                       "total_matches": len(all_files),
                       "returned": len(files),
                       "truncated": len(files) < len(all_files),
                       "files": files}, ensure_ascii=False)

@mcp.tool()
def glob_files(pattern: str, path: str = "", max_results: int = 500) -> str:
    """Find files by NAME pattern (e.g. "**/Mbo*.class"), newest first."""
    root = _confine(path) if path else VAULT
    matches = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime,
                     reverse=True)[:max_results]
    return json.dumps({"count": len(matches),
                       "files": [str(m) for m in matches]}, ensure_ascii=False)

@mcp.tool()
def read_file(path: str, offset: int = 1, limit: int = 300) -> str:
    """Read a text file slice (1-based line offset). Binary-tolerant."""
    p = _confine(path)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    piece = lines[max(0, offset - 1): max(0, offset - 1) + limit]
    return json.dumps({"file": str(p), "total_lines": len(lines),
                       "offset": offset, "lines": piece}, ensure_ascii=False)

@mcp.tool()
def extract_strings(path: str, min_len: int = 6, max_results: int = 500) -> str:
    """Printable ASCII strings from ONE binary file (jar/class hunting)."""
    p = _confine(path)
    data = p.read_bytes()
    found = re.findall(rb"[ -~]{%d,}" % max(2, min_len), data)
    out = [s.decode("ascii", "replace") for s in found[:max_results]]
    return json.dumps({"file": str(p), "count": len(out),
                       "strings": out}, ensure_ascii=False)

# ---------------------------------------------------------------- tools: cache plane
@mcp.tool()
def grep_source(pattern: str, glob: str = "*.java", case_insensitive: bool = False,
                literal: bool = False, context: int = 2,
                max_results: int = DEFAULT_MAX_RESULTS) -> str:
    """SEMANTIC search over DECOMPILED source (the Procyon cache).

    Use for logic/conditions/call-sites that raw .class grep can't see.
    Run cache_status first if unsure of coverage. Hits reference cached .java
    paths, which mirror the vault's directory structure.
    """
    if not SRC.is_dir() or not any(SRC.iterdir()):
        return json.dumps({"error": "decompile cache is empty - run "
                           "scripts/build_cache.py (or update_cache) first"})
    # Search only the decompiled-source root (not manifest.json etc.).
    res = _grep_json(pattern, SRC, glob or None, case_insensitive, literal,
                     context, max(1, min(max_results, 20000)), False)
    return json.dumps(res, ensure_ascii=False)

@mcp.tool()
def decompile_class(path: str) -> str:
    """Decompile ONE .class from the vault into the cache; returns the source
    location and first lines. Cached for future grep_source calls."""
    cls = _confine(path)
    if cls.suffix != ".class":
        return json.dumps({"error": "not a .class file"})
    info = _decompile_one(cls)
    preview = ""
    if info["ok"]:
        jp = CACHE / info["java"]
        preview = "\n".join(jp.read_text(encoding="utf-8",
                            errors="replace").splitlines()[:40])
    return json.dumps({**info, "preview": preview}, ensure_ascii=False)

@mcp.tool()
def update_cache(max_files: int = 25) -> str:
    """Incrementally decompile NEW or CHANGED .class files (bounded batch,
    timeout-safe). Re-run until 'remaining' is 0. Bulk initial build should
    use scripts/build_cache.py instead."""
    m = _load_manifest()
    todo = []
    for cls in VAULT.rglob("*.class"):
        rel = str(cls.relative_to(VAULT))
        if rel not in m or m[rel].get("sha1") != _sha1(cls):
            todo.append(cls)
    done = []
    for cls in todo[:max_files]:
        done.append(_decompile_one(cls)["ok"])
    return json.dumps({"processed": len(done), "succeeded": sum(done),
                       "remaining": max(0, len(todo) - len(done))})

@mcp.tool()
def cache_status() -> str:
    """Decompile-cache coverage: total classes, cached, stale, failed."""
    m = _load_manifest()
    total = cached = stale = failed = 0
    for cls in VAULT.rglob("*.class"):
        total += 1
        rel = str(cls.relative_to(VAULT))
        ent = m.get(rel)
        if not ent:
            continue
        if ent.get("sha1") == _sha1(cls):
            cached += 1
            if not ent.get("ok"):
                failed += 1
        else:
            stale += 1
    return json.dumps({"total_classes": total, "cached": cached,
                       "stale": stale, "failed_decompiles": failed,
                       "uncached": total - cached - stale})

def main() -> None:
    mcp.run()

if __name__ == "__main__":
    main()
