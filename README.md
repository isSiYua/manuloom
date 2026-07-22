<h1 align="center">ManuLoom · 知织</h1>

<p align="center">把视频和网页内容编织成有结构、有细节、有证据的 Obsidian 笔记。</p>

大多数视频工具止步于字幕或简短摘要。ManuLoom 的目标是一篇真正可以阅读、学习和长期保存的编辑稿：保留来源支持的细节，重建清晰章节，选择有用的视觉证据，并把 Markdown 与本地图片直接写入 Obsidian Vault。

它通过同一套证据管线处理 Bilibili、YouTube、抖音公开视频，Bilibili opus/专栏、小红书/RedNote 图文、知乎回答/文章以及普通公开网页。

> ManuLoom 采用本地优先和用户自带模型的方式。Hermes、Codex、Claude Code、OpenCode、Kimi Code 等 Agent 可以作为聊天入口，但真正执行采集、编排、校验和发布的是确定性 Python CLI。

**[English](README.en.md) · [完整示例笔记](examples/synthetic-pipeline-note/manuloom-demo.md) · [部署文档](DEPLOYMENT.md) · [配置说明](references/configuration.md)**

## 为什么不是普通摘要器

| 常见字幕摘要工具 | ManuLoom |
|---|---|
| 输出几百字概述 | 输出保留细节的完整编辑稿 |
| 把字幕当作全部来源 | 结合字幕、ASR、网页结构、OCR 与视觉证据 |
| 固定间隔截图 | 按正文语义规划视觉区间，并优先选择信息完整的稳定画面 |
| 图片与说明分别决定，容易重复 | 每个视觉项只能选择 `drop`、`note_only`、`image_only` 或 `image_with_note` |
| 模型失败也可能留下半成品 | 必要阶段失败时不发布笔记，只保留审计材料 |
| 通常只支持一个视频网站 | 视频和图文来源共享同一适配器协议与编辑管线 |

编辑原则不是“尽量少写”，而是：**删除口语重复，同时保留来源中真正有用的原因、例子、步骤、参数、代码、数字、条件与限制。**

## 看一眼最终效果

仓库提供了一篇不涉及第三方版权的合成示例，展示目标章节结构、信息密度、表格、代码、视觉插入和引用方式：

- **[打开 ManuLoom 完整示例笔记](examples/synthetic-pipeline-note/manuloom-demo.md)**
- [golden-quality.md](references/golden-quality.md) 定义正文质量门禁。
- [golden-style-example.md](references/golden-style-example.md) 定义目标文风。

## 已支持来源

| 来源 | 公开模式 | 可选凭据 | 证据路径 |
|---|---:|---|---|
| Bilibili 视频 | 支持 | Cookie 只用于可选登录增强 | 原生/AI 字幕 → 本地 ASR 回退 → 动态画面 |
| YouTube 视频 | 支持 | Data API Key 可选 | 人工/原语言自动字幕 → 本地 ASR 回退 → 动态画面 |
| 抖音视频 | 支持 | 无 | 公开分享页 → 单次媒体下载 → 本地 ASR → 动态画面 |
| Bilibili opus/专栏 | 支持 | 公开内容无需 | 有序 HTML 块 + 原图 |
| 小红书/RedNote 图文 | 支持 | 公开图文无需 | 有序正文/话题 + 原图 |
| 知乎回答/文章 | 尽力读取 | 风控需要时使用用户自己的 `z_c0` | 结构化 HTML + 链接/代码/LaTeX/图片 |
| 普通网页/CSDN | 网络可达时支持 | 无 | 正文结构 + 表格/代码/图片 |

项目不会绕过登录、付费墙、版权、地区限制、已删除内容或平台风控。

## 处理流程

```text
公开来源链接
  ├─ 视频 → 字幕或本地 ASR
  └─ 文档 → 有序文字块与原图
        ↓
全文结构与视觉区间规划
        ↓
请求区间内的本地场景检测、OCR 与去重
        ↓
规划、成稿、细节恢复、最终校对
        ↓
视觉发布决策与高清定点重截
        ↓
Obsidian Markdown + 本地 assets + 审计材料
```

DeepSeek 或其他兼容文字模型负责语义结构和措辞；Python 负责时间顺序、JSON 契约、任务状态、存储、视觉预算和失败门禁；可选的 Qwen-VL 只读取本地筛选后的视觉候选，不增加第二轮付费图片比较。

## 快速开始

### Debian / Ubuntu

