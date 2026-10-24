# grepcast

**grep for everything you've ever watched or listened to, without uploading any of it.** grepcast turns a folder of saved talks, lectures, videos and podcasts into a local transcript library: it transcribes on your machine (faster-whisper or whisper.cpp) or imports subtitles you already have, indexes every segment with timestamps in SQLite FTS5, and `grepcast search` jumps you straight to the moment something was said.

[![CI](https://github.com/ArisRhiannon/grepcast/actions/workflows/ci.yml/badge.svg)](https://github.com/ArisRhiannon/grepcast/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python >= 3.9](https://img.shields.io/badge/python-%3E%3D3.9-blue)
![Core: zero dependencies](https://img.shields.io/badge/core%20deps-0-brightgreen)
![Local only](https://img.shields.io/badge/uploads-none-informational)

```bash
pipx install "grepcast[whisper] @ git+https://github.com/ArisRhiannon/grepcast"
grepcast ingest ~/Videos/talks ~/Podcasts --engine faster-whisper --model small
grepcast search "rotate the npm tokens"
```

## Why

whisper.cpp, MacWhisper and yt-dlp scripts make *one* transcript. What's missing is the library: incremental ingest, an index, and search that keeps the audio and the timestamp together. grepcast is that layer, and it leaves your media files exactly where they are.

## Usage

```bash
grepcast ingest ~/Videos                          # import existing .srt/.vtt/.json sidecars only
grepcast ingest ~/Podcasts --engine faster-whisper --model medium --language es
grepcast ingest talk.mkv --engine whisper.cpp --model ~/models/ggml-base.bin
grepcast search lockfile defence                  # all words (phrase-insensitive)
grepcast search '"exact phrase" OR worm*' -n 50   # FTS5 query syntax passes through
grepcast search -C 2 kubernetes                   # with 2 neighbouring segments of context
grepcast search --json tokens | jq .
grepcast list
grepcast export "my-talk" --format md > my-talk.md   # timestamped Markdown links; also srt, txt
grepcast prune                                    # forget files that were deleted
```

Example search output (illustrative):

```
supply-chain-keynote @ 1:05  [Lockfiles] are your first line of defence.
    file:///home/me/Videos/talks/supply-chain-keynote.mp4#t=65
```

Open the link in a player that supports media fragments, or run `mpv --start=65 <file>` / `vlc --start-time=65 <file>`.

### How ingest works

1. Walk the paths for media (`mp3 m4a wav flac ogg opus aac mp4 mkv webm mov m4v avi`).
2. Skip anything already indexed with the same size and mtime (use `--force` to redo).
3. If a sidecar exists (`talk.srt`, `talk.vtt`, `talk.json`, or `talk.mp4.vtt`), import it. Otherwise transcribe with `--engine` and save `talk.srt` next to the file (disable with `--no-sidecar`).
4. With no engine and no sidecar, the file is reported as *pending*.

Whisper JSON from openai-whisper / faster-whisper (`segments`) and whisper.cpp (`-oj`) are both understood. Search is accent-insensitive (`gracias` matches `grácias`).

The database lives at `~/.local/share/grepcast/library.db` (override with `--db` or `GREPCAST_DB`).

## Engines

| Engine | Install | Notes |
|---|---|---|
| `faster-whisper` | `pip install faster-whisper` (or the `[whisper]` extra) | downloads the chosen model on first use; GPU if available |
| `whisper.cpp` | build or install `whisper-cli`, download a ggml model, have `ffmpeg` on PATH | pass `--model /path/ggml-*.bin` |
| `none` (default) | nothing | imports existing subtitles only |

## Limits

- Transcription quality and speed are whatever your Whisper model gives you; grepcast does no diarisation (no speaker names) yet.
- CI tests cover parsing, indexing, search and the CLI with a fake engine. The faster-whisper and whisper.cpp adapters are thin wrappers that are **not exercised in CI** (they need models); please report problems.
- Change detection is size + mtime; moving a file re-indexes it under the new path (run `prune` to drop the old entry).
- No watch daemon in v0.1: run `grepcast ingest` from cron/systemd/launchd for a "watch folder".

## License

[MIT](LICENSE)
