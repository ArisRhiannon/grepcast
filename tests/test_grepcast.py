import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from grepcast.cli import main
from grepcast.library import Library, hms, to_markdown, to_srt
from grepcast.transcripts import Engine, find_sidecar, parse_cues, parse_whisper_json

SRT = """1
00:00:01,000 --> 00:00:04,500
Welcome to the talk about supply chain attacks.

2
00:01:05,250 --> 00:01:09,000
Lockfiles are your first line of defence.

3
01:02:03,000 --> 01:02:05,000
Gracias por venir, nos vemos pronto.
"""
VTT = "WEBVTT\n\n00:00.000 --> 00:02.000\n<v Speaker>Hola <b>mundo</b>\n\nNOTE x\n\n00:02.500 --> 00:04.000\nsegunda línea\n"


def tree(d):
    root = Path(tempfile.mkdtemp())
    for k, v in d.items():
        (root / k).parent.mkdir(parents=True, exist_ok=True)
        (root / k).write_bytes(v) if isinstance(v, bytes) else (root / k).write_text(v, encoding="utf-8")
    return root


class FakeEngine(Engine):
    name = "fake"

    def __init__(self):
        self.calls = 0

    def transcribe(self, media, language=None):
        self.calls += 1
        return [(0.0, 1.0, f"fake transcript of {media.stem} in {language or 'auto'}")]


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(args)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Parsing(unittest.TestCase):
    def test_srt(self):
        s = parse_cues(SRT)
        self.assertEqual(len(s), 3)
        self.assertEqual(s[1][0], 65.25)
        self.assertEqual(s[2][0], 3723.0)

    def test_vtt_strips_tags(self):
        self.assertEqual(parse_cues(VTT), [(0.0, 2.0, "Hola mundo"), (2.5, 4.0, "segunda línea")])

    def test_whisper_json_formats(self):
        a = parse_whisper_json(json.dumps({"segments": [{"start": 1, "end": 2, "text": " hi "}]}))
        b = parse_whisper_json(json.dumps({"transcription": [{"offsets": {"from": 1500, "to": 3000}, "text": " yo"}]}))
        self.assertEqual(a, [(1.0, 2.0, "hi")]); self.assertEqual(b, [(1.5, 3.0, "yo")])
        with self.assertRaises(ValueError):
            parse_whisper_json("[]")

    def test_sidecar_names(self):
        r = tree({"a.mp3": b"x", "a.srt": SRT, "b.mp4": b"x", "b.mp4.vtt": VTT})
        self.assertEqual(find_sidecar(r / "a.mp3").name, "a.srt")
        self.assertEqual(find_sidecar(r / "b.mp4").name, "b.mp4.vtt")

    def test_helpers(self):
        self.assertEqual(hms(65), "1:05"); self.assertEqual(hms(3723), "1:02:03")
        self.assertIn("00:01:05,250 --> 00:01:09,000", to_srt(parse_cues(SRT)))
        self.assertIn("#t=65", to_markdown("t", "/x/a.mp3", parse_cues(SRT)))


