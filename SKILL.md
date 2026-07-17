---
name: video-to-detailed-manuscript
description: "Convert public Bilibili/YouTube/Douyin videos, Xiaohongshu/RedNote image notes, Zhihu answers/articles, and public generic-web/CSDN articles into detailed illustrated Obsidian manuscripts, then manage server-side results through natural Chinese. Always use for a supported source link or requests such as '提取这个视频', '提取这篇文章', '任务列表', '下载 1', '删除 1', '恢复 20260716-1', '终止任务', '协议自检', or '配置平台'. Produce an edited full-content note rather than a summary, verbatim transcript, or raw page copy: preserve every useful explanation, example, step, command, datum, qualifier, name, and caveat; remove fillers, page chrome, and genuine repetition; place useful video frames or original article images beside the matching passage. Also use to list, package, send, soft-delete, restore, cancel, self-check, configure, or inspect existing source-note tasks."
---

# Create and manage detailed video manuscripts

Use the bundled CLI as the only execution path. Resolve the directory containing this `SKILL.md` once and invoke the executable `scripts/vtm` launcher by its absolute path; never assume the Agent's current working directory is the Skill directory. Treat a supported Bilibili, YouTube, Douyin, Xiaohongshu/RedNote image-note, Zhihu answer/article, or public generic-web/CSDN URL by itself as a complete extraction request. Do not ask the user to repeat output requirements or remember commands.

## Output contract

Create a detailed, edited manuscript—not a short summary and not an unedited transcript.

- Preserve every meaningful claim, reason, explanation, example, step, command, code fragment, URL, number, condition, exception, comparison, warning, product/person name, and conclusion.
- For operational videos, preserve the entry path, exact button/option names, permissions or allowed items, purpose of each input, adjustable parameters, wait time, cost/quota, output fields, verification method, and failure conditions. Do not replace these with a generic statement that a step was performed.
- Preserve exact names and titles before generic descriptions: podcast/channel names, episode/article titles, people, products, commands, and numbered identifiers must not disappear into phrases such as “某一期” or “一个工具”.
- DeepSeek's final whole-document review compares the draft with the complete transcript and restores details that were generalized or omitted. Do not replace semantic review with mechanical word-retention ratios or arbitrary token gates.
- Merge adjacent repetition and compress wordy phrasing without losing details.
- Remove filler, false starts, promotional requests, and true repetition only.
- Do not add a second layer of editor commentary. Avoid “这说明…”, “价值在于…”, “适合用来…”, “形成互补…” or similar conclusions unless the speaker actually expressed them.
- Organize material into useful headings and natural paragraphs; use lists only for actual steps or enumerations.
- Apply a conservative text-first visual policy. Discard an image only when the frame is simple and OCR/vision demonstrably covers every useful visible item as text, a list, a Markdown table, fenced code, or LaTeX. Partial extraction, summaries of a dense screen, truncated OCR, conflicting recognition, or uncertain completeness always keep the original image.
- Keep the original frame when text cannot preserve the visual meaning or full information: flowcharts, architecture/structure diagrams, charts, UI execution states, before/after comparisons, spatial layouts, rich demonstrations, original figures from papers, long prompts, long articles, multi-card screens, multi-column lists, and other information-dense screens. Publish copyable visual text only when the classification is high-confidence, complete, and independently supported by adequate OCR quality. Partial/unknown or medium/low-confidence OCR and vision descriptions must not become prose or image alt text; retain the original with a neutral label instead. Prefer one strongest indispensable image per topic/paragraph, and remove near-identical frames aligned to adjacent paragraphs when they occur only seconds apart.
- Distribute the bounded paid-vision budget across the video's timeline before filling remaining slots by global score, so several high-scoring frames from one screen do not consume the whole budget. This distributes analysis opportunities; it never forces a final image quota.
- Classify all selected paragraph/frame evidence in one bounded text-model batch rather than one request per paragraph. If the batch fails or omits an item, retain the aligned original frame; do not retry every frame individually.
- Convert reliable formulas to `$...$` or `$$...$$`, code to fenced blocks with language/indentation, and tables to Markdown tables. If symbols, indentation, table relationships, or OCR confidence are uncertain, retain the image and mark the item for review instead of producing misleading copyable content.
- Place each retained image immediately after the passage it explains. Never create an image gallery before the manuscript.
- Treat speech and screen evidence as separate sources. The main paragraph may only contain spoken information. Put useful OCR/vision-only details in a clearly labeled `画面补充` callout; never attribute screenshot text to the speaker.
- Never fabricate missing speech, URLs, names, commands, or unreadable visual details.
- Treat the article plan as structure, not factual authority. Repair adjacent broken ASR fragments as one sentence, preserve negation/comparison direction, and prefer high-confidence visible spelling over phonetic gibberish. If exact recovery is uncertain, publish only the conservative source-supported meaning rather than a confident opposite claim.
- Do not force an edit when the speaker's original paragraph is already coherent, compact, and close to written prose. Editing quality is measured by clarity, structure, fidelity, and removal of filler—not by edit distance.

