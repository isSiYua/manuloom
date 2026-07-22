# Deployment

Run the installer as the same Unix user that runs the Agent. The reference target is Debian/Ubuntu on a 4-vCPU, 8-GB server with at least 5 GB free disk space.

## One-command dependency and Skill installation

```bash
git clone https://github.com/isSiYua/manuloom.git
cd manuloom
./install.sh --agent hermes
```

For Codex use `./install.sh --agent codex`. For another Agent, pass its exact Skill destination:

```bash
./install.sh --skill-dir /absolute/path/to/agent/skills/manuloom
```

The full installer installs `ffmpeg`, `ffprobe`, Chinese Tesseract OCR, Python, pip, Git, `yt-dlp`, CPU PyTorch, torchaudio, FunASR, and ModelScope. It then calls the existing `prepare-asr`, links the repository into the selected Agent Skill directory, and runs `doctor`.

For an existing Hermes + Feishu installation, continue with the dedicated [Hermes + Feishu guide](docs/hermes-feishu.md). For a local computer, the [browser-extension guide](docs/browser-extension.md) provides a loopback-only web bridge and Chromium extension; a server is not required.

Use `--dry-run` to inspect every command without changing the server. Use `--minimal` to skip local ASR packages and model downloads; videos without usable subtitles will then require another prepared ASR backend.

The installer deliberately does not install Hermes or another Agent, configure Feishu, request credentials, or write API keys. Copy only the variable names from `.env.example` into the Agent service's protected environment file, replace the placeholders with the user's own credentials, and set permissions to `600`.

## Prepared FunASR models

The model IDs below are defined once in `scripts/vtm_core/asr.py`; `prepare-asr` is the authoritative downloader. Model weights are not redistributed by this repository.

- ASR: `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch` — [ModelScope model card](https://modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch)
- VAD: `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` — [ModelScope model card](https://modelscope.cn/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch)
- Punctuation: `iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch` — [ModelScope model card](https://modelscope.cn/models/iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch)

The FunASR toolkit documents installing PyTorch and torchaudio before FunASR, and notes that pretrained weights have model-specific licenses. Review the linked model cards before redistribution. See the [official FunASR repository](https://github.com/modelscope/FunASR).

## Existing installations

The installer never overwrites an existing Skill directory. If the target already contains a different copy, move that copy outside the Agent's Skill scan root and run the installer again. This prevents two versions of the same `SKILL.md` from being discovered.

To update a linked installation later:

```bash
cd manuloom
git pull --ff-only
scripts/manuloom doctor
```
