from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .library import Library, hms, to_markdown
from .transcripts import make_engine


def default_db():
    base = os.environ.get("GREPCAST_DB")
    if base:
        return base
    d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "grepcast"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "library.db")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="grepcast", description="Turn saved videos, talks and podcasts into a local, searchable transcript library. Keeps your media where it is, indexes timestamps in SQLite FTS5, and transcribes locally (faster-whisper or whisper.cpp) or imports existing .srt/.vtt/.json subtitles. Nothing is uploaded.",
                                epilog="Exit codes: 0 ok (search: hits found), 1 no hits, 2 usage error.")
    p.add_argument("-v", "--version", action="version", version=__version__)
    p.add_argument("--db", default=None, help="library database (default: $GREPCAST_DB or ~/.local/share/grepcast/library.db)")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("ingest", help="index media files/folders (incremental)")
    i.add_argument("paths", nargs="+")
    i.add_argument("--engine", choices=["none", "faster-whisper", "whisper.cpp"], default="none", help="transcriber for media without a subtitle file (default: none, import sidecars only)")
    i.add_argument("--model", help="faster-whisper model name (default small) or whisper.cpp ggml model path")
    i.add_argument("--whisper-bin", help="path to whisper.cpp CLI")
    i.add_argument("--language", help="spoken language code, e.g. en, es, ja (default: auto-detect)")
    i.add_argument("--force", action="store_true", help="re-index even if unchanged")
    i.add_argument("--no-sidecar", action="store_true", help="don't write .srt next to newly transcribed media")
    s = sub.add_parser("search", help="full-text search; prints timestamped hits")
    s.add_argument("query", nargs="+"); s.add_argument("-n", "--limit", type=int, default=20)
    s.add_argument("-C", "--context", type=int, default=0, help="include N neighbouring segments")
    s.add_argument("--json", action="store_true")
    sub.add_parser("list", help="list indexed media")
    e = sub.add_parser("export", help="export one transcript as Markdown (timestamped links) or SRT")
    e.add_argument("media", help="path or title"); e.add_argument("--format", choices=["md", "srt", "txt"], default="md")
    sub.add_parser("prune", help="forget media files that no longer exist")
    a = p.parse_args(argv)
    lib = Library(a.db or default_db())
    try:
        if a.cmd == "ingest":
            for x in a.paths:
                if not Path(x).exists():
                    print(f"grepcast: not found: {x}", file=sys.stderr); return 2
            try:
                eng = make_engine(a.engine, a.model, a.whisper_bin)
            except RuntimeError as err:
                print(f"grepcast: {err}", file=sys.stderr); return 2
            st = lib.ingest(a.paths, eng, a.language, a.force, log=lambda m: print(m, file=sys.stderr), write_sidecar=not a.no_sidecar)
            print(f"grepcast: {st['added']} indexed, {st['skipped']} unchanged, {st['pending']} pending, {st['failed']} failed")
            return 0
        if a.cmd == "search":
            try:
                hits = lib.search(" ".join(a.query), a.limit, a.context)
            except Exception as err:
                print(f"grepcast: bad query: {err}", file=sys.stderr); return 2
            if a.json:
                print(json.dumps(hits, ensure_ascii=False, indent=2))
            else:
                for h in hits:
                    print(f"{h['title']} @ {hms(h['start'])}  {h['snippet']}\n    {h['link']}" + (f"\n    … {h['context']}" if h.get("context") else ""))
            return 0 if hits else 1
        if a.cmd == "list":
            for path, title, src, n, dur in lib.items():
                print(f"{title}  ({n} segments, {hms(dur)}, {src})\n    {path}")
            return 0
        if a.cmd == "export":
            path, segs = lib.segments(str(Path(a.media).resolve()) if Path(a.media).exists() else a.media)
            if not segs:
                print(f"grepcast: not in library: {a.media}", file=sys.stderr); return 2
            if a.format == "md":
                print(to_markdown(Path(path).stem, path, segs), end="")
            elif a.format == "srt":
                from .library import to_srt
                print(to_srt(segs), end="")
            else:
                print("\n".join(t for _, _, t in segs))
            return 0
        if a.cmd == "prune":
            gone = lib.prune(); print(f"grepcast: removed {len(gone)} missing item(s)"); return 0
    finally:
        lib.close()
    return 2