Use four whole-document DeepSeek editorial passes, matching the way the accepted golden manuscript was made, plus one small pre-writing terminology/断句 reconciliation when ASR suspects or visual evidence exist. Read [golden-style-example.md](references/golden-style-example.md) for prose density and style; learn its editorial decisions only and never copy its video facts:

1. Send the **complete timed transcript** to DeepSeek for an article plan only: real chapters, natural-paragraph boundaries, each paragraph's focus, must-keep details, attribution, ASR suspects, and time ranges where an image is needed for confirmation.
2. Before prose drafting, inspect the locally selected frames in those requested time ranges with Qwen, within the adaptive vision budget. Deterministically attach each planned paragraph's complete local subtitle range as `source_excerpt`; this is evidence placement, not semantic scoring. Then send the complete transcript, evidence-augmented plan, bounded visual evidence, and bundled golden-style reference to DeepSeek to write the detailed structured manuscript. Visual evidence may repair terms and clarify the spoken reference, but screen-only facts remain separate for the later `画面补充` callout.
   Before that writer call, run one bounded DeepSeek reconciliation that returns only a small correction map for phonetic English errors, adjacent broken clauses, and plan/evidence conflicts. It cannot write prose or change structure. Planner-marked suspects are checked first; within the same call, an unflagged uppercase phonetic token of four or more characters is also checked when exact English visible at the same timestamp conflicts with it. Evidence insufficiency returns unresolved. Visible spellings and recovered logical polarity are inserted into the plan as evidence corrections. The final manuscript must preserve each correction's minimal semantic anchors, but may phrase the surrounding sentence naturally; never require an entire repaired sentence to be copied verbatim.
3. Send the **complete timed transcript plus the complete first draft, evidence-augmented plan, and visual evidence** to DeepSeek for a detail pass. It reads every paragraph's `source_excerpt` and restores any explanation, example, step, parameter, number, condition, limitation, exact name, or conclusion that the structural draft compressed too aggressively, without restating information already present.
4. Send the complete transcript, visual evidence, golden-style reference, and detail-complete draft to DeepSeek for one final copyedit. It removes transcript-like wording and duplicated explanations, shortens sentences, fixes ASR residue and attribution, and improves paragraph flow without deleting independent details or adding outside knowledge.

Target the golden manuscript's prose density: one clear purpose per paragraph, direct verbs, usually two to five sentences, and lists only for real steps or enumerations. Use level-three subheadings for genuine tutorial steps, not for every paragraph. “Detailed” means preserving distinct information, not repeating the same point in an introduction, body, and conclusion. Remove likes/follows requests and pure outro speech.

If the final response still contains an explicit small set of presenter phrases or ASR residues confirmed by real regressions, retry that final copyedit once with the concrete phrase. The final editor must also reconcile inconsistent product names and file types against clearer occurrences elsewhere in the complete transcript, using a conservative generic phrase when exact recovery is uncertain. This is a narrow residue safeguard, not a semantic coverage gate; it must not reject a clean unchanged draft.

