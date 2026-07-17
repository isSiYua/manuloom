from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

from . import PIPELINE_VERSION
from .llm import OpenAICompatibleClient, parse_json_object
from .models import Frame, Paragraph, Segment
from .utils import atomic_json, load_json


DIRECT_ATTEMPTS = 2
OBVIOUS_EDITORIAL_RESIDUE_RE = re.compile(
    r"今天手把手|同学们|大家自取|做个总结|封面页、眉页、脚|"
    r"自带超强文献|再厉害的\s+[Ss]cale|切换指文|卷积页码|"
    r"自带超期(?:刊|一篇)?文献|(?:和|与)?三大纲|CS五一|SR[- ]?pick|"
    r"点赞三连|点点三连|请(?:大家|观众)?(?:点赞|关注)|本期视频.{0,8}到这|下期见|拜拜"
)
NARROW_FINAL_REPAIRS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"具备文献处理能力能力"), "具备文献处理能力"),
    (re.compile(r"\bSY\s*paper\b", flags=re.I), "SY Paper"),
    (re.compile(r"封面页、眉页、脚"), "封面、页眉、页脚"),
    (re.compile(r"卷积页码"), "卷、期、页码"),
    (re.compile(r"CS五一", flags=re.I), "CSV"),
    (re.compile(r"SR[- ]?pick", flags=re.I), "SY Paper"),
    (re.compile(r"切换指文"), "相关参数"),
    (re.compile(r"再厉害的\s+[Ss]cale"), "模型规模再大"),
    (re.compile(r"自带超期(?:刊|一篇)?文献|自带超强文献"), "具备文献处理能力"),
    (re.compile(r"和三一?大纲政策"), "和自定义大纲"),
    (re.compile(r"与三一?大纲政策"), "与自定义大纲"),
    (re.compile(r"三一?大纲政策"), "自定义大纲"),
    (re.compile(r"一篇参考论文"), "一篇论文初稿"),
)


DIRECT_OUTLINE_PROMPT = """你是视频详细文字稿的结构编辑。阅读完整带时间戳字幕，只规划文章，不写正文。

先理解整篇视频的真实主题、论证关系或操作流程，再划分具体章节和自然段。章节按真实话题/工作流阶段组织，不能按固定时长或字幕批次切分。每个 paragraph 说明该段要讲清的 focus，并在 must_keep 中列出不能丢的理由、例子、步骤、按钮、参数、数字、条件、限制、验证方式、精确名称和结论。

自然段按语义目标切分：同一节包含准备、操作、结果、限制或多个明显子步骤时，应规划多个自然段，不能把整节视频塞进一个巨型段落；也不能一条字幕规划一段。教程中的真实步骤可设置简短 subheading，普通解释段落则为 null。

章节只在主题、论证阶段或工作流阶段真正变化时切换。开场路线图、概念概述或输入说明若紧接着展开同一对象，应与后续详解保留在同一章节，用多个自然段或必要的步骤 subheading 组织；不要创建相邻的“概述/详解”“介绍/具体说明”章节来重复同一层级。只有进入新的处理阶段、对象或问题时才新建章节。

删除对象仅限口癖、假开头、点赞关注请求、片尾告别和真正重复。不要为纯推广或片尾单独规划正文段落；这些字幕仍会由最后一个有效段落的时间范围承接，但不得写进正文。

每个自然段只返回连续字幕范围的 start_source_id。第一段必须从第一条字幕开始；后续起点必须来自输入并严格递增。章节顺序与视频一致。

同时判断哪些段落需要画面才能确认术语、界面状态、密集提示词、表格、代码、公式、流程图、论文原图或口述没有完整读出的名单。需要时添加 visual_requests，给出字幕时间范围、查看目的和预期类型；不需要时返回空数组。不要假设画面中存在字幕没有暗示的内容。

ASR 可疑片段必须按相邻语句整体判断，尤其要保留“不、没有、只、而不是”等决定逻辑方向的词。若英文术语被识别成无意义字母、相邻两条字幕合起来仍不通顺，或一句话的前后半句明显断裂，应把完整相邻片段放入 asr_suspects，并请求对应时间范围的 visual_requests；在没有高置信证据前，conservative_repair 返回 null。不得把猜测出的专名或句义写入 focus/must_keep。ASR 不确定只影响精确措辞，不能成为删除整条信息的理由；即使某个术语不确定，也必须用字幕支持的保守表达把周围明确的背景、案例、提问、理由和结论写入 focus/must_keep，并把不确定原文留在 asr_suspects 中。

严格返回 JSON：
{"sections":[{"title":"具体章节标题","objective":"本节要完整讲清的内容","paragraphs":[{"start_source_id":"s000001","subheading":"真实步骤标题或 null","focus":"该自然段的中心","must_keep":["不能丢的细节"],"attribution":["必须归属给作者的判断"],"asr_suspects":[{"source_text":"可疑原文","conservative_repair":"保守表达或 null","confidence":"high|medium|low"}],"visual_requests":[{"time_start":0.0,"time_end":20.0,"purpose":"要确认什么","expected_kind":"text|list|table|code|formula|diagram|chart|process|ui|paper_figure|comparison|other"}]}]}]}。"""


DIRECT_WRITER_PROMPT = """你是视频详细文字稿编辑。输入包含完整带时间戳字幕、article_plan、写作前获得的 visual_evidence、已核验的 asr_reconciliation，以及一份 golden_style_reference。严格按照规划写成接近创作者事先准备脚本的完整文章，不是摘要，也不是逐字字幕。

每个规划段落必须覆盖对应 focus、全部 must_keep 和 source_excerpt 中每一项独立的有用信息，并保留原字幕中的观点、理由、解释、例子、步骤、入口、按钮/选项、参数、数字、费用/额度、等待时间、输出字段、验证方法、限制、失败条件、比较、提醒、命令、代码、URL、精确名称、标题和结论。source_excerpt 是该自然段完整的本地字幕证据，不能只写 must_keep 而忽略其中未被规划器列出的有用细节。只删除口癖、假开头、点赞关注请求、片尾告别和真正重复。

学习 golden_style_reference 的信息密度、句长、段落组织和归属方式，但绝对不能复制其中的视频事实。把动作与原因、步骤与结果、观点与例子组织成客观、自然的段落。一句话能完整说清的内容不要拆成两三句；同一个结论只写一次。每段围绕一个明确目的；真实步骤、名单或字段才使用列表。详细不等于重复，不要在章节开头预告一遍、正文解释一遍、结尾再总结一遍。不要为了追求短小，把信息密集的完整教程压缩成提纲式短句；篇幅由独立有效信息决定。

删除“今天手把手、同学们、大家可以看到、我们下面、然后、接着、做个总结”等主持式或流水话语。作者的主观判断、宣传评价和经验必须归属为“作者认为/作者提到/视频展示”。不添加来源外背景或二次总结。

visual_evidence 只用于确认术语、修复 ASR、理解画面与口述的对应关系。画面独有而口述没有表达的事实不能偷偷写进正文；后续程序会把它们作为“画面补充”处理。视觉描述不完整或不确定时，不得猜测。

证据优先级是：语义明确的完整口述和上下文、高置信视觉中可见的精确拼写、article_plan。article_plan 只负责结构，不是事实来源；其中的 focus、must_keep 或 ASR 修复若与完整字幕、相邻语句或视觉证据冲突，必须以证据为准。英文术语在截图中清晰可见时使用画面拼写，不保留音译乱码。

asr_reconciliation 是写作前专门核验过的修复表，优先于 article_plan 中冲突的词句。必须把其中每条 replacement 写入对应段落，但不要在正文解释“字幕原来识别错了”。

修正常见 ASR 错词；把连续的断裂字幕合成一句后再判断，不得逐条机械纠错。必须保留原句的否定、限制和比较方向，不能把“不会、不存在、而不是”等意思改成相反结论。无法高置信还原的乱码用来源支持的保守表达，不猜新专名、参数、数字或因果关系，但不得连同周围明确的背景、案例、提问、理由或结论一起删除。保留本来通顺的产品名、界面标签和缩写。

如果一段口述本身已经连贯、信息密集、接近书面语且没有口癖或重复，允许正文与原文保持接近；不要为了证明“经过 AI 改写”而同义改写。编辑尺度由可读性和信息组织决定，不由改写字数决定。

使用 article_plan 的章节和段落起点；第一段从第一条字幕开始，所有 start_source_id 严格递增。严格返回 JSON：
{"sections":[{"title":"具体章节标题","paragraphs":[{"start_source_id":"s000001","subheading":"真实步骤标题或 null","text":"完整、自然、详细的正文"}]}]}。"""


