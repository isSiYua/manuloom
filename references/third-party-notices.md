# Third-party notices

This skill contains focused adaptations inspired by the following MIT-licensed projects. Their full repositories remain the authoritative sources and licenses.

- BiliNote, copyright its contributors: Bilibili subtitle-first acquisition flow and current `dm_img_*` yt-dlp compatibility approach. https://github.com/JefferyHcool/BiliNote
- summarize, copyright its contributors: scene-threshold calibration, average-hash comparison, and frame-quality approach. https://github.com/steipete/summarize
- video-report-nemotron, copyright its contributors: intermediate artifact separation, stable local media, OCR-first visual workflow, and cleanup lifecycle. https://github.com/AetherX-Technologies/video-report-nemotron
- video-to-subtitle-summary-skill, copyright its contributors: optional faster-whisper runtime fallback patterns. https://github.com/imlewc/video-to-subtitle-summary-skill

The optional FunASR runtime is an external dependency and is not redistributed in this Skill archive. The FunASR toolkit is MIT-licensed, while pretrained model weights have separate model-card licenses that must be reviewed before redistribution. https://github.com/modelscope/FunASR

The optional Bilibili AI-conclusion transcript integration follows public field descriptions maintained by the community Bilibili API collection; no source code from that project is copied here. The endpoint requires the user's own authenticated session and may change or be unavailable. https://github.com/pskdje/bilibili-API-collect

Redistribution of adapted MIT-licensed portions is permitted subject to preservation of the applicable copyright and permission notices. Consult each linked repository's `LICENSE` file when redistributing this skill.

Full notices for source-derived code are preserved in [licenses/BiliNote-MIT.txt](licenses/BiliNote-MIT.txt) and [licenses/summarize-MIT.txt](licenses/summarize-MIT.txt).

VideoLingo (Apache-2.0) was studied for its faithfulness-then-expressiveness workflow and neighbouring-context pattern. No VideoLingo source code is redistributed in this Skill; the independent implementation applies the architectural idea to manuscript editing. https://github.com/Huanshere/VideoLingo

readability-lxml (Apache-2.0) is an external Python dependency used to select and clean the main body of public web articles while retaining article images. The Skill adds its own bounded downloader, SSRF protections, ordered evidence conversion, image placement, and release gates; readability-lxml is not copied into this repository. https://github.com/buriy/python-readability

extruct (BSD-3-Clause) is an external Python dependency used to read standard embedded JSON-LD article metadata such as title, author, publication date, and publisher. Platform-specific selectors remain a fallback rather than replacing this maintained structured-data parser. https://github.com/scrapinghub/extruct

zhihu-tui / zhihu-cli (Apache-2.0) is an external Python dependency used for its maintained, structured Zhihu answer and article API client. This Skill consumes the raw content returned by that client and independently applies bounded evidence conversion, secret handling, image acquisition, manuscript editing, and Obsidian output; no zhihu-cli source is copied into this repository. The integration was verified against version 0.1.3 and repository commit `fdef60e249c70be996b6fe9da32694d313e794b7`. https://github.com/Xiaofan629/zhihu-cli

Social Media Toolkit / social-post-extractor-mcp (Apache-2.0) is the source of the public Douyin share-page `_ROUTER_DATA` routing and field-mapping approach adapted in `scripts/vtm_core/douyin.py`. The adapted implementation removes MCP, GetNote, comments, cloud ASR, browser automation, and artifact code; adds standard-library networking, redirect/CDN validation, response bounds, video-only scope, one-download reuse, and this project's local ASR/visual/manuscript contracts. The adaptation was verified against version 0.3.0 and commit `72718caf3e7bb08a8bf4b990d074a53aec5bd5b9`. Its Apache-2.0 license is included at `licenses/Apache-2.0.txt`. https://github.com/JNHFlow21/social-post-extractor-mcp
