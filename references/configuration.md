# Configuration

## Runtime

- Python 3.10+
- `ffmpeg` and `ffprobe`
- `yt-dlp` Python package or executable
- Recommended FunASR Paraformer + FSMN-VAD + CT-Punctuation for videos without native subtitles on mainland servers
- Optional pre-cached `faster-whisper` fallback
- Optional `tesseract` executable for frame OCR

Install the small default dependency set:

```bash
python3 -m pip install -r scripts/requirements.txt
```

Install the mainland-friendly local ASR separately because it is large, then prepare the model once:

```bash
python3 -m pip install -r scripts/requirements-asr-cn.txt
scripts/vtm prepare-asr
```

`prepare-asr` initializes all three ModelScope models and writes a versioned readiness marker. Re-run it after upgrading from the old Paraformer-only release. Video jobs never install dependencies or download model weights. Keep `requirements-asr.txt` only for an already-cached faster-whisper fallback.

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `BILIBILI_COOKIE` | Complete Bilibili Cookie header; commonly includes `SESSDATA` | unset |
| `VTM_LLM_API_KEY` | Text editing API key | `DEEPSEEK_API_KEY` |
| `VTM_LLM_BASE_URL` | OpenAI-compatible base URL | `https://api.deepseek.com` |
| `VTM_LLM_MODEL` | Text editing model | `deepseek-v4-flash` |
| `VTM_VAULT` | Default Obsidian Vault directory | `~/ObsidianVault` |
| `VTM_STATE_DIR` | SQLite, audit files, temporary media, exports, and trash | `~/.local/share/video-manuscript` |
| `VTM_TIMEZONE` | Daily-number timezone | `Asia/Shanghai` |
| `VTM_ASR_BACKEND` | ASR selection: `auto`, `funasr`, or `faster-whisper` | `auto` |
| `VTM_ASR_MODEL` | Name/path of an already-cached faster-whisper model | `medium` |
| `VTM_VISUAL_HEIGHT` | Maximum height of the video-only screenshot stream | `720` |
| `VTM_FINAL_VISUAL_HEIGHT` | Requested height for final retained screenshots; actual availability depends on the Bilibili account | `1080` |
| `VTM_VISION_API_KEY` | Optional vision API key | unset |
| `VTM_VISION_BASE_URL` | Optional OpenAI-compatible vision base URL | unset |
| `VTM_VISION_MODEL` | Optional vision model | unset |
| `VTM_MAX_VISION_FRAMES` | Hard cost ceiling for distinct AI-requested frames sent to the optional vision model; not a sampling target | `60` |
| `VTM_MAX_CONCURRENT_JOBS` | Maximum detached video workers on one server; values above `4` are clamped | `2` |
| `VTM_PROGRESS_TARGET` | Optional Hermes one-shot delivery target, such as `feishu` or `feishu:chat_id` | unset |
| `VTM_PYTHON` | Prepared Python 3.10+ interpreter used by `scripts/vtm`; set in the service environment, not the CLI env file | auto-detect |

Keep secrets in the service manager's secret store or an environment file readable only by the service account. Do not put them in `SKILL.md`, Obsidian, job JSON, logs, or Agent prompts.

For the inexpensive split used by the reference deployment, keep DeepSeek V4 Flash as the text model and configure Qwen3-VL-Flash only for transcript-requested, locally deduplicated frames:

```text
VTM_VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
VTM_VISION_MODEL=qwen3-vl-flash
VTM_MAX_VISION_FRAMES=60
```

Store `VTM_VISION_API_KEY` in the protected environment file. Vision is optional: without it, local Tesseract OCR and transcript timing still select frames. The value `60` is only a hard ceiling. The actual count is determined by DeepSeek's visual requests and distinct scene/slide changes inside those ranges; unrelated scenes and near-duplicates are not sent.

## Switching OpenAI-compatible models

Text and vision providers are independent and selected entirely through environment variables. The Skill contract and the four-stage manuscript workflow do not depend on DeepSeek or Qwen names. A provider with one multimodal model can serve both logical roles by using the same credential and model name in both sets of variables:

```text
VTM_LLM_API_KEY=<OpenAI API key>
VTM_LLM_BASE_URL=https://api.openai.com/v1
VTM_LLM_MODEL=<an image-capable OpenAI API model>

VTM_VISION_API_KEY=<the same OpenAI API key>
VTM_VISION_BASE_URL=https://api.openai.com/v1
VTM_VISION_MODEL=<the same image-capable OpenAI API model>
```

Use an API key issued for the API account; a consumer chat subscription is not an API credential. Select a currently supported image-capable model from the provider's official model documentation and verify that its endpoint supports the request format used by this Skill. Do not copy a model name from this repository because provider catalogs and snapshots change independently of the Skill.