DeepSeek, not Python, decides semantic importance, chapter boundaries, paragraph boundaries, sentence compression, and wording. Python only validates the JSON shape, requires concrete chapters for a long video, expands each chronological `start_source_id` into a deterministic time range for image alignment, prioritizes planner-requested visual ranges within the fixed budget, checkpoints document passes, and handles media/storage/task operations. It must not reject a coherent manuscript because of character-retention ratios, unfamiliar abbreviations, or mechanically classified words. ASR uncertainty may reduce only the precision of a term or phrase; it must never justify deleting the surrounding source-supported context, case, question, reason, or conclusion.

Every source subtitle is assigned exactly once to one chronological paragraph range, but filler text is not forced into the published prose. The model returns only `start_source_id`; it never copies long ID lists. If a document response is malformed, retry only the response format once. Retry transient provider failures without printing response bodies or credentials. If the required AI document passes fail, keep only audit artifacts; never publish raw ASR or a partial draft.

Reject only objective pipeline failures such as invalid JSON, invalid source chronology, no chapter structure for a long video, an image gallery before the manuscript, or low-confidence visual text being published as prose. Do not add a multi-agent semantic tribunal or ask the Agent to write the note manually. Read [golden-quality.md](references/golden-quality.md) before changing the four editorial prompts or the bounded reconciliation prompt.

For development and regression work, use `scripts/vtm evaluate --source-dir <task-audit-dir>`. It reuses an existing `raw-transcript.json`, writes only an isolated preview/checkpoint under that audit directory (or `--output-dir`), downloads no media, reserves no task number, and never writes to the Vault. Do not use this developer command as a substitute for a user-requested `run`.

## User-visible progress

Hide tools, commands, searches, dependency checks, credentials, and raw logs. Send only stage changes emitted by the CLI:

1. `正在读取视频信息。` or `正在读取来源信息。`
2. `正在获取或识别字幕。` or `正在获取完整文字证据。`
3. `正在清理和重排完整文字稿。`
4. `正在提取并匹配关键画面。`
5. `正在生成 Obsidian 文稿。`
6. `处理完成。` or a terminal failure/cancellation line. A failed job must end with `[6/6 · FAILED]` plus a concise reason, confirmation that the task ended without a note, and confirmation that temporary media was cleaned. A cancelled job must end with `CANCELLED`. Never stop silently after an intermediate stage, and never present a review draft as completion.

Every line is prefixed with `[今日任务 N · YYYYMMDD-N]`, so concurrent jobs remain distinguishable. On Hermes Feishu/Lark, invoke the deterministic `submit` command instead of a long-running `run` tool call. `submit` creates an operating-system-detached child process and returns immediately; the CLI child sends all stage changes directly through the official `hermes send` command to the configured Feishu home channel. This releases the current Agent/chat turn even if Hermes would otherwise serialize terminal work.

Return immediately after `submit` returns `"status": "submitted"`. Before the first real CLI message, say only `已提交后台处理，任务编号将在第一条进度中显示。` Never guess a task number and never print examples containing `N`, `YYYYMMDD-N`, or `...`. Do not explain, summarize, retry, poll, wait for the process, or start another command. The user does not need `/background`. The reference 8 GB server permits two concurrent video workers. Additional jobs are accepted, receive their own task number, emit a `QUEUED` state, and start automatically when a slot is free; chat and task-management commands remain available meanwhile.

Persist every job's state and last stage in the task registry. For `任务状态`, `处理到哪了`, `有没有卡住`, or equivalent, run `tasks` and report every `queued` or `running` task with its stable ID, stage number, and stage message. `queued` is a normal intermediate state, not a failure. Do not answer with Hermes' generic Agent status when the user is asking about video-note jobs.

## Configure core services and source platforms

For `配置`, `配置平台`, `平台配置`, or `检查配置`, run:

```bash
<skill-root>/scripts/vtm configure
<skill-root>/scripts/vtm configure status
```

Relay the deterministic menu and boolean configuration state. The platform menu uses stable numbers, but the bare-number safety rule remains unchanged: a reply such as `1` is never an instruction. Require `配置 1`, `配置 B站`, or another explicit configuration verb, then run:

