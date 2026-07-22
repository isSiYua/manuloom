# Changelog

## Unreleased

- Makes the Chinese README the default repository landing page and moves the English version to `README.en.md`.
- Removes the decorative launch banner, README badges, and generated browser-extension icons for a cleaner text-first presentation.

## 1.2.0 — ManuLoom public launch

- Rebrands the public project as ManuLoom while retaining the compatible `scripts/vtm` launcher and `VTM_*` runtime variables.
- Adds an original project banner, bilingual launch documentation, a copyright-safe synthetic reference note, contribution templates, security policy, and public CI.
- Adds a loopback-only local web bridge protected by a mode-`0600` pairing token.
- Adds a Manifest V3 browser extension with only `activeTab`, `storage`, and loopback host permissions; it contains no model or platform credential.
- Documents one-Skill Hermes + Feishu deployment and a lowest-cost subtitle/document-first server profile.
- Does not add a model call, change the manuscript prompts, or alter the pipeline version.

## Multi-source foundation

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
- Adds Zhihu answer/article support through the Apache-2.0 `zhihu-tui` client, reusing the document manuscript core while preserving links, inline code, LaTeX, and original images.
- Attempts Zhihu public reads first and reports an actionable limitation when risk control requires the user's hidden `z_c0`; no shared Cookie or unofficial public key is embedded.
- Adds public Douyin video support by adapting the Apache-2.0 `social-post-extractor-mcp` share-page parser, then reusing the existing one-download local ASR and dynamic visual pipeline.
- Validates Douyin redirects and CDN hosts, caps responses at 8MB/2GB, stores no expiring signed media URL in durable metadata, and requires no API key or Cookie for public-share mode.
- Adds public Xiaohongshu/RedNote image-note support by adapting the Apache-2.0 Social Media Toolkit initial-state parser, preserving ordered body blocks, topics, and all original images without a key or Cookie.
- Supports `xiaohongshu.com`, the current `rednote.com` domain, `xhslink.com`, and share text; rejects video notes, login/captcha/deleted/risk-control pages, and untrusted image hosts.
- Normalizes equivalent full-width/half-width punctuation in ASR reconciliation anchors and conservatively drops an unsupported optional correction instead of failing the complete manuscript.
- Keeps an overview and its immediate same-topic expansion in one semantic section, and preserves small progressive changes in requested PPT/whiteboard/diagram windows so a more complete later frame can win normal evidence scoring.

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