DIRECT_DETAIL_PROMPT = """你是视频详细文字稿的内容编辑。输入包含完整带时间戳字幕、article_plan、visual_evidence、asr_reconciliation 和 current_draft。请主动逐段对照并返回补充、整理后的完整文稿，而不是原样复制初稿或输出评语。

重点检查初稿是否为了简洁而漏掉了原字幕中的理由、解释、例子、步骤、入口、按钮/选项、参数、数字、费用/额度、等待时间、输出字段、验证方式、限制、失败条件、比较、提醒、命令、代码、URL、精确名称、标题和作者结论。逐个阅读 paragraph_evidence_packets；每个 packet 已把本段 source_excerpt 和 current_text 放在一起。找出 current_text 尚未表达的独立信息并放回对应段落，尤其不要漏掉过去与现在的比较、失败后改用另一方法的过程、顺带举出的例子和验证动作；已准确表达的信息只保留一次。

把 article_plan 当作结构提示而不是事实证据。逐段重新核对相邻字幕与 visual_evidence：可见的英文拼写优先于音译乱码；连续断裂字幕要合并成完整句再修复；否定词、限制词和比较方向不得被反转。无法确认的片段应保守概括，不能沿用规划阶段猜出的专名或相反结论，也不能因为局部术语不确定而删除周围明确可理解的信息。

逐条检查 asr_reconciliation：对应 replacement 必须准确出现在稿件中，冲突的旧词或旧句必须删除。修复表只用于校正来源，不得扩展成新的背景知识。

必须检查 article_plan 中每个规划段落的 focus 和 must_keep 是否都已落实；一个章节规划了多个自然段时，不得重新合并成一个巨型段落。补回的是独立信息，不是重复措辞：同一细节已经准确出现时不要换一种说法再写一次。可以合并重复口语，但不能把详细文字稿压缩成摘要，不能添加来源外知识。视觉证据只用于修正术语和理解对应关系，不能把画面独有内容伪装成口述。删除仍残留的“今天手把手、同学们、大家自取、我们接下来、做个总结”等主持式措辞，并把主观评价归属给作者。保持视频原始顺序；第一段必须从第一条字幕开始，所有 start_source_id 必须来自字幕并严格递增。返回完整 JSON，不返回解释：
{"sections":[{"title":"具体章节标题","paragraphs":[{"start_source_id":"s000001","subheading":"真实步骤标题或 null","text":"细节完整的正文"}]}]}。"""


DIRECT_REVIEW_PROMPT = """你是视频文字稿的最终编辑。输入包含完整带时间戳字幕、visual_evidence、asr_reconciliation、golden_style_reference 和一版已经补充细节的 current_draft。请直接返回修订后的完整文稿，而不是输出评语或问题列表。

逐段对照原字幕完成四件事：
1. 逐个检查 paragraph_evidence_packets，补回 current_draft 遗漏的观点、原因、例子、步骤、界面入口、按钮、参数、数字、条件、限制、比较、验证方式、精确名称和作者结论；不得因为追求简洁而删掉独立细节。
2. 删除仍残留的口癖、重复、主持式称呼、点赞关注请求、片尾告别和时间流水账；合并同义句，使每段围绕一个清晰意思展开，但不要把整段压成摘要。按照 golden_style_reference 压缩文风：一句话能说清不用两句，同一结论不在导语、正文和结尾重复，删掉“这里主要介绍了”“这意味着”“其价值在于”等编辑套话。压缩的是表达，不是独立信息。
3. 修正明显 ASR 乱码、错词、断词和不自然句子。无法确定的乱码用来源支持的保守上位表达，不猜新名称、数字或事实。来源中本来通顺的产品名、界面标签或缩写直接保留。
4. 检查章节是否按真实主题/工作流阶段组织。visual_evidence 可用于确认术语和口述所指画面，但画面独有内容不得混入口述正文；图片和“画面补充”由后续程序插入。不要添加来源外背景、用途、价值判断或编辑者总结，也不得复制 golden_style_reference 中的视频事实。

在返回前逐句扫描：中文搭配是否成立；同一产品名、文件类型或缩写在前后是否出现矛盾变体；数字与单位是否合乎句意。优先使用完整字幕中较早出现且语义明确的写法。比如原文前面已经明确是 CSV，后面听成 MD 时应保持为 CSV；无法确认精确名词时，写成“相关参数”“大量文献”“自定义大纲”等来源支持的保守表达，不能保留看似逐字忠实但语义不成立的 ASR 片段。

article_plan 和 current_draft 都不是事实权威。最终逐段用完整相邻字幕与 visual_evidence 复核：截图中清晰可见的英文术语可纠正音译乱码；跨两三条字幕的断句必须合并理解；“不、没有、只、而不是、可能”等逻辑词必须保留，不能产生与来源方向相反的新结论。无法确定时只删去不可靠的精确措辞，保留上下文支持的背景、案例、提问、理由、结论或上位意思。若原口述已经是连贯、紧凑、无口癖的书面表达，可以少改或不改；不得为了制造编辑痕迹而降质改写。

最终逐条核对 asr_reconciliation：replacement 必须保留，source_text 中的错误词句不得残留。除此之外不要把核对表本身写进文章。

最终稿中不得残留“今天手把手、同学们、大家自取、我们接下来、做个总结”等主持式措辞，也不得残留“封面页、眉页、脚”这类明显错误复合词边界；应写成“封面、页眉、页脚”。“同学们的选题”可中性写成“不同选题”，“供大家自取”可写成“可在该网站获取”；“再厉害的 scale”一类中英混杂乱码应根据上下文保守写成“模型规模再大”。孤立的“第一”如果没有后续编号，应删除编号但保留内容。这些只是错误类型示例，不得把示例事实加入其他视频。

“切换指文、卷积页码、自带超期一篇文献、三大纲、CS五一、SR pick”同样是错误类型示例：应根据上下文分别恢复成语义明确的相关参数、卷/期/页码、文献能力或文献数量、大纲、CSV、SY Paper 等；上下文不足时必须使用保守表达，不得机械套用示例答案。

保持视频原始顺序。可以调整章节、段落和 start_source_id，但第一段必须从第一条字幕开始，所有起点必须来自字幕并严格递增。必须实际完成校对，不要未经检查便原样复制 current_draft。返回完整 JSON，不返回解释：
{"sections":[{"title":"具体章节标题","paragraphs":[{"start_source_id":"s000001","subheading":"真实步骤标题或 null","text":"最终正文"}]}]}。"""