```bash
<skill-root>/scripts/vtm configure platform 1
```

Public, no-login acquisition is preferred. Explain each adapter's actual public mode, optional credential, official setup URL, and access limitations without promising that an API credential can read arbitrary content. An adapter marked uninstalled is a roadmap item, not a working capability.

Never ask the user to paste a Cookie, `SESSDATA`, API Key, client secret, access secret, OAuth token, or refresh token into Hermes, Feishu/Lark, another chat, a shell command argument, or a pipe. Chat delivery is not a secure secret channel. Direct the user to SSH into the server and run the exact interactive command returned by `configure platform`, for example:

```bash
<skill-root>/scripts/vtm configure secret bilibili_cookie
```

The CLI requires a TTY, hides input, writes only an allowlisted value to the project-specific `0600` secret file, and never returns the value. For `移除 <配置项>` first explain the affected capability and ask for confirmation; only after explicit confirmation run `configure remove <配置项> --confirm`. Configuration status and tests may report only presence, validity, expiry, or insufficient permission. They must never print a secret value, prefix, request header, provider response body, or the contents of an environment file.

## Extract a video

1. Run `doctor` only when deployment readiness is unknown. Never install packages or download models inside a user video job.
2. Run once:

```bash
<skill-root>/scripts/vtm submit --url '<supported-public-video-url>' --gateway-output --progress-target feishu
```

On Hermes messaging platforms, use exactly one short foreground terminal call for `submit`; the command itself detaches the real worker and releases the chat. Do not use `terminal(background=true)`, `process(wait)`, a long timeout, a shell-level `&`/`nohup`, or a second interpreter command. The launcher chooses one prepared Python runtime before the detached CLI reserves a task number. If submission itself fails, report that single failure; never retry with another Python executable.

Pass URLs as argv values rather than interpolating them into shell code. Useful explicit options are `--part`, `--no-visual`, `--max-frames`, `--cookies-file`, `--llm-model`, `--asr-backend`, and `--resume`.

If the same BVID and part already has a running or completed task, the CLI stops and returns its stable ID. Tell the user it already exists and offer download or regeneration. Only when the user explicitly says `重新生成` or equivalent, rerun with `--force`; the regenerated note receives a new daily and stable ID instead of overwriting history.

The same rule applies to `failed` and `cancelled` tasks. Never start another `run`, switch ASR backends, install a model, or add `--force` on your own after failure. Report the stored failure reason and wait for explicit user direction. One user extraction request must consume at most one task number.

The CLI reserves today's number atomically when the request starts. At completion, respond with title, status, transcript source, image count, and:

```text
今日任务 N（历史编号 YYYYMMDD-N）已暂存服务器。需要时说“下载 N”。
```

## Natural-language task management

Ordinary numbers always mean today's task list in `Asia/Shanghai`. They reset to 1 each day and are never reused within that day. Historical IDs never change.

A bare numeric message such as `1`, `2`, or `20260716-1` is never an instruction. Do not download, regenerate, delete, list, or otherwise mutate anything from a number alone. Execute task management only when the message contains an explicit action verb, such as `下载 1`, `重新生成 7`, `删除 3`, or `恢复 20260716-1`.

For `重新生成 7` or `重新生成任务 7`, resolve that task's stored URL and part from today's deterministic task registry, then invoke one detached `submit` with the resolved URL, original part, `--force`, `--gateway-output`, and `--progress-target feishu`. Do not ask the user to repeat the URL, do not regenerate the old directory in place, and do not start more than one replacement task.

### Protocol self-check

For `协议自检`, `完整自检`, or a request to verify what this Skill has learned, run:

```bash
<skill-root>/scripts/vtm contract
```

Relay that JSON faithfully and use the current `SKILL.md` only for its human-readable explanation. Do not invent a questionnaire, reinterpret fields from conversation history, consult user-profile memory, or use an older Skill version. A claimed `PASS` is invalid when any answer contradicts the deterministic contract.

### List today

For `任务列表`, `今天的任务`, or `查看任务`:

