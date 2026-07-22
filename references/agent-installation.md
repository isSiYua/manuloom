# Agent installation

Keep one canonical copy of this folder. Install it by copying or symlinking the whole `manuloom` directory into the target Agent's configured skills directory. The core behavior lives in `scripts/video_manuscript.py`; product-specific Skill loading is only an invocation adapter.

## Hermes

Place or link the folder under the Hermes skills directory, commonly:

```text
~/.hermes/skills/manuloom/
```

Never keep a backup containing the same `SKILL.md` anywhere under `~/.hermes/skills/`. Hermes scans subdirectories and two manifests with the same `name` can route invocations to different versions. Move backups to `~/skill-backups/` or another directory outside the Skill scan root. `doctor` reports `duplicate_skill_paths` and refuses video runs while duplicates remain.

Run the Hermes service with access to the Obsidian Vault path, `ffmpeg`, Python dependencies, a prepared ASR model, and secret environment variables. In Feishu, sending a Bilibili link alone or “提取这个视频：<URL>” should trigger `SKILL.md`, which calls the CLI and returns the generated note path or attachment.

Disable raw tool progress in the messaging platform and allow only the Skill's six Chinese stage messages. The exact Hermes configuration keys can change by release; use the installed Hermes configuration help rather than embedding provider internals in this portable Skill.

For current Hermes releases, users can speak naturally: `提取这个视频：<URL>`, `任务列表`, `所有笔记列表`, `下载 1`, or `删除 1`. Keep Feishu tool progress off and let the Skill start the `scripts/vtm` launcher in terminal background mode with `--gateway-output --progress-target feishu`. Do not use `notify_on_complete` or `watch_patterns` for these runs. The CLI uses the official `hermes send` command and the configured Feishu home channel to deliver `[今日任务 N · YYYYMMDD-N] [N/6]` stage output directly while the main chat remains available. Keep normal busy input in `queue` mode as a fallback for any foreground task.

The launcher prefers an interpreter that can import FunASR, using `/usr/bin/python3.10` first on the reference Linux deployment. Set `VTM_PYTHON` in the gateway service environment only when a different prepared interpreter is required. Agents must invoke the launcher once and must not retry with another Python executable.

## Codex and other Skill-aware agents

Place or link the same directory in the product's configured skills location. Do not fork the Python core. If a product requires different frontmatter or UI metadata, add a thin product-specific adapter that delegates to the canonical CLI.

## Agents without Skill discovery

Expose a command or tool definition that invokes:

```text
<absolute-skill-path>/scripts/vtm run --url <URL> --vault <VAULT>
```

Pass arguments structurally rather than through shell interpolation. Parse the JSON printed to stdout. A zero exit code means success; errors are JSON on stderr with a non-zero exit code.
