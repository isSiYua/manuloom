# Hermes + Feishu deployment

This path is for a user who already has a working Hermes installation and a Feishu/Lark bot channel. ManuLoom does not create a Feishu application, request bot credentials, or patch Hermes itself; it installs one portable Skill and uses Hermes' official send command.

## 1. Install as the Hermes service user

SSH in as the same non-root Unix user that runs the Hermes gateway:

```bash
git clone https://github.com/isSiYua/manuloom.git
cd manuloom
./install.sh --agent hermes
```

Use `--minimal` for the least expensive subtitle/document-first installation. Full installation also downloads CPU ASR packages and models and therefore takes longer and uses several GB of disk.

The installer creates this single link:

```text
~/.hermes/skills/manuloom -> /absolute/path/to/manuloom
```

It refuses to overwrite another Skill. Do not keep backup copies containing the same `SKILL.md` anywhere below `~/.hermes/skills`.

## 2. Configure without exposing secrets in chat

Run status and platform guidance first:

```bash
scripts/manuloom configure status
scripts/manuloom configure platform 1
```

Enter each required value in an SSH terminal with hidden input:

```bash
scripts/manuloom configure secret text_llm_key
scripts/manuloom configure secret vision_api_key
```

Public Bilibili, YouTube, Douyin, Xiaohongshu image notes, Bilibili opus, and ordinary public articles do not require a platform credential for their public path. A Bilibili Cookie is optional; Zhihu may require the user's own `z_c0` when anonymous access is risk-controlled. Never paste either into Feishu.

The secret file is outside the repository at `~/.config/manuloom/secrets.env` with mode `0600`. Status output contains only booleans.

## 3. Select a Vault and verify

Set `VTM_VAULT` in the protected Hermes service environment, or use the default `~/ObsidianVault`. Then run:

```bash
scripts/manuloom doctor
```

Confirm that `skill_installation_ok` and `text_llm_key` are true. `asr_ready` may be false on a minimal installation; subtitle-bearing videos and document sources still work.

Restart the gateway using the method recommended by the installed Hermes release. ManuLoom deliberately does not guess or overwrite the gateway's service-manager configuration.

## 4. Use from Feishu

Send a supported public link alone, or use natural Chinese:

```text
提取这个：https://www.bilibili.com/video/BV...
所有笔记
任务状态
下载 1
配置平台
```

For extraction, the Skill uses one short `submit` invocation. The real pipeline is detached from the chat turn and sends six stable progress stages through `hermes send`, so a long video does not occupy the conversational Agent. Multiple requests receive distinct daily task IDs; when concurrency is full they wait in the pipeline queue.

## 5. Update safely

```bash
cd /absolute/path/to/manuloom
git pull --ff-only
scripts/manuloom doctor
```

Because the Skill is a symlink, no second copy step is needed. Review release notes before upgrading production and keep the previous Git commit ID for rollback.

## Troubleshooting

- Hermes does not invoke ManuLoom: verify the symlink and run `scripts/manuloom doctor`; remove duplicate Skill copies.
- Feishu shows no progress: verify the Hermes home channel and the official `hermes send --to feishu` path independently.
- Source works locally but not on the server: the two machines have different network exits. Configure only a private, policy-compliant source proxy where permitted.
- Videos without subtitles fail on `--minimal`: install the full ASR dependencies with the installer or run a prepared ASR backend.
- A task is queued: this is normal when `VTM_MAX_CONCURRENT_JOBS=1`; it does not mean the gateway is stuck.
