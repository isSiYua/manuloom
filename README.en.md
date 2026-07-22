<h1 align="center">ManuLoom</h1>

<p align="center">
  Weave videos and web sources into detailed, source-grounded Obsidian notes.
</p>

Most video tools stop at a transcript or a short summary. ManuLoom builds a readable manuscript instead: it preserves source-supported details, reconstructs coherent sections, selects useful visual evidence, and writes portable Markdown with local assets directly into an Obsidian Vault.

It handles public Bilibili and YouTube videos, Douyin shares, Bilibili opus/articles, Xiaohongshu/RedNote image notes, Zhihu answers/articles, and ordinary public web pages through one evidence pipeline.

> ManuLoom is local-first and bring-your-own-model. Hermes, Codex, Claude Code, OpenCode, Kimi Code, or another command-capable Agent can provide the chat interface, but the deterministic Python CLI remains the source of truth.

**[中文首页](README.md) · [Reference output](examples/synthetic-pipeline-note/manuloom-demo.md) · [Deployment](DEPLOYMENT.md) · [Configuration](references/configuration.md)**

## Why ManuLoom

| Typical transcript summarizer | ManuLoom |
|---|---|
| Produces a short overview | Produces a detailed edited manuscript |
| Treats subtitles as the whole source | Combines subtitles, ASR, page structure, OCR, and visual evidence |
| Samples screenshots on a fixed interval | Plans visual ranges from the transcript and selects stable, information-complete states |
| Describes every retained image | Publishes each visual as `drop`, `note_only`, `image_only`, or `image_with_note` |
| Publishes partial output when a model pass fails | Fails closed and keeps audit artifacts instead of publishing an incomplete note |
| Usually supports one video site | Uses a shared adapter protocol for videos and image-rich articles |

The editorial goal is not “say less.” It is **preserve the source's useful detail without preserving its spoken repetition**.

## What the output looks like

The repository includes a copyright-safe, synthetic reference note showing the intended structure, information density, diagrams, tables, code, attribution, and visual placement:

- **[Open the complete ManuLoom reference note](examples/synthetic-pipeline-note/manuloom-demo.md)**
- The editorial contract lives in [golden-quality.md](references/golden-quality.md).
- The style examples live in [golden-style-example.md](references/golden-style-example.md).

```markdown
## Evidence acquisition and fallback

ManuLoom prefers an existing subtitle track because it preserves timing without
another recognition pass. When no usable subtitle exists, it downloads one audio
stream and runs the configured local ASR backend.

![Evidence pipeline](assets/evidence-pipeline.svg)

The final note retains the source URL, platform identity, and local media assets.
Temporary video, audio, and WAV files are removed after success, failure, or cancellation.
```

## Supported sources

| Source | Public mode | Optional credentials | Evidence path |
|---|---:|---|---|
| Bilibili video | Yes | Cookie for optional login-only enhancements | Native/AI subtitle → local ASR fallback → dynamic frames |
| YouTube video | Yes | Data API key is optional | Manual/original-language subtitle → local ASR fallback → dynamic frames |
| Douyin video | Yes | None | Public share page → one media download → local ASR → dynamic frames |
| Bilibili opus/article | Yes | None for public content | Ordered HTML blocks + original images |
| Xiaohongshu/RedNote image note | Yes | None for public image notes | Ordered text/topics + original images |
| Zhihu answer/article | Best effort | Your own `z_c0` only when risk control requires it | Structured HTML + links/code/LaTeX/images |
| Generic web/CSDN | Yes, when reachable | None | Main article structure + tables/code/images |

ManuLoom does not bypass logins, paywalls, copyright restrictions, regional restrictions, deleted content, or platform risk controls.

## Pipeline

```text
public source URL
  ├─ video → subtitles or local ASR
  └─ document → ordered text blocks and original images
        ↓
full-source outline and visual-range planning
        ↓
local scene/OCR/dedup analysis on requested ranges
        ↓
four-stage manuscript editing
        ↓
visual publication decision and high-resolution recapture
        ↓
Obsidian Markdown + local assets + audit artifacts
```

DeepSeek (or another compatible text model) decides semantic structure and wording. Python validates chronology, JSON contracts, task state, storage, visual budgets, and failure gates. Optional Qwen-VL (or another compatible vision model) reads only locally selected visual candidates; frame selection does not add a second paid comparison round.

## Quick start

### Debian or Ubuntu

```bash
git clone https://github.com/isSiYua/manuloom.git
cd manuloom
./install.sh --agent hermes
```