```bash
<skill-root>/scripts/vtm tasks
```

Render a compact Chinese list with daily number, title, and status. Do not expose JSON paths.

### List history

For `所有笔记列表`, `历史任务`, or a request about older notes:

```bash
<skill-root>/scripts/vtm tasks --all
```

Render history using stable IDs such as `20260716-1`.

### Download

For `下载 1`, resolve today's task. For `下载 20260716-1`, resolve history:

```bash
<skill-root>/scripts/vtm bundle --task 1 --send-target feishu
<skill-root>/scripts/vtm bundle --task 20260716-1 --send-target feishu
```

Run exactly once per requested ID. On Hermes Feishu/Lark, `--send-target feishu` packages the note and calls the official Hermes sender with `[[as_document]] MEDIA:<absolute-zip-path>` in the same deterministic CLI execution. After a successful result containing `"sent": true`, reply only that the attachment has been sent. Never print a server path and wait for the user to ask again; never make a second attachment call after success. For a non-Hermes Agent, omit `--send-target` and deliver the returned ZIP through that host's ordinary file mechanism.

The ZIP must contain exactly one top-level note folder holding one Markdown note and `assets/`; it must not contain the Vault root, unrelated notes, or audit files unless the user asks for source material. Only a task with status `complete` is downloadable. A legacy `needs_review` task must be regenerated successfully or explicitly deleted; do not send its poor draft as a finished note.

Never start an HTTP server, expose an IP address, paste the manuscript instead of the requested archive, or suggest SCP when file delivery is requested. If attachment upload fails, retain the ZIP and report its exact path and the upload error.

### Cancel

If the user says `终止任务`, `停止处理`, or equivalent while extraction is active, interrupt that extraction process immediately. Do not start a retry. The current CLI records an interrupted job as `cancelled`. If a job started by an older version still appears as `running` after the process has stopped, reconcile it once with:

```bash
<skill-root>/scripts/vtm cancel --latest-running
```

Report that the task was cancelled and temporary video/audio files were cleaned; it must never remain silently `running`.

### Delete and restore

For `删除 1`, show the title and stable ID, explain that it will enter a 30-day server recycle bin, and ask for confirmation. Do not run deletion yet.

For `确认删除 1`:

```bash
<skill-root>/scripts/vtm delete --task 1 --confirm
```

Deletion also applies to `failed` or `cancelled` tasks that have no note directory. Treat these as **record-only soft deletions**: move any audit artifacts plus the task record into the same 30-day recycle bin, hide the record from ordinary task lists, and do not fail merely because no Markdown directory exists.

For a request such as `删除除了 20260716-39 和 20260716-40 之外的所有历史任务`, never issue one delete command per task. First create one immutable plan using stable history IDs:

```bash
<skill-root>/scripts/vtm delete-many --all-history --keep 20260716-39 --keep 20260716-40
```

Show the returned delete count, retained IDs, automatically protected `queued`/`running` jobs, and the 30-day recycle-bin behavior. Ask once for confirmation. Store the returned `confirmation_token` in the conversation; do not recompute the list after the date changes. Only an explicit confirmation of that pending operation may execute:

```bash
<skill-root>/scripts/vtm delete-many --confirm-token <token> --send-target feishu
```

The CLI sends `RUNNING`, periodic numeric progress for larger sets, and `COMPLETE`/`PARTIAL` directly to Feishu. This is one deterministic operation and must not block or cancel video workers. A different user message, including a new video URL, is not confirmation; process the new request normally and leave the delete plan pending until it expires after 15 minutes.

For `恢复 20260716-1`:

```bash
<skill-root>/scripts/vtm restore --task 20260716-1
```

Restoring a record-only task restores its audit artifacts and its original `failed` or `cancelled` status. It does not invent a note, mark the task complete, or make it downloadable.

Do not invent a checkbox when the host lacks a generic interactive-card API. Natural language is the portable interface across Hermes, Codex, Claude Code, OpenCode, Kimi Code, and similar agents.

## Acquisition and processing policy

Use the bounded built-in chain:

