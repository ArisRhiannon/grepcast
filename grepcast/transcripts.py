"""Parse existing transcripts (SRT, WebVTT, whisper JSON) and run local transcription engines."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
MEDIA = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
SIDECAR = (".srt", ".vtt", ".json")


def _sec(m):
    h, mi, s, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_cues(text):
    """SRT or VTT -> [(start, end, text)]."""
    segs = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.replace("\ufeff", ""))
    for b in blocks:
        lines = [l for l in b.strip().splitlines() if l.strip()]
        for i, l in enumerate(lines):
            if "-->" in l:
                a, _, z = l.partition("-->")
                ma, mz = TS.search(a), TS.search(z)
                if ma and mz:
                    body = " ".join(re.sub(r"<[^>]+>", "", x).strip() for x in lines[i + 1:])
                    if body:
                        segs.append((_sec(ma), _sec(mz), body))
                break
    return segs


def parse_whisper_json(text):
    d = json.loads(text)
    if isinstance(d, dict) and "segments" in d:  # openai-whisper / faster-whisper dumps
        return [(float(s["start"]), float(s["end"]), s["text"].strip()) for s in d["segments"] if s.get("text", "").strip()]
    if isinstance(d, dict) and "transcription" in d:  # whisper.cpp -oj
        out = []
        for s in d["transcription"]:
            off = s.get("offsets") or {}
            out.append((off.get("from", 0) / 1000, off.get("to", 0) / 1000, s.get("text", "").strip()))
        return [s for s in out if s[2]]
    raise ValueError("unrecognised JSON transcript")


def parse_file(path):
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix.lower() == ".json":
        return parse_whisper_json(text)
    return parse_cues(text)


def find_sidecar(media: Path):
    for ext in SIDECAR:
        for cand in (media.with_suffix(ext), media.with_name(media.name + ext)):
            if cand.exists() and cand != media:
                return cand
    return None


class Engine:
    name = "none"

    def transcribe(self, media: Path, language=None):
        raise NotImplementedError


class FasterWhisper(Engine):
    name = "faster-whisper"

    def __init__(self, model="small"):
        from faster_whisper import WhisperModel  # optional dependency
        self.model = WhisperModel(model, device="auto", compute_type="auto")

    def transcribe(self, media, language=None):
        segs, _ = self.model.transcribe(str(media), language=language, vad_filter=True)
        return [(s.start, s.end, s.text.strip()) for s in segs if s.text.strip()]


class WhisperCpp(Engine):
    name = "whisper.cpp"

    def __init__(self, model_path, binary=None):
        self.bin = binary or shutil.which("whisper-cli") or shutil.which("whisper-cpp") or shutil.which("main")
        if not self.bin:
            raise RuntimeError("whisper.cpp binary not found (whisper-cli); pass --whisper-bin")
        if not model_path or not Path(model_path).exists():
            raise RuntimeError("whisper.cpp needs --model pointing at a ggml model file")
        self.model = model_path
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg is required to convert audio for whisper.cpp")

    def transcribe(self, media, language=None):
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "a.wav")
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(media), "-ar", "16000", "-ac", "1", wav], check=True)
            args = [self.bin, "-m", self.model, "-f", wav, "-oj", "-of", os.path.join(td, "out")]
            if language:
                args += ["-l", language]
            subprocess.run(args, check=True, capture_output=True)
            return parse_whisper_json(Path(td, "out.json").read_text(encoding="utf-8"))


def make_engine(name, model=None, binary=None):
    if name in (None, "none"):
        return None
    if name == "faster-whisper":
        try:
            return FasterWhisper(model or "small")
        except ImportError:
            raise RuntimeError("faster-whisper is not installed: pip install faster-whisper")
    if name == "whisper.cpp":
        return WhisperCpp(model, binary)
    raise RuntimeError(f"unknown engine {name}")
