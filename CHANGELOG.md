# Changelog

## Unreleased — multi-source foundation

- Adds the source-adapter discovery protocol and wraps the frozen Bilibili client without changing `pipeline_version: 1.0.0`.
- Adds a deterministic core/platform configuration menu with explicit installed-versus-planned capability status.
- Adds hidden TTY-only secret entry, an allowlisted project-specific `0600` secret store, and value-free configuration status.
- Keeps bare numeric replies non-operative and prohibits Cookie, API Key, Secret, or Token delivery through Agent chats.
- Adds public YouTube video support with canonical URL identity, manual/original-language subtitle priority, audio-ASR fallback, dynamic visuals, and retained-frame high-resolution seeking.
- Generalizes task identity to `platform/source_kind/source_id/source_key` while preserving legacy BVID fields and Bilibili duplicate behavior.
- Adds bounded source-network timeouts and an optional protected outbound proxy setting for restricted server networks.
- Adds public generic-web/CSDN document support with ordered article blocks, headings, lists, tables, code, original-image placement, compact inspection, and no fake video timestamps.
- Reuses Apache-2.0 `readability-lxml` for main-article selection, with a deterministic structure-fidelity fallback when upstream cleanup would drop tables, code, or original images.
- Reuses BSD-3-Clause `extruct` for standard JSON-LD article title, author, publication time, and publisher metadata instead of adding site-specific metadata scrapers.
- Rejects credential-bearing, non-HTTP(S), localhost, private, and reserved source URLs; bounds HTML/image response sizes and excludes navigation, comments, recommendations, and other page chrome.

## 1.1.0 — deployment packaging

- Adds a dry-run-capable Debian/Ubuntu installer for Hermes, Codex, and explicit Agent Skill directories.
- Installs the CPU runtime and delegates all model preparation to the existing deterministic `prepare-asr` command.
- Documents the exact Paraformer, FSMN-VAD, and CT-Punctuation ModelScope IDs and official model cards.
- Does not change the frozen manuscript, visual, task, or storage pipeline.

## 1.0.0 — first public Bilibili release

This release freezes the Bilibili manuscript pipeline after acceptance testing with multiple real videos.

- Produces structured, detail-preserving edited manuscripts instead of summaries or raw subtitles.
- Uses complete-transcript planning, golden-style writing, detail restoration, and final concise copyediting.
- Reconciles bounded ASR/terminology suspects without turning mechanical checks into the writer.
- Plans visual evidence from transcript semantics, locally detects and deduplicates candidate frames, and limits paid vision calls.
- Converts complete simple text evidence to Markdown while retaining dense prompts, diagrams, paper figures, complex interfaces, and partial OCR as inline images.
- Re-captures retained images from the highest available stream up to the requested 1080p.
- Runs detached background jobs with two-worker queuing, per-task six-stage progress, cancellation, one-shot Feishu ZIP delivery, soft deletion, recovery, and media cleanup.
- Keeps credentials, operational state, Vault content, exports, source media, and model weights outside the repository.

Future source adapters must preserve this release's Bilibili output and reuse the common manuscript core.
