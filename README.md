# fs-grep MCP

A small, **read-only** [Model Context Protocol](https://modelcontextprotocol.io) server that
gives Claude Code, Claude Desktop, and other MCP clients fast **content search** over a local
directory tree — including *meaningful* search across compiled Java **`.class`** files.

Built because the standard filesystem MCP servers can read and glob files but can't `grep`
their contents, which is impractical across a tree of tens of thousands of files.

## Why two search planes

Compiled `.class` files are binary, but they carry their **string literals, class names, and
method names as readable UTF-8** in the constant pool. So there are two useful ways to search:

| Plane | Engine | Good for |
|---|---|---|
| **Raw** | [ripgrep](https://github.com/BurntSushi/ripgrep) (binary-safe `-a`) | string literals, markers, class/method names — instant, no preprocessing |
| **Source** | [Procyon](https://github.com/mstrobel/procyon) decompiler → ripgrep over a `.java` cache | logic, conditions, call sites — the things raw byte-grep can't see |

The decompiled cache is built once (and refreshed incrementally), stored **outside** the
searched tree so it doesn't pollute it.

## Tools

| Tool | Plane | Description |
|---|---|---|
| `grep_content` | raw | Regex/literal content search; glob filter, case flag, context lines, binary-as-text. Returns `{file, line, text}`. |
| `grep_files` | raw | Fast triage — just the files whose contents match. |
| `grep_source` | source | Semantic search over the decompiled cache. |
| `glob_files` | — | Find files by name pattern, newest first. |
| `read_file` | — | Bounded, binary-tolerant slice of a text file. |
| `extract_strings` | raw | Printable strings from one binary file. |
| `decompile_class` | source | Decompile one `.class` on demand (cached). |
| `update_cache` | source | Incrementally decompile new/changed classes (bounded batches). |
| `cache_status` | source | Cache coverage: total / cached / stale / failed. |

**Read-only by design:** there are no write, edit, move, or delete tools. Every path is
resolved and confined to an allow-list of roots; escapes (`..`, absolute paths outside the
roots) are rejected before any filesystem access.

## Requirements

- **Windows** (paths and the bundled `rg.exe` are Windows; portable with small changes)
- **Python 3.11+**
- **Java 8 or 11+** on the machine (for the decompiler; Java 8-era bytecode needs no newer JVM)
- **ripgrep** and the **Procyon jar** — fetched by a setup script (not committed to the repo)

## Setup

```bash
git clone https://github.com/<you>/fs-grep-mcp.git
cd fs-grep-mcp

python -m venv venv
venv\Scripts\python -m pip install mcp

# fetch ripgrep + procyon into bin/ (not vendored in git)
venv\Scripts\python scripts\setup_binaries.py
```

Point the server at the tree you want to search with the `FSGREP_VAULT` environment
variable (defaults to `E:\mcp-obsidian`).

### Build the decompile cache (one time, resumable)

```bash
venv\Scripts\python scripts\build_cache.py          # full tree
venv\Scripts\python scripts\build_cache.py --limit 500   # try a slice first
```

Batches hundreds of classes per JVM launch; safe to interrupt and re-run (a content-hash
manifest skips unchanged classes). The raw-search plane works immediately — the cache only
gates `grep_source`.

## Register with a client

**Claude Code** (user scope):

```bash
claude mcp add-json fs-grep "{\"command\":\"<abs>\\venv\\Scripts\\python.exe\",\"args\":[\"<abs>\\src\\fs_grep_server\\server.py\"],\"cwd\":\"<abs>\"}" --scope user
```

**Claude Desktop** — add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "fs-grep": {
      "command": "C:\\path\\to\\fs-grep-mcp\\venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\fs-grep-mcp\\src\\fs_grep_server\\server.py"],
      "cwd": "C:\\path\\to\\fs-grep-mcp"
    }
  }
}
```

Restart the client; the tools appear under `fs-grep`.

## Layout

```
fs-grep-mcp/
├── bin/                 rg.exe + procyon jar   (fetched, git-ignored)
├── cache/               decompiled .java + manifest.json  (git-ignored)
├── src/fs_grep_server/
│   └── server.py        FastMCP server (the tools above)
├── scripts/
│   ├── setup_binaries.py
│   └── build_cache.py
└── README.md
```

## Credits

Designed and built collaboratively by **[jamesbond379](https://github.com/jamesbond379)**
and Claude. jamesbond379 scoped the problem and the options, drove the key design
decisions (two-plane search over the 15,000+ `.class` corpus, the decompile-cache
approach), caught the result-truncation limit, and reviewed each build step; Claude
handled implementation and testing.

## License

MIT
