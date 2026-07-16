# Artifact schema

User-facing Vault content:

```text
<vault>/Sources/Videos/YYYY/YYYY-MM/YYYYMMDD-N-title [BV-p1]/
├── title.md
└── assets/
    └── YYYYMMDD-N-001-00m35s.png

<vault>/Indexes/
├── 视频资料库.md
└── Daily/YYYY-MM-DD.md
```

YouTube videos use the same layout with a `[YT-video_id]` folder marker. Source-neutral task metadata records `platform`, `source_kind`, `source_id`, and `source_key`; legacy `bvid` and `part` remain for Bilibili compatibility.

Operational content outside the Vault:

```text
<state>/
├── tasks.sqlite3
├── tasks/YYYYMMDD-N/
│   ├── metadata.json
│   ├── raw-transcript.json
│   ├── information-units.json
│   ├── outline.json
│   ├── clean-transcript.json
│   ├── coverage.json
│   ├── manuscript-checkpoint.json
│   ├── visual-manifest.json
│   └── job.json
├── work/YYYYMMDD-N/       # temporary; removed in finally or after 6 hours
├── exports/               # generated ZIPs; 24-hour cache
└── trash/YYYYMMDD-N/      # soft-deleted notes; 30-day retention
```

`raw-transcript.json` contains ordered `{id,start,end,text}` segments. Segments must pass monotonic-time and maximum-size gates.

`information-units.json` is the complete evidence-accounting layer. Every source ID appears in exactly one chronological unit. Kept units contain normalized meaning, concrete details, and high-confidence exact anchors. Dropped units require one legal reason: filler, false start, promotional request, or true repetition.

`outline.json` assigns every kept information unit exactly once to a concrete topic or workflow section. It is planned before final prose and is independent of transcript chunk boundaries.

`clean-transcript.json` contains section-composed paragraphs with expanded source IDs, timestamps, headings, spoken-text content, and an optional `visual_note`. DeepSeek returns chronological start boundaries; the CLI expands both source-to-unit and unit-to-paragraph ranges. `visual_note` may contain only OCR/vision evidence and renders as a labeled `画面补充` callout, so screen text is never misattributed to the speaker.

`coverage.json` records the information units, outline, global audit history, kept/dropped counts, warnings, and `quality_status`. `manuscript-checkpoint.json` allows a matching transcript and editing-schema signature to resume information extraction, outline planning, and completed sections. A failed semantic pass is retained only as an audit failure under the task state directory: it is not written to the Vault, indexed, bundled, or reported as completion.

Developer-only `vtm evaluate` writes `manuscript-preview.md`, `clean-transcript.json`, `coverage.json`, and a reusable checkpoint under `<source>/evaluations/<pipeline-version>/` by default. This path is not a task, Vault note, index entry, or downloadable artifact.

`visual-manifest.json` contains every reviewed frame with timestamp, nearby source IDs, OCR text, optional vision description, perceptual hash, quality metrics, `content_kind`, `extracted_markdown`, `evidence_confidence`, `evidence_completeness`, `information_density`, `keep_image`, and the actual `final_height` when a retained screenshot is replaced from the final stream. Text-replaceable frames have an empty final path only after complete coverage is proven; visually indispensable or information-dense frames retain a relative asset path.

Asset filenames use only the task key, sequence number, and timestamp. Descriptive Chinese text belongs in Markdown alt text rather than filesystem names. Missing or empty candidate captures are omitted without failing the note.

The download ZIP contains exactly one top-level note directory with its Markdown and `assets/`. Audit files are excluded unless explicitly requested with `--include-source`.