1. For Bilibili, prefer native/manual or ordinary AI subtitle tracks; this downloads no audio. With an optional login cookie, next try Bilibili's timestamped AI-conclusion transcript.
2. For YouTube, prefer a creator-provided subtitle track, then an original-language automatic caption track. Do not silently choose an auto-translated track merely because it matches the manuscript language.
3. For generic public web/CSDN documents, extract the ordered main-article blocks and original images directly. Exclude navigation, comments, recommendations, login panels, and page chrome. Never display the internal document-order axis as a video timestamp. If a page requires login, payment, JavaScript rendering, or returns risk-control/HTTP 521, report the limitation; do not route content through a public proxy or third-party reader.
4. For Zhihu answers/articles, use the installed `zhihu-tui` client and preserve its raw HTML evidence rather than its text-only display payload. Try public read first. If Zhihu risk control rejects it, tell the user to SSH into the server and run `scripts/vtm configure secret zhihu_z_c0`; never request or accept `z_c0` in chat. Do not collect comments, paid excerpts, deleted content, or another account's inaccessible content.
5. For Douyin, use the installed public-share adapter. It requires no API Key or Cookie, adapts the Apache-2.0 Social Media Toolkit router-data parser, downloads the source video once, and reuses it for local ASR plus final frame recapture. If the share page has no native subtitle, this is expected. Do not fetch comments, profiles, private collections, deleted works, or image notes through the video adapter; do not fall back to public parsing proxies.
6. For Xiaohongshu/RedNote, use the installed public image-note adapter. Preserve all ordered body blocks, topics, and original images. When text-block and image counts match, keep one-to-one placement; otherwise retain the images as one ordered source gallery after the note body. Accept current, legacy, short, and share-text links. Do not use browser fingerprinting, private API signing, local browser Cookie extraction, comments, profiles, video notes, or public parsing proxies.
7. If a selected video platform exposes no transcript, download one audio stream and run the preloaded local ASR.
8. Use yt-dlp only after the direct player stream fails.
9. Use at most a 720p video-only stream for scene detection, OCR prefiltering, deduplication, and frame selection. A dedicated DeepSeek visual planner reads the complete timestamped transcript plus the approved article plan and decides which time ranges need visual evidence without modifying the prose plan. Inside those ranges, treat every locally distinct scene/slide change as a candidate rather than sampling once per minute. Paid vision review is therefore dynamic: its base budget starts at 6 and increases with video duration and subtitle-grounded requests, with a hard upper bound of 60 controlled by `--max-frames` and `VTM_MAX_VISION_FRAMES`. Six is not a required retained-image count: a visually sparse conversation may keep none, while a slide-dense technical video may use more. Preserve multiple distinct visual items for one paragraph when they carry different information, and remove adjacent near-duplicates. After deciding which images must remain, seek only those timestamps from the highest available stream up to 1080p and replace the analysis frames. Record the actual returned height; never claim 1080p when the platform only exposes a lower stream. Extract complete simple text/list/table/code/formula evidence into copyable Markdown/LaTeX first and remove its frame only after completeness is proven. Keep diagrams, architecture/process charts, paper figures, complex UI, partial OCR, dense prompts, and any visually irreplaceable evidence immediately after the matching passage.

Discard decorative or zero-information-gain frames even when they are temporally aligned: presenter avatars, stock/cartoon illustrations, watermarks, repeated subtitles/titles, simple labels with decorative icons, and a single arrow whose full meaning is already present in prose. A verbose vision description alone never makes a simple screen “dense.” Classification failure still keeps the aligned original conservatively.

Do not deduplicate requested text/PPT frames by layout alone. Two slides or progressive PPT/whiteboard/UI states may share the same template while adding one sentence, annotation, diagram edge, or completed result, including a negation that reverses the conclusion. Inside transcript-grounded text/list/table/code/formula/diagram/chart/process/ui/paper-figure/comparison ranges, retain smaller perceptual changes for OCR/vision review. Within one semantic section, replace a weaker same-template image only when the classified evidence gain is strictly stronger; do not always choose the last frame, and preserve equal-strength stages, real before/after comparisons, and distinct visuals.

