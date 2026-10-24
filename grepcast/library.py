"""SQLite + FTS5 transcript library."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

from .transcripts import MEDIA, find_sidecar, parse_file

SCHEMA = """
CREATE TABLE IF NOT EXISTS media(id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT, size INTEGER, mtime REAL, fingerprint TEXT, source TEXT, added REAL);
CREATE TABLE IF NOT EXISTS segments(id INTEGER PRIMARY KEY, media_id INTEGER REFERENCES media(id) ON DELETE CASCADE, start REAL, end REAL, text TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS seg_fts USING fts5(text, content='segments', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS seg_ai AFTER INSERT ON segments BEGIN INSERT INTO seg_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS seg_ad AFTER DELETE ON segments BEGIN INSERT INTO seg_fts(seg_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
"""


def fingerprint(p: Path):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        h.update(f.read(1 << 20))
    h.update(str(p.stat().st_size).encode())
    return h.hexdigest()[:32]


class Library:
    def __init__(self, db):
        self.db = sqlite3.connect(str(db))
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.commit(); self.db.close()

    def _known(self, p: Path):
        r = self.db.execute("SELECT id, size, mtime FROM media WHERE path=?", (str(p),)).fetchone()
        return r

    def add(self, media: Path, segs, source):
        st = media.stat()
        self.db.execute("DELETE FROM media WHERE path=?", (str(media),))
        cur = self.db.execute("INSERT INTO media(path,title,size,mtime,fingerprint,source,added) VALUES(?,?,?,?,?,?,?)",
                              (str(media), media.stem, st.st_size, st.st_mtime, fingerprint(media), source, time.time()))
        mid = cur.lastrowid
        self.db.executemany("INSERT INTO segments(media_id,start,end,text) VALUES(?,?,?,?)", [(mid, s, e, t) for s, e, t in segs])
        self.db.commit()
        return len(segs)

    def ingest(self, roots, engine=None, language=None, force=False, log=print, write_sidecar=True):
        stats = {"added": 0, "skipped": 0, "pending": 0, "failed": 0}
        for root in roots:
            root = Path(root)
            items = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA)
            for m in items:
                m = m.resolve()
                k = self._known(m)
                st = m.stat()
                if k and not force and k[1] == st.st_size and abs(k[2] - st.st_mtime) < 1e-6:
                    stats["skipped"] += 1; continue
                side = find_sidecar(m)
                try:
                    if side:
                        segs, src = parse_file(side), f"sidecar:{side.name}"
                    elif engine:
                        log(f"transcribing {m.name} with {engine.name} ...")
                        segs, src = engine.transcribe(m, language), engine.name
                        if write_sidecar:
                            try:
                                m.with_suffix(".srt").write_text(to_srt(segs), encoding="utf-8")
                            except OSError:
                                pass
                    else:
                        stats["pending"] += 1; log(f"pending (no transcript, no engine): {m}"); continue
                    n = self.add(m, segs, src)
                    stats["added"] += 1; log(f"indexed {m.name}: {n} segment(s) [{src}]")
                except Exception as e:  # keep going on one bad file
                    stats["failed"] += 1; log(f"failed {m}: {e}")
        return stats

    def search(self, query, limit=20, context=0):
        q = query if any(op in query for op in ('"', " OR ", " NOT ", " NEAR(", "*")) else " ".join('"' + w.replace('"', '""') + '"' for w in query.split())
        rows = self.db.execute(
            "SELECT s.id, m.path, m.title, s.start, s.end, snippet(seg_fts, 0, '[', ']', '…', 16), s.media_id FROM seg_fts "
            "JOIN segments s ON s.id = seg_fts.rowid JOIN media m ON m.id = s.media_id WHERE seg_fts MATCH ? ORDER BY bm25(seg_fts) LIMIT ?",
            (q, limit)).fetchall()
        out = []
        for sid, path, title, start, end, snip, mid in rows:
            hit = {"path": path, "title": title, "start": start, "end": end, "snippet": snip, "link": media_link(path, start)}
            if context:
                ctx = self.db.execute("SELECT text FROM segments WHERE media_id=? AND id BETWEEN ? AND ? ORDER BY id", (mid, sid - context, sid + context)).fetchall()
                hit["context"] = " ".join(c[0] for c in ctx)
            out.append(hit)
        return out

    def items(self):
        return self.db.execute("SELECT m.path, m.title, m.source, COUNT(s.id), COALESCE(MAX(s.end),0) FROM media m LEFT JOIN segments s ON s.media_id=m.id GROUP BY m.id ORDER BY m.title").fetchall()

    def segments(self, key):
        r = self.db.execute("SELECT id, path FROM media WHERE path=? OR title=? ORDER BY added DESC LIMIT 1", (str(key), str(key))).fetchone()
        if not r:
            return None, []
        return r[1], self.db.execute("SELECT start, end, text FROM segments WHERE media_id=? ORDER BY start", (r[0],)).fetchall()

    def prune(self):
        gone = [r[0] for r in self.db.execute("SELECT path FROM media") if not Path(r[0]).exists()]
        for g in gone:
            self.db.execute("DELETE FROM media WHERE path=?", (g,))
        self.db.commit()
        return gone


def hms(t):
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def media_link(path, start):
    return Path(path).resolve().as_uri() + f"#t={int(start)}"


def to_srt(segs):
    def ts(x):
        ms = int(round(x * 1000)); return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i}\n{ts(s)} --> {ts(e)}\n{t}\n" for i, (s, e, t) in enumerate(segs, 1))


def to_markdown(title, path, segs):
    lines = [f"# {title}", "", f"Source: `{path}`", ""]
    for s, e, t in segs:
        lines.append(f"- [{hms(s)}]({media_link(path, s)}) {t}")
    return "\n".join(lines) + "\n"
