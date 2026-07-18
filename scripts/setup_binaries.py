# -*- coding: utf-8 -*-
"""Fetch the two vendored binaries the server needs (not committed to git).

  - ripgrep (rg.exe)     -> bin/rg.exe
  - Procyon decompiler   -> bin/procyon-decompiler-<ver>.jar

Run once after cloning:  python scripts/setup_binaries.py
"""
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
BIN = BASE / "bin"
BIN.mkdir(exist_ok=True)

RG_URL = ("https://github.com/BurntSushi/ripgrep/releases/download/"
          "14.1.1/ripgrep-14.1.1-x86_64-pc-windows-msvc.zip")
PROCYON_URL = ("https://github.com/mstrobel/procyon/releases/download/"
               "v0.6.0/procyon-decompiler-0.6.0.jar")

def fetch(url: str) -> bytes:
    print(f"  downloading {url.rsplit('/',1)[-1]} ...")
    with urllib.request.urlopen(url) as r:
        return r.read()

def main() -> None:
    rg = BIN / "rg.exe"
    if rg.is_file():
        print("rg.exe already present")
    else:
        data = fetch(RG_URL)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next(n for n in z.namelist() if n.endswith("rg.exe"))
            rg.write_bytes(z.read(name))
        print(f"  -> {rg}")

    jar = BIN / "procyon-decompiler-0.6.0.jar"
    if jar.is_file():
        print("procyon jar already present")
    else:
        jar.write_bytes(fetch(PROCYON_URL))
        print(f"  -> {jar}")

    print("done. binaries ready in bin/")

if __name__ == "__main__":
    sys.exit(main())