class Lib(unittest.TestCase):
    def setUp(self):
        self.dir = tree({"talks/sc.mp4": b"video", "talks/sc.srt": SRT, "pods/ep1.mp3": b"audio", "pods/notes.txt": "ignore"})
        self.lib = Library(self.dir / "lib.db")

    def tearDown(self):
        self.lib.close()

    def test_ingest_sidecar_and_pending(self):
        st = self.lib.ingest([self.dir], log=lambda m: None)
        self.assertEqual((st["added"], st["pending"]), (1, 1))

    def test_ingest_with_engine_writes_srt(self):
        eng = FakeEngine()
        self.lib.ingest([self.dir], engine=eng, language="es", log=lambda m: None)
        self.assertEqual(eng.calls, 1)
        self.assertTrue((self.dir / "pods/ep1.srt").exists())
        self.assertEqual(self.lib.search("fake")[0]["title"], "ep1")

    def test_incremental(self):
        eng = FakeEngine()
        self.lib.ingest([self.dir], engine=eng, log=lambda m: None, write_sidecar=False)
        st = self.lib.ingest([self.dir], engine=eng, log=lambda m: None, write_sidecar=False)
        self.assertEqual((st["added"], st["skipped"]), (0, 2))
        p = self.dir / "pods/ep1.mp3"; p.write_bytes(b"changed audio"); os.utime(p, (time.time() + 5, time.time() + 5))
        st = self.lib.ingest([self.dir], engine=eng, log=lambda m: None, write_sidecar=False)
        self.assertEqual(st["added"], 1)
        self.assertEqual(len(self.lib.items()), 2)

    def test_search_phrase_diacritics_and_timestamps(self):
        self.lib.ingest([self.dir], log=lambda m: None)
        h = self.lib.search("lockfiles defence")
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["start"], 65.25)
        self.assertIn("[Lockfiles]", h[0]["snippet"])
        self.assertTrue(h[0]["link"].endswith("#t=65"))
        self.assertEqual(len(self.lib.search("gracias")), 1)
        self.assertEqual(len(self.lib.search("Grácias")), 1)  # diacritics folded
        self.assertEqual(self.lib.search("nonexistentword"), [])

    def test_fts_operators_and_context(self):
        self.lib.ingest([self.dir], log=lambda m: None)
        self.assertEqual(len(self.lib.search("supply OR gracias")), 2)
        self.assertIn("Welcome", self.lib.search("lockfiles", context=1)[0]["context"])

    def test_prune(self):
        self.lib.ingest([self.dir], log=lambda m: None)
        (self.dir / "talks/sc.mp4").unlink()
        self.assertEqual(len(self.lib.prune()), 1)
        self.assertEqual(self.lib.search("lockfiles"), [])

    def test_bad_engine_output_counts_as_failed(self):
        class Boom(Engine):
            name = "boom"
            def transcribe(self, media, language=None):
                raise RuntimeError("model crashed")
        st = self.lib.ingest([self.dir], engine=Boom(), log=lambda m: None)
        self.assertEqual(st["failed"], 1)


class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = tree({"a/talk.mp4": b"v", "a/talk.vtt": VTT})
        self.db = str(self.dir / "l.db")

    def test_help(self):
        code, out, _ = run(["--help"])
        self.assertEqual(code, 0); self.assertIn("searchable transcript", out)

    def test_ingest_search_list_export(self):
        self.assertEqual(run(["--db", self.db, "ingest", str(self.dir)])[0], 0)
        code, out, _ = run(["--db", self.db, "search", "mundo"])
        self.assertEqual(code, 0); self.assertIn("talk @ 0:00", out)
        self.assertEqual(run(["--db", self.db, "search", "zzz"])[0], 1)
        self.assertIn("2 segments", run(["--db", self.db, "list"])[1])
        code, out, _ = run(["--db", self.db, "export", "talk"])
        self.assertEqual(code, 0); self.assertIn("- [0:02](", out)
        code, out, _ = run(["--db", self.db, "export", str(self.dir / "a/talk.mp4"), "--format", "txt"])
        self.assertEqual(out.strip().splitlines(), ["Hola mundo", "segunda línea"])
        code, out, _ = run(["--db", self.db, "search", "--json", "segunda"])
        self.assertEqual(json.loads(out)[0]["start"], 2.5)

    def test_errors(self):
        self.assertEqual(run(["--db", self.db, "ingest", "/no/such"])[0], 2)
        self.assertEqual(run(["--db", self.db, "export", "nothing"])[0], 2)
        self.assertEqual(run(["--db", self.db, "ingest", str(self.dir), "--engine", "whisper.cpp", "--model", "/no/model.bin"])[0], 2)


if __name__ == "__main__":
    unittest.main()