Use `--agent codex` for Codex, or `--skill-dir /absolute/agent/skills/manuloom` for another Agent. To skip the larger local ASR installation when most of your sources already contain subtitles:

```bash
./install.sh --agent codex --minimal
```

### macOS local machine

A server is optional. Install the system tools locally, then let the installer prepare the Python dependencies and Skill link:

```bash
brew install python ffmpeg tesseract
git clone https://github.com/isSiYua/manuloom.git
cd manuloom
./install.sh --agent codex --minimal --skip-system-packages
```

Full local ASR requires the extra packages and model download described in [DEPLOYMENT.md](DEPLOYMENT.md). Windows users should currently use WSL2; native Windows installation is not yet a supported path.

### Configure models safely

Copy `.env.example` into your service environment, or enter secrets in a hidden terminal prompt:

```bash
scripts/manuloom configure secret text_llm_key
scripts/manuloom configure secret vision_api_key   # optional
scripts/manuloom configure status
```

Never paste API keys or Cookies into an Agent chat. The dedicated secret file is stored outside the repository with mode `0600`, and status output never prints values.

### Run without an Agent

```bash
scripts/manuloom doctor
scripts/manuloom run --url 'https://www.bilibili.com/video/BV...' --vault ~/ObsidianVault
```

The CLI is fully usable on a local machine. Agent integrations add natural-language commands, background progress, and attachment delivery; they are not required for manuscript generation.

## Two easy ways to use it

### Hermes + Feishu on a server

If Hermes and its Feishu channel already work, ManuLoom installs as one linked Skill:

```bash
git clone https://github.com/isSiYua/manuloom.git
cd manuloom
./install.sh --agent hermes
scripts/manuloom configure status
```

Restart the Hermes gateway, then send a supported link with `提取这个`. The Skill submits a detached job, posts six stable progress stages to Feishu, and can return a portable ZIP through `下载 N`. See the complete [Hermes + Feishu guide](docs/hermes-feishu.md).

### Local browser extension

Keep acquisition, ASR, model calls, and files on your own computer while submitting the active tab from Chrome, Edge, or Brave:

```bash
scripts/manuloom serve
scripts/manuloom browser-token
```

Load `browser-extension/` as an unpacked extension, paste the local pairing token, and click **Generate Obsidian note**. The extension requests temporary `activeTab` access only after you click it. It has no `<all_urls>` permission, contains no model credential, and can connect only to the loopback bridge. See [Local browser setup](docs/browser-extension.md).

## Recommended low-cost model split

- Text editing: DeepSeek through an OpenAI-compatible Chat Completions endpoint.
- Visual reading: Qwen-VL through a separate compatible endpoint.
- ASR: local FunASR Paraformer + FSMN-VAD + CT-Punctuation.

Providers and model names are configurable. Different models can change Chinese editing quality, OCR accuracy, and structured-JSON reliability, so test a known source after switching.

## Quality and cost boundaries

- Four text passes: plan, write, detail recovery, and final concise review.
- A bounded terminology reconciliation pass may repair uncertain ASR spans but cannot delete surrounding context.
- The default visual budget starts at six requests and grows with duration and transcript needs, with a hard cap of 60.
- Local frame refinement does not increase the number of paid vision image inputs.
- A malformed required document pass never becomes a published “best effort” note.
- The pipeline version remains separate from the package/release version.

## Project layout

```text
scripts/manuloom              friendly CLI entry point
scripts/vtm                   compatibility CLI entry point
scripts/vtm_core/             deterministic acquisition and publishing core
browser-extension/            narrow-permission Chromium extension
SKILL.md                      Agent behavior and natural-language contract
references/                   quality, architecture, configuration, and licenses
examples/                     copyright-safe reference outputs
```

## Contributing

Bug reports, source adapters, documentation improvements, and reproducible quality cases are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md) before opening a pull request.

Core editorial changes require evidence that they improve general quality without degrading accepted manuscripts. Platform adapters should acquire evidence only; they must not fork the manuscript-writing pipeline.

## Privacy, copyright, and security

- API keys, Cookies, Vault content, task databases, model weights, source media, and generated private notes are ignored and must never be committed.
- Use only content you are authorized to access, download, and transform.
- Public-source adapters do not attempt to circumvent access controls.
- See [SECURITY.md](SECURITY.md) for private-reporting guidance.
- Third-party dependencies and adapted algorithms are documented in [third-party-notices.md](references/third-party-notices.md).

## License

ManuLoom is released under the [MIT License](LICENSE). Third-party libraries, model weights, web content, and provider services remain subject to their own licenses and terms.