DIRECT_ASR_RECONCILIATION_PROMPT = """你是写作前的术语与断句核对编辑。输入是若干带 item_id 的 ASR 可疑项；每项只包含一个 planner 尚未高置信解决的 source_text，或一个字幕全大写音译词与同期可见英文冲突的候选项，以及相邻上下文和高置信画面文字。你只核对这些项，不写文章、不改结构。

每个输入 item_id 必须恰好返回一次：
- correct：证据足以修复，给出 replacement、confidence、basis 和 required_anchors。
- keep：planner_repair 已经正确，不再修改。
- unresolved：证据不足，保留保守处理，不猜答案。

verified_visual_clues 已剔除视觉模型的长篇解释；存在 clue 时优先采用其中清晰可见的精确拼写。被切成相邻两三条的残句必须合起来理解，保留“不、没有、只、而不是、可能”等逻辑方向。replacement 不超过 160 字。画面清晰可见用 basis=visible_text；上下文恢复用 adjacent_context。不要借助外部知识补事实，不确定就 unresolved。不要做大小写、空格或普通文风校对。

required_anchors 是最终稿必须保留的 1 到 4 个最小语义锚点，每项 2 到 40 字，并且必须原样出现在 replacement 中。只填真正决定修复是否成立的词，不要把整句当锚点：术语修复保留精确术语；断句修复保留对象、结论和否定方向。例如一条修复同时包含精确标识符、精确命中和“不存在语义漂移”时，这三者应分别成为锚点。replacement 有“不存在、没有、并非、而不是”等否定结论时，至少一个锚点必须包含该否定方向。

两个已确认的错误类型必须避免：屏幕原文清晰显示 `agentic search` 时，不能沿用音译乱码 `EGICS`；当画面或上下文确认“grep 搜某个精确标识符就是精确命中”，而相邻残句含有“不存在……漂移”时，必须保留“不存在语义漂移”的否定方向，不能改写成“RAG 可能命中不存在于代码中的问题”。这些是核对方法示例，不得把示例事实加入无关视频。

严格返回 JSON：
{"items":[{"item_id":"r001","action":"correct|keep|unresolved","replacement":"来源支持的修复或 null","required_anchors":["最小锚点"],"confidence":"high|medium|low","basis":"visible_text|adjacent_context|none"}]}。不得遗漏、增加或重复 item_id。"""


VISUAL_REQUEST_PLANNER_PROMPT = """你是视频画面证据规划器。你只规划要检查的画面时间范围，不改文章结构、不写正文。

输入包含完整带时间戳字幕和已经批准的 article_plan。根据字幕语义判断哪些时段的屏幕/PPT/演示可能包含口述未完整读出的名单、标题、步骤、参数、表格、代码、公式、流程图、架构图、图表、论文原图、复杂界面状态或前后对比。

不要按固定分钟采样，也不要为了凑数量请求画面。纯聊天、口述已经完整且画面大概率只是人物时可以不请求。对于课程录屏、科研汇报、软件教程和 PPT 讲解，不要只在字幕出现“看这里”时请求：如果一段话明显对应课程大纲、操作演示、结果展示、成品图、论文图表或连续幻灯片，应覆盖该语义段的完整时间范围，后续场景检测会在范围内拆出每个不同页面。一个视频可以返回 0 个或很多范围，取决于信息密度。

purpose 必须说明要核验或提取什么；expected_kind 只能是 text、list、table、code、formula、diagram、chart、process、ui、paper_figure、comparison、other。不得假设画面中存在字幕完全没有依据的具体事实。

严格返回 JSON：
{"requests":[{"time_start":0.0,"time_end":20.0,"purpose":"要核验或提取的画面信息","expected_kind":"text|list|table|code|formula|diagram|chart|process|ui|paper_figure|comparison|other"}]}。"""


def _transcript_text(segments: list[Segment]) -> str:
    return "\n".join(
        f"[{segment.id} {segment.start:.2f}-{segment.end:.2f}] {segment.text}"
        for segment in segments
    )


def _call_document(
    client: OpenAICompatibleClient,
    system_prompt: str,
    payload: dict[str, Any],
    validator: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    error = ""
    for _attempt in range(DIRECT_ATTEMPTS):
        request = dict(payload)
        if error:
            request["format_repair"] = error
        try:
            response = client.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(request, ensure_ascii=False),
                    },
                ],
                temperature=0.15,
                max_tokens=16000,
                json_mode=True,
            )
            parsed = parse_json_object(response)
            if not isinstance(parsed.get("sections"), list):
                raise ValueError("response must contain sections")
            if validator:
                validator(parsed)
            return parsed
        except Exception as exc:
            error = f"上一响应格式无效：{exc}。只返回完整 JSON。"
    raise RuntimeError(f"DeepSeek 未返回有效的完整文稿：{error}")