Keep paid vision work proportional to the video: after transcript-grounded planning and local scene/OCR filtering, review at most roughly two distinct candidates per video minute, with a minimum allowance of six and the existing hard ceiling of sixty. This is a cost ceiling, not a sampling target; visually sparse videos use fewer. Short, low/medium-density text or list screens whose useful content has been transcribed become text-only evidence even if the classifier conservatively labels completeness as partial. Dense prompts, code, formulas, tables, diagrams, UI states, and original figures retain the image when text cannot replace it.

Within that unchanged vision budget, narrow ASR-suspect ranges have priority and may use their first and last distinct frame. This captures consecutive cards such as an identifier followed by a negated conclusion without adding another model call or raising the budget. Ordinary visual ranges then receive representatives and any remaining budget is distributed across requested scenes.

The mainland ASR deployment uses Paraformer + FSMN-VAD + CT-Punctuation. A giant multi-minute segment must be resegmented or rejected. The reference low-cost deployment uses DeepSeek V4 Flash for text and Qwen3-VL-Flash for requested visual frames, with local OCR as the free prefilter. These are configuration defaults, not pipeline dependencies: any OpenAI-compatible model with sufficient Chinese instruction following and structured-output reliability can replace the text model, and any compatible image-input model can replace the visual model. A single multimodal provider may serve both roles through the separate `VTM_LLM_*` and `VTM_VISION_*` settings. Changing models does not disable the Skill contract or its release gates, but output quality must be rechecked on a known video because model behavior is not identical.

## Storage and cleanup

Default Vault: `VTM_VAULT` or `~/ObsidianVault`.

User-facing notes:

```text
ObsidianVault/Sources/Videos/YYYY/YYYY-MM/YYYYMMDD-N-title [BV]/
  title.md
  assets/
ObsidianVault/Sources/Documents/YYYY/YYYY-MM/YYYYMMDD-N-title [WEB-source]/
  title.md
  assets/
ObsidianVault/Indexes/视频资料库.md
ObsidianVault/Indexes/来源资料库.md
ObsidianVault/Indexes/Daily/YYYY-MM-DD.md
```

Name image assets with short ASCII identifiers only: `YYYYMMDD-N-序号-时间戳.png`. Keep descriptive Chinese text in the Markdown alt text, never in the filename. If a candidate capture is missing or empty, skip that frame instead of failing the whole note.

Operational state stays outside the Vault at `VTM_STATE_DIR` or `~/.local/share/video-manuscript/`: SQLite registry, raw transcript/audit, temporary media, exports, and recycle bin.

Video, audio, WAV, and temporary ASR files are removed in `finally` after success, failure, or cancellation. Stale work older than 6 hours is removed on the next CLI invocation; generated ZIP files expire after 24 hours; soft-deleted notes expire after 30 days. Reject new processing when disk free space is below 5 GB. Retain no source media unless the user explicitly invokes the debugging-only `--keep-video` option.

## Secrets and deployment

Load only allowlisted variables from the deployment environment. Never read, print, search for, or test secret values in chat or logs.

- Text: `VTM_LLM_API_KEY` or `DEEPSEEK_API_KEY`, optional `VTM_LLM_BASE_URL`, `VTM_LLM_MODEL`.
- Vision: `VTM_VISION_API_KEY`, `VTM_VISION_BASE_URL`, `VTM_VISION_MODEL`.
- Optional Bilibili login: `BILIBILI_COOKIE` or `--cookies-file`.
- Optional outbound source proxy for a server that cannot reach a supported public platform directly: `VTM_SOURCE_PROXY`. Configure it through hidden TTY input; never paste a credential-bearing proxy URL into chat.
- Storage: `VTM_VAULT`, `VTM_STATE_DIR`, `VTM_TIMEZONE`.

Read [configuration.md](references/configuration.md) for model and server setup, [agent-installation.md](references/agent-installation.md) for Agent integration, [artifact-schema.md](references/artifact-schema.md) when extending files, and [design-research.md](references/design-research.md) before changing acquisition or ASR behavior.
