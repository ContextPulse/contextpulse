# Phase 1 Transcribe-Only Pipeline

> One of several pipeline variants under `contextpulse_pipeline.pipelines.*`.
> See `pipelines/__init__.py` for the full library.

## Purpose

Take N raw audio files, return N independent per-source Whisper transcripts.
No grouping. No diarization. No speaker assumptions. No mastering.

This is the **clean foundation** for the Phase 1 voice-pipeline architecture
(sync derived from cross-source phrase matching, not assumed). The matcher
(`sync_matcher.py`) consumes these transcripts to compute sync offsets.

## Why a separate variant

The pre-existing `workers/spot_worker.py` runs the v0.1 "full pipeline"
(transcribe + diarize + master + mix in one job, with hardcoded
filename-based speaker mapping). That design is what produced the
"Frankenstein output" — speakers misaligned, channels collapsed, broken sync.

This variant intentionally drops everything past transcription so each
source's output stands alone and is composable into the new clean Phase 1
architecture.

## Components

```
phase1_transcribe/
├── worker.py     — runs on the spot instance; reads spec from S3, transcribes, uploads
├── submit.py     — runs locally; uploads inputs, launches spot, polls, downloads outputs
└── README.md     — this file
```

Boot script (sibling to existing): `infra/boot/boot_phase1_transcribe.sh`

## Spec format (S3 JSON)

```json
{
  "container": "ep-2026-04-26-josh-cashman",
  "model": "large-v3",
  "audio_s3_keys": ["phase1-input/<container>/audio/foo.wav", ...],
  "output_prefix": "phase1-output/<container>/transcripts/",
  "raw_sources_s3_key": "phase1-input/<container>/raw_sources.json"
}
```

## Outputs

For each input audio file, two files written to `s3://<bucket>/<output_prefix>/`:
- `{sha256[:16]}.json` — Whisper verbose JSON (matches the format from
  `transcribe_per_source.transcribe_raw_source()`)
- `{sha256[:16]}.txt` — plain text transcript

Plus `_DONE` marker on completion (or `_FAILED` with details on error).

## Cost / time (single g6.xlarge spot)

- Boot + deps install + model pre-download: ~10-12 min
- Transcription on L4 GPU at ~0.05-0.1 RTF: ~3-6 min per hour of audio
- Total for 6 hr audio: ~30-45 min, ~$0.50

## Cost / time (parallel, K instances)

`submit.py` supports `--parallelism K` to split the source list across K spot
instances. Total time becomes max(boot, single_source_time) ≈ 15-20 min for
4-way parallelism on the Josh hike fixture (6 hr audio split 4 ways).