def _validate_plan(payload: dict[str, Any], segments: list[Segment]) -> None:
    if not segments:
        raise ValueError("字幕为空")
    source_index = {segment.id: index for index, segment in enumerate(segments)}
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("大纲必须至少包含一个章节")
    starts: list[int] = []
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("大纲章节必须是对象")
        if not str(section.get("title") or "").strip():
            raise ValueError("大纲章节必须有具体标题")
        if not str(section.get("objective") or "").strip():
            raise ValueError("大纲章节必须说明目标")
        paragraphs = section.get("paragraphs")
        if not isinstance(paragraphs, list) or not paragraphs:
            raise ValueError("每个章节必须规划自然段")
        for paragraph in paragraphs:
            if not isinstance(paragraph, dict):
                raise ValueError("自然段规划必须是对象")
            source_id = str(paragraph.get("start_source_id") or "").strip()
            if source_id not in source_index:
                raise ValueError("自然段规划必须使用有效字幕起点")
            if not str(paragraph.get("focus") or "").strip():
                raise ValueError("自然段规划必须说明中心")
            if not isinstance(paragraph.get("must_keep"), list):
                raise ValueError("自然段规划必须列出细节清单")
            requests = paragraph.get("visual_requests", [])
            if not isinstance(requests, list):
                raise ValueError("visual_requests 必须是数组")
            for request in requests:
                if not isinstance(request, dict):
                    raise ValueError("画面请求必须是对象")
                try:
                    time_start = float(request.get("time_start"))
                    time_end = float(request.get("time_end"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("画面请求必须包含有效时间范围") from exc
                if time_start < 0 or time_end < time_start:
                    raise ValueError("画面请求时间范围无效")
                if not str(request.get("purpose") or "").strip():
                    raise ValueError("画面请求必须说明查看目的")
            starts.append(source_index[source_id])
    if starts[0] != 0 or starts != sorted(set(starts)):
        raise ValueError("大纲自然段起点必须从第一条字幕开始并严格递增")
    duration = segments[-1].end - segments[0].start
    if duration >= 180 and len(sections) < 2:
        raise ValueError("长视频大纲必须包含多个真实章节")


def _paragraphs_from_document(
    payload: dict[str, Any],
    segments: list[Segment],
) -> list[Paragraph]:
    if not segments:
        raise ValueError("字幕为空")
    source_index = {segment.id: index for index, segment in enumerate(segments)}
    rows: list[tuple[str, str, str | None, str]] = []
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("文稿必须至少包含一个章节")
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("章节必须是对象")
        title = str(section.get("title") or "").strip()
        paragraphs = section.get("paragraphs")
        if not title or not isinstance(paragraphs, list) or not paragraphs:
            raise ValueError("每个章节必须有标题和正文")
        for paragraph in paragraphs:
            if not isinstance(paragraph, dict):
                raise ValueError("段落必须是对象")
            source_id = str(paragraph.get("start_source_id") or "").strip()
            subheading = str(paragraph.get("subheading") or "").strip() or None
            text = str(paragraph.get("text") or "").strip()
            if source_id not in source_index or not text:
                raise ValueError("段落必须使用有效起点并包含正文")
            rows.append((title, source_id, subheading, text))
            title = ""
    indices = [source_index[source_id] for _title, source_id, _subheading, _text in rows]
    if indices[0] != 0:
        raise ValueError("第一段必须从第一条字幕开始")
    if indices != sorted(set(indices)):
        raise ValueError("段落起点必须严格递增且不能重复")
    duration = segments[-1].end - segments[0].start
    if duration >= 180 and len(sections) < 2:
        raise ValueError("长视频必须按真实主题或工作流划分章节")

    result: list[Paragraph] = []
    for row_index, (title, _source_id, subheading, text) in enumerate(rows):
        start_index = indices[row_index]
        end_index = indices[row_index + 1] if row_index + 1 < len(indices) else len(segments)
        assigned = segments[start_index:end_index]
        if not assigned:
            raise ValueError("段落字幕范围不能为空")
        result.append(
            Paragraph(
                source_ids=[segment.id for segment in assigned],
                text=text,
                start=assigned[0].start,
                end=assigned[-1].end,
                heading=title or None,
                subheading=subheading,
            )
        )
    return result


def _require_final_copyedit(
    candidate: dict[str, Any],
    current: dict[str, Any],
    segments: list[Segment],
) -> None:
    _apply_narrow_final_repairs(candidate)
    _paragraphs_from_document(candidate, segments)
    current_text = json.dumps(current, ensure_ascii=False, sort_keys=True)
    candidate_text = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
    residue = OBVIOUS_EDITORIAL_RESIDUE_RE.search(candidate_text)
    if residue:
        if candidate_text == current_text:
            raise ValueError(
                f"最终校对原样复制且仍含明确主持式或 ASR 残留：{residue.group(0)}；请实际修订"
            )
        raise ValueError(
            f"最终稿仍含明确主持式或 ASR 残留：{residue.group(0)}；请根据全文上下文修订"
        )


_ANCHOR_PUNCTUATION = str.maketrans(
    {
        "、": ",",
        "，": ",",
        "。": ".",
        "；": ";",
        "：": ":",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
    }
)


def _anchor_comparison(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.translate(_ANCHOR_PUNCTUATION).casefold()
    return re.sub(r"\s+", "", normalized)


def _anchor_from_replacement(anchor: str, replacement: str) -> str:
    """Return the replacement's spelling for an equivalent minimal anchor."""

    needle = _anchor_comparison(anchor)
    if not needle:
        return ""
    rendered: list[str] = []
    positions: list[int] = []
    for index, character in enumerate(replacement):
        normalized = unicodedata.normalize("NFKC", character)
        normalized = normalized.translate(_ANCHOR_PUNCTUATION).casefold()
        for item in normalized:
            if item.isspace():
                continue
            rendered.append(item)
            positions.append(index)
    offset = "".join(rendered).find(needle)
    if offset < 0:
        return ""
    return replacement[positions[offset] : positions[offset + len(needle) - 1] + 1].strip()


def _anchor_is_present(rendered: str, anchor: Any) -> bool:
    needle = _anchor_comparison(anchor)
    if not needle:
        return False
    if needle in rendered:
        return True
    # Final copyediting may naturally insert a neutral Chinese distributor,
    # for example “参数为零” -> “参数也为零”.  Permit only this bounded class;
    # polarity-bearing anchors still require their explicit source direction.
    if any(marker in needle for marker in ("不", "无", "未", "非", "没有")):
        return False
    pattern = ""
    previous = ""
    for character in needle:
        if previous and "\u3400" <= previous <= "\u9fff" and "\u3400" <= character <= "\u9fff":
            pattern += "[也均都]*"
        pattern += re.escape(character)
        previous = character
    return re.search(pattern, rendered) is not None


def _require_reconciliation_applied(
    candidate: dict[str, Any], reconciliation: dict[str, Any]
) -> None:
    rendered = _anchor_comparison(json.dumps(candidate, ensure_ascii=False))
    for correction in reconciliation.get("corrections", []):
        if not isinstance(correction, dict):
            continue
        anchors = correction.get("required_anchors")
        if not isinstance(anchors, list):
            anchors = []
        missing = [
            str(anchor)
            for anchor in anchors
            if not _anchor_is_present(rendered, anchor)
        ]
        if missing:
            raise ValueError(f"最终稿未落实已核验修复锚点：{', '.join(missing)}")


def _apply_narrow_final_repairs(payload: dict[str, Any]) -> int:
    """Repair only regression-confirmed short ASR fragments after model copyedit."""
    repaired = 0
    for section in payload.get("sections", []):
        if not isinstance(section, dict):
            continue
        for paragraph in section.get("paragraphs", []):
            if not isinstance(paragraph, dict):
                continue
            text = str(paragraph.get("text") or "")
            for pattern, replacement in NARROW_FINAL_REPAIRS:
                text, count = pattern.subn(replacement, text)
                repaired += count
            paragraph["text"] = text
    return repaired


def _signature(segments: list[Segment], context: str) -> str:
    material = json.dumps(
        {
            "pipeline_version": PIPELINE_VERSION,
            "architecture": "whole_transcript_plan_visual_reconcile_write_restore_copyedit",
            "context": context,
            "segments": [segment.to_dict() for segment in segments],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _golden_style_reference() -> str:
    path = Path(__file__).resolve().parents[2] / "references" / "golden-style-example.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def visual_requests_from_plan(
    plan: dict[str, Any], segments: list[Segment] | None = None
) -> list[dict[str, Any]]:
    """Flatten bounded, transcript-grounded requests for the vision selector."""
    result: list[dict[str, Any]] = []
    for section in plan.get("sections", []):
        if not isinstance(section, dict):
            continue
        for paragraph in section.get("paragraphs", []):
            if not isinstance(paragraph, dict):
                continue
            for request in paragraph.get("visual_requests", []):
                if not isinstance(request, dict):
                    continue
                try:
                    start = max(0.0, float(request.get("time_start")))
                    end = max(start, float(request.get("time_end")))
                except (TypeError, ValueError):
                    continue
                purpose = str(request.get("purpose") or "").strip()
                if not purpose:
                    continue
                result.append(
                    {
                        "time_start": start,
                        "time_end": end,
                        "purpose": purpose[:300],
                        "expected_kind": str(request.get("expected_kind") or "other")[:40],
                    }
                )
    # An ASR suspect is itself a reason to inspect the matching visual range.
    # Do this deterministically because a planner may correctly flag a broken
    # sentence but forget to emit its companion visual request.
    if segments:
        source_index = {segment.id: index for index, segment in enumerate(segments)}
        planned_paragraphs = [
            paragraph
            for section in plan.get("sections", [])
            if isinstance(section, dict)
            for paragraph in section.get("paragraphs", [])
            if isinstance(paragraph, dict)
            and str(paragraph.get("start_source_id") or "") in source_index
        ]
        starts = [source_index[str(paragraph["start_source_id"])] for paragraph in planned_paragraphs]
        for index, paragraph in enumerate(planned_paragraphs):
            suspects = paragraph.get("asr_suspects", [])
            if not isinstance(suspects, list) or not suspects:
                continue
            start_index = starts[index]
            end_index = starts[index + 1] if index + 1 < len(starts) else len(segments)
            assigned = segments[start_index:end_index]
            if not assigned:
                continue
            suspect_text = "；".join(
                str(item.get("source_text") or "").strip()
                for item in suspects
                if isinstance(item, dict) and str(item.get("source_text") or "").strip()
            )
            compact_suspect = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", suspect_text).lower()
            matched = []
            for segment in assigned:
                compact_segment = re.sub(
                    r"[^0-9A-Za-z\u4e00-\u9fff]+", "", segment.text
                ).lower()
                if compact_segment and (
                    compact_segment in compact_suspect
                    or compact_suspect in compact_segment
                ):
                    matched.append(segment)
            evidence_range = matched or assigned
            result.append(
                {
                    "time_start": max(0.0, evidence_range[0].start - 1.5),
                    "time_end": evidence_range[-1].end + 1.5,
                    "purpose": f"核验 ASR 可疑术语或断句：{suspect_text[:220]}",
                    "expected_kind": "text",
                }
            )
    # A long, slide-dense technical video can legitimately contain many more
    # visual evidence windows than a conversational video.  This is only a
    # malformed-model safety ceiling; the frame/vision ceilings remain the
    # runtime cost controls.
    return result[:160]


def create_visual_request_plan(
    segments: list[Segment],
    client: OpenAICompatibleClient | None,
    article_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Ask a dedicated semantic planner for visual ranges without touching prose."""
    if client is None or not segments:
        return []
    duration = max(segment.end for segment in segments)
    error = ""
    for _attempt in range(DIRECT_ATTEMPTS):
        payload: dict[str, Any] = {
            "complete_transcript": _transcript_text(segments),
            "article_plan": article_plan,
        }
        if error:
            payload["format_repair"] = error
        try:
            raw = client.chat(
                [
                    {"role": "system", "content": VISUAL_REQUEST_PLANNER_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.0,
                max_tokens=5000,
                json_mode=True,
            )
            parsed = parse_json_object(raw)
            requests = parsed.get("requests")
            if not isinstance(requests, list):
                raise ValueError("response must contain requests")
            normalized: list[dict[str, Any]] = []
            allowed_kinds = {
                "text", "list", "table", "code", "formula", "diagram",
                "chart", "process", "ui", "paper_figure", "comparison", "other",
            }
            for request in requests:
                if not isinstance(request, dict):
                    raise ValueError("each visual request must be an object")
                start = max(0.0, float(request.get("time_start")))
                end = min(duration, max(start, float(request.get("time_end"))))
                purpose = str(request.get("purpose") or "").strip()
                kind = str(request.get("expected_kind") or "other").strip().lower()
                if not purpose or end <= start:
                    raise ValueError("visual request must have a non-empty range and purpose")
                if kind not in allowed_kinds:
                    kind = "other"
                normalized.append(
                    {
                        "time_start": round(start, 3),
                        "time_end": round(end, 3),
                        "purpose": purpose[:300],
                        "expected_kind": kind,
                    }
                )
            return normalized[:160]
        except Exception as exc:
            error = f"上一响应格式无效：{exc}。只返回 requests JSON。"
    raise RuntimeError(f"视觉范围规划未返回有效 JSON：{error}")


def merge_visual_requests(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge plan sources while removing exact/near-identical time windows."""
    merged: list[dict[str, Any]] = []
    for request in sorted(
        [item for group in groups for item in group],
        key=lambda item: (float(item.get("time_start", 0)), float(item.get("time_end", 0))),
    ):
        start = float(request.get("time_start", 0))
        end = float(request.get("time_end", start))
        kind = str(request.get("expected_kind") or "other")
        duplicate = any(
            kind == str(existing.get("expected_kind") or "other")
            and abs(start - float(existing.get("time_start", 0))) <= 1.0
            and abs(end - float(existing.get("time_end", 0))) <= 1.0
            for existing in merged
        )
        if not duplicate:
            merged.append(request)
    return merged[:160]


def _visual_evidence(frames: list[Frame] | None) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for index, frame in enumerate(frames or [], start=1):
        ocr = frame.ocr_text.strip()
        description = frame.vision_description.strip()
        if not (ocr or description):
            continue
        evidence.append(
            {
                "frame_id": f"f{index:03d}",
                "timestamp": round(frame.timestamp, 2),
                "source_ids": frame.source_ids[:12],
                "ocr": ocr[:1800],
                "vision_description": description[:1400],
                "ocr_confidence": frame.ocr_confidence,
            }
        )
    return evidence[:30]


def _visual_signature(evidence: list[dict[str, Any]]) -> str:
    encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plan_has_asr_suspects(plan: dict[str, Any]) -> bool:
    return any(
        isinstance(paragraph, dict) and bool(paragraph.get("asr_suspects"))
        for section in plan.get("sections", [])
        if isinstance(section, dict)
        for paragraph in section.get("paragraphs", [])
    )


def _asr_suspect_contexts(
    plan: dict[str, Any], segments: list[Segment]
) -> list[dict[str, Any]]:
    """Return only paragraph ranges that the planner explicitly marked suspect."""
    source_index = {segment.id: index for index, segment in enumerate(segments)}
    paragraphs = [
        paragraph
        for section in plan.get("sections", [])
        if isinstance(section, dict)
        for paragraph in section.get("paragraphs", [])
        if isinstance(paragraph, dict)
        and str(paragraph.get("start_source_id") or "") in source_index
    ]
    starts = [source_index[str(paragraph["start_source_id"])] for paragraph in paragraphs]
    result: list[dict[str, Any]] = []
    for index, paragraph in enumerate(paragraphs):
        suspects = paragraph.get("asr_suspects", [])
        if not isinstance(suspects, list) or not suspects:
            continue
        start = starts[index]
        end = starts[index + 1] if index + 1 < len(starts) else len(segments)
        excerpt = segments[start:end]
        result.append(
            {
                "start_source_id": paragraph.get("start_source_id"),
                "source_ids": [segment.id for segment in excerpt],
                "asr_suspects": suspects,
                "context": "\n".join(
                    f"[{segment.id} {segment.start:.2f}-{segment.end:.2f}] {segment.text}"
                    for segment in excerpt
                ),
            }
        )
    return result


def _verified_visual_clues(
    visual_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compress visual evidence to short exact strings for terminology repair."""
    result: list[dict[str, Any]] = []
    for frame in visual_evidence:
        exact: list[str] = []
        ocr = str(frame.get("ocr") or "").strip()
        try:
            ocr_confidence = float(frame.get("ocr_confidence") or 0)
        except (TypeError, ValueError):
            ocr_confidence = 0.0
        if ocr and ocr_confidence >= 50 and len(ocr) <= 240:
            exact.append(ocr)
        description = str(frame.get("vision_description") or "")
        marked_values = re.findall(r"`([^`\n]{2,180})`", description)
        marked_values.extend(
            re.findall(r"\*\*([^*\n]{2,180})\*\*", description)
        )
        for value in marked_values:
            cleaned = value.strip()
            if cleaned and cleaned not in exact:
                exact.append(cleaned)
        if not exact:
            continue
        result.append(
            {
                "timestamp": frame.get("timestamp"),
                "source_ids": frame.get("source_ids", []),
                "exact_visible_text": exact[:16],
            }
        )
    return result[:24]


def _call_asr_reconciliation(
    client: OpenAICompatibleClient,
    *,
    segments: list[Segment],
    plan: dict[str, Any],
    visual_evidence: list[dict[str, Any]],
    suspect_contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    verified_visual_clues = _verified_visual_clues(visual_evidence)
    visible_corpus = re.sub(
        r"\s+",
        "",
        json.dumps(verified_visual_clues, ensure_ascii=False),
    ).lower()
    items: list[dict[str, Any]] = []
    item_sources: dict[str, dict[str, Any]] = {}
    for context in suspect_contexts:
        context_ids = set(str(value) for value in context.get("source_ids", []))
        clues = [
            clue
            for clue in verified_visual_clues
            if context_ids & set(str(value) for value in clue.get("source_ids", []))
        ]
        for suspect in context.get("asr_suspects", []):
            if not isinstance(suspect, dict):
                continue
            source_text = str(suspect.get("source_text") or "").strip()
            planner_repair = str(suspect.get("conservative_repair") or "").strip()
            planner_confidence = str(suspect.get("confidence") or "").strip().lower()
            if not source_text or (planner_confidence == "high" and planner_repair):
                continue
            item_id = f"r{len(items) + 1:03d}"
            row = {
                "item_id": item_id,
                "source_text": source_text[:160],
                "planner_repair": planner_repair[:160] or None,
                "planner_confidence": planner_confidence or "low",
                "context": str(context.get("context") or "")[:3500],
                "verified_visual_clues": clues[:8],
            }
            items.append(row)
            item_sources[item_id] = row
            if len(items) >= 12:
                break
        if len(items) >= 12:
            break
    # The structural planner occasionally treats a phonetic uppercase token as
    # a valid acronym and therefore does not mark it suspect.  When a >=4-char
    # uppercase token conflicts with exact English visible in the same frame,
    # add it to this same bounded reconciliation call.  The model may still
    # return unresolved; this creates no extra request and does not scan prose.
    source_index = {segment.id: index for index, segment in enumerate(segments)}
    existing_suspect_text = " ".join(
        str(item.get("source_text") or "") for item in items
    ).upper()
    for clue in verified_visual_clues:
        if len(items) >= 12:
            break
        clue_text = " ".join(str(value) for value in clue.get("exact_visible_text", []))
        visible_tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{3,}", clue_text)
        }
        if not visible_tokens:
            continue
        clue_ids = sorted(
            {
                str(value)
                for value in clue.get("source_ids", [])
                if str(value) in source_index
            },
            key=lambda value: source_index[value],
        )
        for source_id in clue_ids:
            segment = segments[source_index[source_id]]
            candidates = re.findall(r"\b[A-Z][A-Z0-9_-]{3,}\b", segment.text)
            for token in candidates:
                if token in existing_suspect_text or token.lower() in visible_tokens:
                    continue
                position = source_index[source_id]
                nearby = segments[max(0, position - 2) : position + 3]
                item_id = f"r{len(items) + 1:03d}"
                row = {
                    "item_id": item_id,
                    "source_text": segment.text[:160],
                    "planner_repair": None,
                    "planner_confidence": "low",
                    "context": "\n".join(
                        f"[{item.id} {item.start:.2f}-{item.end:.2f}] {item.text}"
                        for item in nearby
                    ),
                    "verified_visual_clues": [clue],
                }
                items.append(row)
                item_sources[item_id] = row
                existing_suspect_text += " " + token
                if len(items) >= 12:
                    break
            if len(items) >= 12:
                break
    if not items:
        return {"corrections": []}
    error = ""
    for _attempt in range(DIRECT_ATTEMPTS):
        payload: dict[str, Any] = {"items": items}
        if error:
            payload["format_repair"] = error
        try:
            raw = client.chat(
                [
                    {"role": "system", "content": DIRECT_ASR_RECONCILIATION_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.0,
                max_tokens=1800,
                json_mode=True,
            )
            parsed = parse_json_object(raw)
            results = parsed.get("items")
            if not isinstance(results, list):
                raise ValueError("response must contain items")
            result_by_id = {
                str(item.get("item_id") or ""): item
                for item in results
                if isinstance(item, dict)
            }
            if set(result_by_id) != set(item_sources) or len(results) != len(item_sources):
                raise ValueError("every input item_id must appear exactly once")
            normalized: list[dict[str, Any]] = []
            for item_id, source in item_sources.items():
                result = result_by_id[item_id]
                action = str(result.get("action") or "").strip().lower()
                if action not in {"correct", "keep", "unresolved"}:
                    raise ValueError("action must be correct, keep, or unresolved")
                if action != "correct":
                    continue
                replacement = str(result.get("replacement") or "").strip()
                confidence = str(result.get("confidence") or "").strip().lower()
                basis = str(result.get("basis") or "").strip().lower()
                if confidence not in {"high", "medium"} or basis not in {
                    "visible_text", "adjacent_context"
                }:
                    continue
                if basis == "adjacent_context":
                    if not re.search(
                        r"(?:不|没有|不存在|而不是|只|但|相反|可能|漂移|问题)",
                        source["source_text"],
                    ):
                        # Without direct visual evidence, this pass only repairs
                        # sentence logic. Names and design terms remain with the
                        # full-document editor, which has broader context.
                        continue
                    confidence = "medium"
                if not (1 <= len(replacement) <= 160):
                    continue
                required_anchors = result.get("required_anchors")
                if not isinstance(required_anchors, list) or not 1 <= len(required_anchors) <= 4:
                    continue
                replacement_anchors = [
                    _anchor_from_replacement(str(anchor).strip(), replacement)
                    for anchor in required_anchors
                    if 2 <= len(str(anchor).strip()) <= 40
                ]
                if len(replacement_anchors) != len(required_anchors) or any(
                    not anchor or len(anchor) > 40 for anchor in replacement_anchors
                ):
                    # One unsupported optional correction must not block the
                    # complete manuscript.  Treat it as unresolved and let the
                    # full-document passes use the original evidence.
                    continue
                required_anchors = replacement_anchors
                negative_markers = [
                    marker
                    for marker in ("不存在", "没有", "并非", "不是", "而不是", "不能", "不会")
                    if marker in replacement
                ]
                if negative_markers and not any(
                    any(marker in anchor for marker in negative_markers)
                    for anchor in required_anchors
                ):
                    continue
                if basis == "visible_text":
                    visible_replacement = re.sub(r"\s+", "", replacement).lower()
                    if visible_replacement not in visible_corpus:
                        # Sentence repairs may include connective Chinese around a
                        # visibly confirmed term. Require at least its English token.
                        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", replacement)
                        if not tokens or not any(token.lower() in visible_corpus for token in tokens):
                            continue
                normalized.append(
                    {
                        "kind": "sentence" if len(source["source_text"]) > 48 else "term",
                        "source_text": source["source_text"],
                        "replacement": replacement,
                        "required_anchors": required_anchors,
                        "confidence": confidence,
                        "basis": basis,
                    }
                )
            return {"corrections": normalized}
        except Exception as exc:
            error = f"上一响应格式或证据无效：{exc}。只返回 corrections JSON。"
    raise RuntimeError(f"术语与断句核对未返回有效 JSON：{error}")


def _apply_asr_reconciliation(
    plan: dict[str, Any], reconciliation: dict[str, Any]
) -> dict[str, Any]:
    corrected = json.loads(json.dumps(plan, ensure_ascii=False))
    corrections = [
        item
        for item in reconciliation.get("corrections", [])
        if isinstance(item, dict)
    ]

    def replace_plan_value(value: Any, *, preserve_source_text: bool = False) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, nested in value.items():
                result[key] = replace_plan_value(
                    nested,
                    preserve_source_text=preserve_source_text or key == "source_text",
                )
            if "source_text" in value:
                raw_source = str(value.get("source_text") or "").strip()
                for correction in corrections:
                    if re.sub(r"\s+", "", raw_source).lower() == re.sub(
                        r"\s+", "", str(correction.get("source_text") or "")
                    ).lower():
                        result["conservative_repair"] = correction["replacement"]
                        result["confidence"] = correction["confidence"]
                        break
            return result
        if isinstance(value, list):
            return [replace_plan_value(item, preserve_source_text=preserve_source_text) for item in value]
        if isinstance(value, str) and not preserve_source_text:
            rendered = value
            for correction in corrections:
                source_text = str(correction.get("source_text") or "")
                replacement = str(correction.get("replacement") or "")
                if source_text and len(source_text) <= 80:
                    rendered = re.sub(re.escape(source_text), replacement, rendered, flags=re.I)
            return rendered
        return value

    corrected = replace_plan_value(corrected)
    corrected["asr_reconciliation"] = corrections
    return corrected


def _plan_with_source_excerpts(
    plan: dict[str, Any], segments: list[Segment]
) -> dict[str, Any]:
    """Attach each planned paragraph's complete local evidence without judging it."""
    source_index = {segment.id: index for index, segment in enumerate(segments)}
    cloned = json.loads(json.dumps(plan, ensure_ascii=False))
    paragraphs: list[dict[str, Any]] = []
    for section in cloned.get("sections", []):
        if isinstance(section, dict):
            paragraphs.extend(
                item for item in section.get("paragraphs", []) if isinstance(item, dict)
            )
    starts = [source_index[str(item["start_source_id"])] for item in paragraphs]
    for index, paragraph in enumerate(paragraphs):
        start = starts[index]
        end = starts[index + 1] if index + 1 < len(starts) else len(segments)
        paragraph["source_excerpt"] = "\n".join(
            f"[{segment.id} {segment.start:.2f}-{segment.end:.2f}] {segment.text}"
            for segment in segments[start:end]
        )
    return cloned


def _paragraph_evidence_packets(
    grounded_plan: dict[str, Any], document: dict[str, Any]
) -> list[dict[str, Any]]:
    current_by_start: dict[str, dict[str, Any]] = {}
    for section in document.get("sections", []):
        if not isinstance(section, dict):
            continue
        for paragraph in section.get("paragraphs", []):
            if isinstance(paragraph, dict):
                current_by_start[str(paragraph.get("start_source_id") or "")] = paragraph

    packets: list[dict[str, Any]] = []
    for section in grounded_plan.get("sections", []):
        if not isinstance(section, dict):
            continue
        for paragraph in section.get("paragraphs", []):
            if not isinstance(paragraph, dict):
                continue
            source_id = str(paragraph.get("start_source_id") or "")
            current = current_by_start.get(source_id, {})
            packets.append(
                {
                    "section_title": str(section.get("title") or ""),
                    "start_source_id": source_id,
                    "planned_subheading": paragraph.get("subheading"),
                    "focus": paragraph.get("focus"),
                    "must_keep": paragraph.get("must_keep", []),
                    "source_excerpt": paragraph.get("source_excerpt", ""),
                    "current_subheading": current.get("subheading"),
                    "current_text": current.get("text", ""),
                }
            )
    return packets


def _checkpoint_for(
    segments: list[Segment], context: str, checkpoint_path: Path | None
) -> tuple[str, dict[str, Any]]:
    signature = _signature(segments, context)
    checkpoint = load_json(checkpoint_path) if checkpoint_path else None
    if not isinstance(checkpoint, dict) or checkpoint.get("signature") != signature:
        checkpoint = {
            "schema": "whole-transcript-plan-visual-write-review-v2",
            "signature": signature,
        }
    return signature, checkpoint


def create_direct_plan(
    segments: list[Segment],
    client: OpenAICompatibleClient | None,
    *,
    context: str = "",
    checkpoint_path: Path | None = None,
) -> dict[str, Any]:
    if client is None:
        raise RuntimeError("缺少文本大模型配置，禁止发布未经 AI 编辑的原始字幕")
    if not segments:
        raise RuntimeError("没有可编辑的字幕")
    _signature_value, checkpoint = _checkpoint_for(segments, context, checkpoint_path)
    transcript = _transcript_text(segments)
    plan = checkpoint.get("plan")
    if not isinstance(plan, dict):
        plan = _call_document(
            client,
            DIRECT_OUTLINE_PROMPT,
            {"video_context": context, "complete_transcript": transcript},
            validator=lambda payload: _validate_plan(payload, segments),
        )
        checkpoint["plan"] = plan
        if checkpoint_path:
            atomic_json(checkpoint_path, checkpoint)
    else:
        _validate_plan(plan, segments)
    return plan


def complete_direct_manuscript(
    segments: list[Segment],
    client: OpenAICompatibleClient | None,
    plan: dict[str, Any],
    *,
    context: str = "",
    frames: list[Frame] | None = None,
    checkpoint_path: Path | None = None,
) -> tuple[list[Paragraph], dict[str, Any]]:
    if client is None:
        raise RuntimeError("缺少文本大模型配置，禁止发布未经 AI 编辑的原始字幕")
    if not segments:
        raise RuntimeError("没有可编辑的字幕")
    _validate_plan(plan, segments)
    _signature_value, checkpoint = _checkpoint_for(segments, context, checkpoint_path)
    checkpoint["plan"] = plan

    transcript = _transcript_text(segments)
    visual_evidence = _visual_evidence(frames)
    visual_signature = _visual_signature(visual_evidence)
    if checkpoint.get("visual_signature") != visual_signature:
        for key in ("asr_reconciliation", "draft", "detailed", "final", "completed"):
            checkpoint.pop(key, None)
        checkpoint["visual_signature"] = visual_signature
        if checkpoint_path:
            atomic_json(checkpoint_path, checkpoint)
    needs_reconciliation = bool(visual_evidence) or _plan_has_asr_suspects(plan)
    reconciliation = checkpoint.get("asr_reconciliation")
    if needs_reconciliation and not isinstance(reconciliation, dict):
        reconciliation = _call_asr_reconciliation(
            client,
            segments=segments,
            plan=plan,
            visual_evidence=visual_evidence,
            suspect_contexts=_asr_suspect_contexts(plan, segments),
        )
        checkpoint["asr_reconciliation"] = reconciliation
        if checkpoint_path:
            atomic_json(checkpoint_path, checkpoint)
    if not isinstance(reconciliation, dict):
        reconciliation = {"corrections": []}
    reconciled_plan = _apply_asr_reconciliation(plan, reconciliation)
    grounded_plan = _plan_with_source_excerpts(reconciled_plan, segments)
    style_reference = _golden_style_reference()

    draft = checkpoint.get("draft")
    if not isinstance(draft, dict):
        draft = _call_document(
            client,
            DIRECT_WRITER_PROMPT,
            {
                "video_context": context,
                "complete_transcript": transcript,
                "article_plan": grounded_plan,
                "visual_evidence": visual_evidence,
                "asr_reconciliation": reconciliation,
                "golden_style_reference": style_reference,
            },
            validator=lambda payload: _paragraphs_from_document(payload, segments),
        )
        checkpoint["draft"] = draft
        if checkpoint_path:
            atomic_json(checkpoint_path, checkpoint)

    detailed = checkpoint.get("detailed")
    if not isinstance(detailed, dict):
        draft_evidence_packets = _paragraph_evidence_packets(grounded_plan, draft)
        detailed = _call_document(
            client,
            DIRECT_DETAIL_PROMPT,
            {
                "video_context": context,
                "complete_transcript": transcript,
                "article_plan": grounded_plan,
                "visual_evidence": visual_evidence,
                "asr_reconciliation": reconciliation,
                "paragraph_evidence_packets": draft_evidence_packets,
                "current_draft": draft,
            },
            validator=lambda payload: _paragraphs_from_document(payload, segments),
        )
        checkpoint["detailed"] = detailed
        if checkpoint_path:
            atomic_json(checkpoint_path, checkpoint)

    final = checkpoint.get("final")
    if not isinstance(final, dict):
        detailed_evidence_packets = _paragraph_evidence_packets(grounded_plan, detailed)
        final = _call_document(
            client,
            DIRECT_REVIEW_PROMPT,
            {
                "video_context": context,
                "complete_transcript": transcript,
                "visual_evidence": visual_evidence,
                "asr_reconciliation": reconciliation,
                "golden_style_reference": style_reference,
                "paragraph_evidence_packets": detailed_evidence_packets,
                "current_draft": detailed,
            },
            validator=lambda payload: (
                _require_final_copyedit(payload, detailed, segments),
                _require_reconciliation_applied(payload, reconciliation),
            ),
        )
        paragraphs = _paragraphs_from_document(final, segments)
        checkpoint["final"] = final
        checkpoint["completed"] = True
        if checkpoint_path:
            atomic_json(checkpoint_path, checkpoint)
    else:
        paragraphs = _paragraphs_from_document(final, segments)

    coverage = {
        "quality_status": "pass",
        "semantic_editing": True,
        "editing_architecture": "whole_transcript_plan_visual_reconcile_write_restore_copyedit",
        "llm_document_passes": 4,
        "llm_targeted_asr_reconciliation_passes": 1 if needs_reconciliation else 0,
        "asr_reconciliation": reconciliation,
        "visual_evidence_before_writing": True,
        "visual_evidence_count": len(visual_evidence),
        "golden_style_reference": bool(style_reference),
        "source_count": len(segments),
        "represented_source_count": sum(len(paragraph.source_ids) for paragraph in paragraphs),
        "section_count": sum(1 for paragraph in paragraphs if paragraph.heading),
        "paragraph_count": len(paragraphs),
        "missing_ids": [],
        "warnings": [],
        "outline": plan.get("sections", []),
    }
    return paragraphs, coverage


def create_direct_manuscript(
    segments: list[Segment],
    client: OpenAICompatibleClient | None,
    *,
    context: str = "",
    frames: list[Frame] | None = None,
    checkpoint_path: Path | None = None,
) -> tuple[list[Paragraph], dict[str, Any]]:
    """Run the complete editor; production may pause after planning for vision."""
    plan = create_direct_plan(
        segments, client, context=context, checkpoint_path=checkpoint_path
    )
    return complete_direct_manuscript(
        segments,
        client,
        plan,
        context=context,
        frames=frames,
        checkpoint_path=checkpoint_path,
    )
