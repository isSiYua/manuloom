# Multi-source roadmap

Version 1.0.0 freezes the first public Bilibili manuscript pipeline. New sources should enter through adapters and reuse the same transcript/evidence, editing, visual, task, storage, and export layers. Do not fork the manuscript core for each website.

## Adapter boundary

Each source adapter should provide the smallest applicable subset of:

- `can_handle(url)` and a canonical URL;
- title, author, publication time, source ID, and access metadata;
- clean text or timed subtitle segments;
- one authorized audio stream when local ASR is required;
- one analysis video stream and a higher-quality retained-frame stream when visuals exist;
- stable source links and evidence timestamps.

The common pipeline converts those results into chronological text evidence and optional visual evidence. Text-only pages skip ASR and frame extraction. Video sources follow the existing subtitle-first order.

## Planned adapters

### YouTube

Implemented for public videos. It uses public metadata and creator-provided subtitle tracks first, then original-language automatically generated captions. It acquires one audio stream only when subtitles are unavailable and obtains video frames only for transcript-requested visual evidence. Restricted server networks may require a privately operated outbound source proxy.

### Generic web pages

Extract the main article rather than browser chrome, navigation, comments, or recommendations. Preserve headings, lists, tables, code, formulas, captions, source links, author, and publication time. Retain original figures when their spatial or visual meaning cannot be represented faithfully as text.

### Zhihu

Treat public articles and answers as structured web documents. Public video follows the video pipeline when an authorized media URL is available. Login-only, paid, deleted, or risk-controlled content should return an explicit access limitation instead of attempting a bypass.

### Douyin and Xiaohongshu

Treat these as optional, best-effort adapters because share URLs, dynamic rendering, login requirements, and risk control change frequently. Process only content available through the user's authorized session. Prefer post text and platform captions; download media only when necessary for ASR or indispensable visual evidence.

## One multimodal model

A single image-capable provider/model may already serve both text and vision by assigning the same API key, base URL, and model name to `VTM_LLM_*` and `VTM_VISION_*`. Keep the logical calls separate: manuscript editing and frame inspection need different prompts, token ceilings, retries, caching, and failure handling. This preserves model portability while allowing a future DeepSeek multimodal release to replace the current DeepSeek/Qwen split without redesigning the Skill.

## Release order

1. Freeze and publish the Bilibili core with reproducible tests and no credentials or private artifacts.
2. Add the source-adapter protocol without changing Bilibili output.
3. Implement YouTube (complete) and generic web-page adapters with fixture-based regression tests.
4. Add Zhihu public pages.
5. Evaluate Douyin and Xiaohongshu as optional integrations against platform stability and account safety.