```bash
git clone https://github.com/isSiYua/manuloom.git
cd manuloom
./install.sh --agent hermes
```

Codex 使用 `--agent codex`；其他 Agent 使用 `--skill-dir /绝对路径/skills/manuloom`。如果来源通常已有字幕，可以跳过较大的本地 ASR：

```bash
./install.sh --agent codex --minimal
```

### macOS 本地运行

服务器不是必需条件：

```bash
brew install python ffmpeg tesseract
git clone https://github.com/isSiYua/manuloom.git
cd manuloom
./install.sh --agent codex --minimal --skip-system-packages
```

完整本地 ASR 的额外依赖与模型下载见 [DEPLOYMENT.md](DEPLOYMENT.md)。Windows 当前建议使用 WSL2，尚未承诺原生 Windows 安装体验。

### 安全配置模型

```bash
scripts/manuloom configure secret text_llm_key
scripts/manuloom configure secret vision_api_key   # 可选
scripts/manuloom configure status
```

不要把 API Key 或 Cookie 发给 Agent 聊天。专用秘密文件位于仓库外，权限为 `0600`；状态命令只显示是否配置，不显示值。

### 不使用 Agent，直接运行

```bash
scripts/manuloom doctor
scripts/manuloom run --url 'https://www.bilibili.com/video/BV...' --vault ~/ObsidianVault
```

Agent 只增加自然语言命令、后台进度和附件发送；核心生成能力可以完全在个人电脑上运行。

## 两种便捷使用方式

### 服务器上的 Hermes + 飞书

如果 Hermes 与飞书通道已经可以正常通信，只需把 ManuLoom 安装成一个软链接 Skill：

```bash
git clone https://github.com/isSiYua/manuloom.git
cd manuloom
./install.sh --agent hermes
scripts/manuloom configure status
```

重启 Hermes Gateway 后，在飞书中发送受支持的链接并说 `提取这个` 即可。Skill 会提交独立后台任务、发送六阶段进度，并通过 `下载 N` 返回可迁移 ZIP。完整步骤见 [Hermes + 飞书部署](docs/hermes-feishu.md)。

### 本地浏览器扩展

采集、ASR、模型调用和笔记文件全部留在自己的电脑上，只用 Chrome、Edge 或 Brave 扩展提交当前标签页：

```bash
scripts/manuloom serve
scripts/manuloom browser-token
```

把 `browser-extension/` 作为“已解压的扩展程序”加载，粘贴一次本机配对令牌，然后点击生成。扩展只有用户点击时才获得临时 `activeTab` 权限；它没有 `<all_urls>` 权限、不包含模型密钥，而且只能连接回环地址。完整步骤见 [本地浏览器使用](docs/browser-extension.md)。

## 推荐的低成本组合

- 文字编辑：DeepSeek 的 OpenAI-compatible 接口；
- 视觉识别：Qwen-VL 的兼容接口；
- ASR：本地 FunASR Paraformer + FSMN-VAD + CT-Punctuation。

模型与服务商均可替换。不同模型的中文编辑、OCR 和 JSON 遵循能力不同，更换后应先用已知来源回归。

## 质量与成本边界

- 正文采用规划、成稿、细节恢复和最终精简校对四个阶段；
- 术语核对只能修复有证据的局部 ASR 疑点，不能删除周围上下文；
- 视觉默认基础预算为 6，并按视频时长和字幕需求动态增加，硬上限为 60；
- 本地选帧优化不会增加视觉模型图片输入次数；
- 必要正文阶段返回错误时不会发布“尽力而为”的半成品；
- 管线版本与项目发布版本分别管理。

## 参与贡献

欢迎提交 Bug、来源适配器、文档改进和可复现的质量案例。请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [行为准则](CODE_OF_CONDUCT.md)。

正文提示或质量门禁的修改必须证明具备通用收益，并且不会让已验收样稿退化。新平台应只负责取得证据，不得复制一套新的正文生成流程。

## 隐私、版权与开源

- API Key、Cookie、Vault、任务数据库、模型权重、源媒体和私人笔记均不得提交；
- 只处理你有权访问、下载和转换的内容；
- 公开适配器不会尝试绕过访问控制；
- 安全问题见 [SECURITY.md](SECURITY.md)；
- 第三方依赖、算法思路和许可证见 [third-party-notices.md](references/third-party-notices.md)。

ManuLoom 使用 [MIT License](LICENSE)。第三方库、模型权重、网页内容和模型服务仍受各自许可证与条款约束。