Using one model does not collapse the pipeline into one large request. Text editing and visual inspection retain separate prompts, budgets, retries, and cache behavior. This keeps costs bounded and allows either role to be switched independently later. If a future DeepSeek multimodal release meets both requirements, point `VTM_LLM_*` and `VTM_VISION_*` at that same release.

Switching providers does not bypass the Skill: acquisition, outline, golden-style writing, detail restoration, final copyedit, visual placement, cleanup, and release gates remain active. Output quality is not mathematically invariant across models because instruction following, Chinese editing, OCR/vision accuracy, context limits, and structured-output reliability differ. Test one known video before changing the production default. When a provider offers dated snapshots, prefer a snapshot for reproducible results.

The CLI safely loads only its allowlisted variables from `~/.hermes/.env` when present, so an Agent must never inspect or print that file. Set `VTM_ENV_FILE` to use a different protected environment file.

## Interactive configuration center

Use the deterministic menu to inspect core and platform readiness without exposing values:

```bash
scripts/vtm configure
scripts/vtm configure status
scripts/vtm configure platform 1
```

The menu currently numbers Bilibili as 1, YouTube as 2, Zhihu as 3, generic web/CSDN as 4, Douyin as 5, and Xiaohongshu as 6. `adapter_installed` is authoritative: a listed roadmap platform is not usable until its adapter is implemented and tested.

Never paste a Cookie, API Key, Secret, or Token into Hermes or another chat. From an SSH terminal, use hidden interactive input instead:

```bash
scripts/vtm configure secret bilibili_cookie
```

The dedicated store defaults to `~/.config/video-to-detailed-manuscript/secrets.env`; its directory is `0700`, the file and lock are `0600`, and writes are atomic. It contains only allowlisted project variables. The loader reads this project file first and fills still-missing allowlisted values from the legacy `~/.hermes/.env`; it never loads unrelated environment entries. Status output contains booleans and labels only.

Public acquisition is preferred. Bilibili public videos need no credential; its complete Cookie header is optional. YouTube's API Key is an optional future metadata/API enhancement, not a universal subtitle credential. Public Zhihu pages are planned without a key, while its official Access Secret enables only the APIs actually granted. Douyin official client credentials apply only to reviewed applications and authorized scopes. The currently documented Xiaohongshu merchant/mini-app credentials are not treated as a general public-note reading API.

## Server baseline

Use a mainland China ECS in Shanghai or Hangzhou. Start with 4 vCPU, 8 GB RAM, 40–80 GB SSD, Ubuntu 24.04, no GPU, and one CPU-ASR job; test two only after observing memory. Use Feishu WebSocket mode so the service does not need a public web endpoint.

Run native-subtitle jobs without ASR or audio download. For missing subtitles, use the preloaded CPU Paraformer model. It provides timestamps needed to align frames and avoids the Hugging Face/Xet route that can fail from mainland networks. The reference 4-vCPU/8-GB deployment permits at most two workers. Start with one worker when most videos require CPU ASR; use two after observing memory, or when jobs usually have native subtitles. Additional submitted tasks remain queued.

## Bilibili acquisition

The pipeline requests native subtitles first. When a login cookie is configured, it can also use Bilibili's optional AI-conclusion transcript if that video exposes timestamped subtitle fragments. When ASR is necessary, it asks Bilibili's public player API for an audio stream and uses yt-dlp only as a fallback. After the transcript exists, visual analysis requests a video-only stream capped at 720p. Once indispensable frames are known, ffmpeg seeks those timestamps from the highest available stream up to 1080p and replaces only the retained screenshots; it does not keep the final video stream. A cookie may expose more subtitle tracks or qualities, but it is not required for every public video.

## Obsidian

Point `--vault` at any writable Vault directory. The CLI writes user-facing notes under `Sources/Videos/YYYY/YYYY-MM`, plus master and daily indexes under `Indexes/`. Operational state never lives inside the Vault. Markdown uses relative image links, so synchronization is independent of the processing pipeline. Use Obsidian Sync, Syncthing, Git, or WebDAV according to the deployment environment.

Run `scripts/vtm cleanup` hourly if desired. Every CLI invocation also removes work directories older than 6 hours, exports older than 24 hours, and recycle-bin entries older than 30 days. Active task media is removed in `finally` on success, failure, or cancellation.

## Bilibili account safety

Use a dedicated low-risk account. Mount the cookie file read-only. Rotate it when invalid, never echo it, and keep job concurrency low. A mainland region improves network locality but does not bypass account, payment, copyright, geographic, or risk-control restrictions.
