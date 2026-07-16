# video-to-detailed-manuscript

把 Bilibili 视频整理成适合 Obsidian 的“有结构、有细节、有证据”的完整编辑文字稿，而不是简短摘要或原始 ASR 字幕。

这是一个可移植 Agent Skill：核心工作由确定性 Python CLI 完成，Hermes、Codex、Claude Code、OpenCode、Kimi Code 等支持 Skill/命令调用的 Agent 只负责理解自然语言、启动任务和传送结果。

## 主要能力

- 字幕优先：原生字幕 → 登录态 AI 字幕/总结片段 → 本地 FunASR。
- 四阶段文字编辑：全文规划、黄金文风成稿、细节恢复、最终精简校对。
- 保留观点、原因、例子、步骤、参数、代码、命令、数字、条件、限制和精确名称。
- 字幕驱动的动态视觉规划，不按“每分钟一张”机械采样。
- 720p 场景检测、OCR 和相似帧去重；只对 AI 请求区间内的不同页面调用视觉模型。
- 简单文字、名单、表格、代码和公式在完整识别后转成可复制 Markdown/LaTeX。
- 流程图、架构图、论文原图、复杂 UI、密集或部分识别画面保留截图，并放在对应正文之后。
- 最终仅对保留图片按时间点从最高可用视频流重新截图，默认请求最高 1080p。
- Obsidian Vault、每日任务编号、单篇 ZIP 下载、软删除/恢复、取消、进度通知与临时媒体清理。

## 处理流程

```text
视频链接
  → 字幕/本地 ASR
  → 全文结构与视觉区间规划
  → 720p 场景候选 + OCR + 去重
  → 可选视觉 API
  → 四阶段详细文字稿编辑
  → 画面文字替换 / 复杂图保留
  → 最高 1080p 定点重截
  → Obsidian Markdown + assets
```

## 快速开始

在 Debian/Ubuntu 服务器上，克隆仓库后运行一键安装器：

```bash
git clone https://github.com/isSiYua/video-to-detailed-manuscript.git
cd video-to-detailed-manuscript
./install.sh --agent hermes
```

Codex 使用 `--agent codex`；其他 Agent 使用 `--skill-dir` 指定其 Skill 目录。安装器会安装 `ffmpeg`、Python 依赖和 CPU 版 FunASR，准备三个固定的 ModelScope 模型，建立 Skill 链接并运行 `doctor`。它不会安装 Agent、配置飞书或写入任何密钥。

如果只处理已有字幕的视频，可以跳过较大的本地 ASR：

```bash
./install.sh --agent hermes --minimal
```

完整安装选项、三个 FunASR 模型的准确 ID 与官方链接见 [DEPLOYMENT.md](DEPLOYMENT.md)。环境变量和模型切换见 [references/configuration.md](references/configuration.md)。

## 模型配置

参考低成本组合：

- 文字：DeepSeek V4 Flash
- 视觉：Qwen3-VL-Flash
- ASR：FunASR Paraformer + FSMN-VAD + CT-Punctuation

文字与视觉接口均为 OpenAI-compatible，可分别切换，也可以使用同一个多模态 OpenAI 模型。更换模型不会绕过 Skill 的结构、清理和发布门禁，但不同模型的中文编辑、视觉识别和 JSON 遵循能力并不完全相同，建议先用已知视频回归。

## 常用命令

```bash
scripts/vtm doctor
scripts/vtm contract
scripts/vtm configure
scripts/vtm configure platform 1
scripts/vtm configure status
scripts/vtm tasks
scripts/vtm tasks --all
scripts/vtm bundle --task 1 --send-target feishu
scripts/vtm delete --task 1 --confirm
scripts/vtm restore --task YYYYMMDD-1
scripts/vtm cancel --latest-running
scripts/vtm cleanup
```

Agent 自然语言映射和进度规则见 [SKILL.md](SKILL.md)。

`configure` 会显示核心服务与来源平台的确定性配置清单。公开内容优先使用无凭据模式；Cookie、API Key、Secret 和 Token 不得发送到 Agent 聊天，只能通过 SSH 交互终端运行 `scripts/vtm configure secret <配置项>` 隐藏录入。秘密保存在项目专用的 `0600` 文件中，状态命令只返回是否已配置，不返回值或前缀。

## 安全与平台规则

- 不要提交 `.env`、Cookie、`SESSDATA`、API Key、任务数据库、Obsidian Vault、模型权重或下载的视频。
- Bilibili Cookie 应使用低风险专用账号，文件权限设为 `600`，失效或泄露时立即轮换。
- 项目不绕过付费、版权、地区或平台风险控制；只处理你有权访问和保存的内容。
- 视频、音频和 WAV 在成功、失败或取消后自动清理；源媒体不是最终产物。

## 测试

```bash
cd scripts
python3 -m unittest discover -s tests -q
```

当前版本同时包含文字黄金质量回归、任务管理、Bilibili 获取、动态视觉规划、多图插入、OCR 替换、1080p 定点重截和清理测试。

## 路线图

`1.0.0` 冻结首个公开的 Bilibili 处理核心。后续平台接入不会复制文字编辑和视觉判断逻辑，而是在同一证据管线前增加来源适配器：

- YouTube：优先读取字幕，缺失时获取单路音频进行本地 ASR，并按需取得视频帧；
- 普通网页：抽取正文、标题、作者、发布时间和必要图片，跳过音频步骤；
- 知乎：先支持无需登录即可访问的文章、回答和公开视频；
- 抖音、小红书：作为可选适配器处理可公开访问的分享链接，但登录、动态页面和平台风控会影响成功率。

适配器只负责取得用户有权访问的文字、时间轴和视觉证据，统一交给现有的详细文稿编辑、Obsidian 存储和任务系统。项目不会绕过登录、付费、版权、地区或风险控制。设计边界和计划见 [references/roadmap.md](references/roadmap.md)。

## 来源与许可证

项目采用 MIT License。受 BiliNote、VideoLingo、summarize、yt-dlp、FunASR 等项目启发或使用其外部依赖；完整说明和适用许可证见 [references/third-party-notices.md](references/third-party-notices.md)。预训练模型权重和第三方服务仍受各自条款约束。
