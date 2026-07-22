from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

from vtm_core import PIPELINE_VERSION
from vtm_core.asr import FUNASR_BATCH_SECONDS, faster_whisper_model_path, normalize_segments, parse_srt
from vtm_core.bilibili import BilibiliClient
from vtm_core.configuration import (
    configuration_menu,
    platform_configuration,
    remove_secret,
    secret_store_path,
    set_secret,
)
from vtm_core.llm import OpenAICompatibleClient
from vtm_core.direct_manuscript import (
    DIRECT_DETAIL_PROMPT,
    DIRECT_OUTLINE_PROMPT,
    DIRECT_REVIEW_PROMPT,
    DIRECT_WRITER_PROMPT,
    _apply_narrow_final_repairs,
    _call_asr_reconciliation,
    _paragraphs_from_document,
    _require_final_copyedit,
    _require_reconciliation_applied,
    _verified_visual_clues,
    complete_direct_manuscript,
    create_direct_plan,
    create_direct_manuscript,
    create_visual_request_plan,
    merge_visual_requests,
    visual_requests_from_plan,
)
from vtm_core.douyin import (
    DouyinClient,
    DouyinSourceAdapter,
    DouyinVideoInfo,
    _media_url as validate_douyin_media_url,
    parse_douyin_router_data,
)
from vtm_core.models import Frame, InformationUnit, OutlineSection, Paragraph, Segment
from vtm_core.sources import (
    BilibiliSourceAdapter,
    SourceAdapter,
    SourceReference,
    adapter_by_platform,
    adapter_for,
)
from vtm_core.manuscript import (
    _adjudicate_final_audit,
    _anchor_supported as manuscript_anchor_supported,
    _anchors as manuscript_anchors,
    _extract_units,
    _plan_outline,
    _proofread_asr_residue,
    create_manuscript,
    manuscript_quality_report,
)
from vtm_core.output import compose_markdown, plan_frame_evidence, plan_frame_evidence_groups, update_indexes
from vtm_core.pipeline import (
    Options,
    _DocumentEditingClient,
    _align_document_images,
    _resume_checkpoint,
    _source_folder_name,
    run,
)
from vtm_core.bilibili import VideoInfo
from vtm_core.transcript import (
    BATCH_EDIT_MAX_ATTEMPTS,
    FAITHFUL_SYSTEM_PROMPT,
    FULL_MANUSCRIPT_REPAIR_ATTEMPTS,
    REFINEMENT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    _chunk_context,
    _load_checkpoint_chunks,
    _paragraphs_from_response,
    _proofread_asr_artifacts,
    _save_checkpoint_chunks,
    _validate_full_manuscript,
    edit_transcript,
    enrich_with_visual_evidence,
    exact_anchors,
    chunk_segments,
)
from vtm_core.tasks import get_task, list_tasks, reserve_task, update_task
from vtm_core.utils import safe_name
from vtm_core.visual import (
    DEFAULT_VISION_FRAME_BUDGET,
    MAX_ADAPTIVE_VISION_FRAME_BUDGET,
    MAX_PAID_VISION_REVIEWS_PER_MINUTE,
    adaptive_vision_frame_budget,
    asset_filename,
    candidate_times,
    average_hash,
    describe_if_needed,
    duplicate_distance_threshold,
    _parse_freeze_intervals,
    extract_useful_frames,
    hash_distance,
    ocr_text_is_usable,
    quality,
    recapture_retained_frames,
    refine_completion_timestamps,
    semantic_vision_frame_budget,
    vision_priority_ids,
    vision_priority_ids_for_requests,
)
from vtm_core.youtube import (
    YouTubeClient,
    YouTubeSourceAdapter,
    YouTubeVideoInfo,
    parse_youtube_json3,
    parse_youtube_vtt,
)
from vtm_core.xiaohongshu import (
    _XhsImageRedirectHandler,
    XiaohongshuSourceAdapter,
    parse_xhs_initial_state,
)
from vtm_core.web import (
    BilibiliDocumentSourceAdapter,
    GenericWebInfo,
    GenericWebSourceAdapter,
    _structured_article_metadata,
    canonicalize_web_url,
    parse_html_document,
    validate_public_url,
)
import vtm_core.zhihu as zhihu_module
from vtm_core.zhihu import ZhihuSourceAdapter
from video_manuscript import (
    bundle_job,
    cancel_job,
    confirm_bulk_delete,
    contract,
    delete_job,
    default_vault,
    format_progress,
    format_gateway_completion,
    find_existing_video_task,
    load_runtime_env,
    main,
    plan_bulk_delete,
    progress_label,
    restore_job,
    duplicate_skill_paths,
    GatewayProgressReporter,
    send_hermes_document,
    submit_detached,
    evaluate_text_core,
)


class FakeClient(OpenAICompatibleClient):
    def __init__(self, response: dict):
        self.response = response

    def chat(self, messages, **kwargs):
        return "```json\n" + json.dumps(self.response, ensure_ascii=False) + "\n```"


class SequenceClient(OpenAICompatibleClient):
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, **kwargs):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return "```json\n" + json.dumps(response, ensure_ascii=False) + "\n```"


class RawSequenceClient(SequenceClient):
    def chat(self, messages, **kwargs):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(response, str):
            return response
        return "```json\n" + json.dumps(response, ensure_ascii=False) + "\n```"


def manuscript_responses(
    text: str = "完整内容。", *, publication_copyedit: bool = False
) -> list[dict]:
    responses = [
        {"units": [{
            "start_source_id": "s000001", "action": "keep", "kind": "claim",
            "topic": "核心内容", "text": text, "details": [], "drop_reason": None,
        }]},
        {"corrections": []},
        {"sections": [{
            "start_unit_id": "u000001", "title": "核心内容说明",
            "objective": "完整说明视频内容", "format_hint": "prose",
        }]},
        {"paragraphs": [{"start_unit_id": "u000001", "text": text}]},
    ]
    if publication_copyedit:
        responses.append({"paragraphs": [{"start_unit_id": "u000001", "text": text}]})
        responses.append(
            {
                "checked_section_ids": ["sec001"],
                "corrections": [],
                "unresolved": [],
            }
        )
    responses.append(audit_pass("sec001"))
    if publication_copyedit:
        responses.append(
            {
                "checked_section_ids": ["sec001"],
                "corrections": [],
                "unresolved": [],
            }
        )
    return responses


def direct_manuscript_responses(text: str = "完整内容。") -> list[dict]:
    plan = {
        "sections": [
            {
                "title": "完整内容",
                "objective": "完整说明内容",
                "paragraphs": [
                    {
                        "start_source_id": "s000001",
                        "focus": "核心内容",
                        "must_keep": ["完整内容"],
                    }
                ],
            }
        ]
    }
    document = {
        "sections": [
            {
                "title": "完整内容",
                "paragraphs": [
                    {"start_source_id": "s000001", "text": text}
                ],
            }
        ]
    }
    return [plan, document, document, document]


def audit_pass(*section_ids: str) -> dict:
    return {
        "verdict": "pass",
        "section_reviews": [
            {"section_id": section_id, "status": "pass", "reason": "内容完整且已编辑"}
            for section_id in section_ids
        ],
        "issues": [],
    }


def audit_repair(section_statuses: dict[str, str], issues: list[dict]) -> dict:
    return {
        "verdict": "repair",
        "section_reviews": [
            {"section_id": section_id, "status": status, "reason": "需要定向修复" if status == "repair" else "通过"}
            for section_id, status in section_statuses.items()
        ],
        "issues": issues,
    }


class CoreTests(unittest.TestCase):
    def test_repository_installer_dry_run_is_safe_and_external_to_skill_contract(self):
        root = Path(__file__).resolve().parents[2]
        installer = root / "install.sh"
        self.assertTrue(installer.is_file())
        skill_before = (root / "SKILL.md").read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "agent-skills" / "manuloom"
            completed = subprocess.run(
                [
                    "sh",
                    str(installer),
                    "--skill-dir",
                    str(target),
                    "--skip-system-packages",
                    "--minimal",
                    "--dry-run",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("requirements.txt", completed.stdout)
            self.assertIn("ln -s", completed.stdout)
            self.assertIn("scripts/manuloom doctor", completed.stdout)
            self.assertFalse(target.exists())
        self.assertEqual((root / "SKILL.md").read_bytes(), skill_before)

    def test_deployment_document_lists_exact_prepared_model_ids(self):
        root = Path(__file__).resolve().parents[2]
        deployment = (root / "DEPLOYMENT.md").read_text(encoding="utf-8")
        from vtm_core.asr import FUNASR_MODEL, FUNASR_PUNC_MODEL, FUNASR_VAD_MODEL

        self.assertIn(FUNASR_MODEL, deployment)
        self.assertIn(FUNASR_VAD_MODEL, deployment)
        self.assertIn(FUNASR_PUNC_MODEL, deployment)

    def test_deterministic_contract_exposes_the_non_negotiable_rules(self):
        payload = contract()
        self.assertEqual(payload["pipeline_version"], PIPELINE_VERSION)
        self.assertTrue(
            payload["source_id_audit"]["every_source_assigned_to_one_chronological_paragraph"]
        )
        self.assertFalse(payload["source_id_audit"]["model_copies_individual_ids"])
        self.assertEqual(payload["source_id_audit"]["model_output_fields"], ["start_source_id"])
        self.assertEqual(
            payload["source_id_audit"]["assignment_method"],
            "deterministic_paragraph_start_ranges",
        )
        self.assertFalse(payload["source_id_audit"]["filler_text_is_forced_into_prose"])
        self.assertEqual(
            payload["semantic_editing"]["editing_stages"],
            [
                "whole_transcript_structure_and_visual_request_planning",
                "bounded_prewrite_asr_and_term_reconciliation",
                "golden_style_structured_writing_with_visual_evidence",
                "whole_transcript_detail_restoration",
                "golden_style_concise_copyedit",
            ],
        )
        self.assertEqual(payload["semantic_editing"]["document_passes"], 4)
        self.assertTrue(payload["semantic_editing"]["writer_reads_complete_transcript"])
        self.assertTrue(
            payload["semantic_editing"]["writer_reads_visual_evidence_before_drafting"]
        )
        self.assertTrue(
            payload["semantic_editing"]["writer_reads_bundled_golden_style_reference"]
        )
        self.assertTrue(
            payload["semantic_editing"][
                "each_planned_paragraph_receives_complete_local_source_excerpt"
            ]
        )
        self.assertTrue(
            payload["semantic_editing"][
                "detail_and_final_receive_aligned_source_draft_packets"
            ]
        )
        self.assertFalse(payload["semantic_editing"]["style_reference_facts_copyable"])
        self.assertTrue(
            payload["semantic_editing"]["reviewer_reads_complete_transcript_and_draft"]
        )
        self.assertTrue(
            payload["semantic_editing"]["llm_plans_sections_paragraphs_and_sentence_rewriting"]
        )
        self.assertTrue(
            payload["semantic_editing"]["final_with_obvious_residue_retried_once"]
        )
        self.assertFalse(payload["semantic_editing"]["program_decides_semantic_importance"])
        self.assertFalse(payload["developer_evaluation"]["reserves_task_number"])
        self.assertFalse(payload["developer_evaluation"]["writes_vault"])
        self.assertFalse(payload["developer_evaluation"]["downloads_media"])
        self.assertTrue(payload["semantic_editing"]["resume_reuses_persisted_raw_transcript"])
        self.assertTrue(payload["semantic_editing"]["semantic_checkpoint_source_signature"])
        self.assertEqual(payload["progress"]["failure_terminal"], "[6/6 · FAILED]")
        self.assertFalse(payload["tasks"]["bare_number_is_instruction"])
        self.assertTrue(payload["tasks"]["download_is_one_shot_attachment"])
        self.assertTrue(payload["preservation"]["detail_preservation_is_semantic_llm_review"])
        self.assertFalse(payload["preservation"]["mechanical_word_retention_gate"])
        self.assertTrue(payload["visuals"]["classification_failure_keeps_aligned_original"])
        self.assertTrue(payload["visuals"]["paid_vision_frames_temporally_distributed"])
        self.assertTrue(
            payload["visuals"]["planner_requested_ranges_prioritized_for_paid_vision"]
        )
        self.assertFalse(
            payload["visuals"]["medium_or_low_confidence_visual_text_publishable"]
        )
        self.assertFalse(payload["visuals"]["partial_or_unverified_visual_text_publishable"])
        self.assertEqual(
            payload["visuals"]["copyable_visual_text_minimum_ocr_confidence"], 50
        )
        self.assertFalse(payload["visuals"]["uncertain_visual_description_used_as_alt_text"])
        self.assertTrue(payload["visuals"]["nearby_duplicate_aligned_frames_removed"])
        self.assertEqual(payload["tasks"]["downloadable_statuses"], ["complete"])
        self.assertFalse(payload["secrets"]["secret_values_printed_or_searched"])
        self.assertTrue(payload["acquisition"]["source_adapter_registry"])
        self.assertFalse(payload["acquisition"]["future_sources_fork_manuscript_core"])
        self.assertEqual(
            payload["acquisition"]["installed_video_platforms"],
            ["bilibili", "youtube", "douyin"],
        )
        self.assertFalse(payload["acquisition"]["youtube_public_mode_requires_api_key"])
        self.assertEqual(
            payload["acquisition"]["installed_document_platforms"],
            ["generic_web", "zhihu", "xiaohongshu"],
        )
        self.assertTrue(
            payload["acquisition"]["youtube_automatic_caption_prefers_original_language"]
        )
        self.assertEqual(
            payload["tasks"]["cross_platform_identity_fields"],
            ["platform", "source_kind", "source_id", "source_key"],
        )
        self.assertFalse(payload["secrets"]["chat_secret_delivery_allowed"])
        self.assertEqual(payload["secrets"]["dedicated_secret_file_permissions"], "0600")
        self.assertTrue(payload["configuration"]["deterministic_platform_menu"])
        self.assertFalse(payload["configuration"]["bare_number_configures_platform"])

    def test_direct_manuscript_uses_whole_document_writer_and_reviewer(self):
        segments = [
            Segment("s000001", 0.0, 2.0, "嗯，先打开设置。"),
            Segment("s000002", 2.0, 4.0, "选择语言，重启后生效。"),
            Segment("s000003", 4.0, 6.0, "最后检查输出。"),
        ]
        draft = {
            "sections": [
                {
                    "title": "设置与检查",
                    "paragraphs": [
                        {"start_source_id": "s000001", "text": "打开设置并选择语言。"}
                    ],
                }
            ]
        }
        final = {
            "sections": [
                {
                    "title": "完成语言设置",
                    "paragraphs": [
                        {
                            "start_source_id": "s000001",
                            "text": "打开设置并选择语言；重新启动后，语言设置才会生效。",
                        }
                    ],
                },
                {
                    "title": "验证输出",
                    "paragraphs": [
                        {"start_source_id": "s000003", "text": "最后检查输出结果。"}
                    ],
                },
            ]
        }
        plan = {
            "sections": [
                {
                    "title": "完成语言设置",
                    "objective": "完成设置并验证",
                    "paragraphs": [
                        {
                            "start_source_id": "s000001",
                            "focus": "语言设置",
                            "must_keep": ["重启后生效"],
                        },
                        {
                            "start_source_id": "s000003",
                            "focus": "检查输出",
                            "must_keep": ["验证结果"],
                        },
                    ],
                }
            ]
        }
        client = SequenceClient([plan, draft, final, final])
        paragraphs, coverage = create_direct_manuscript(
            segments, client, context="设置教程"
        )
        self.assertEqual(client.calls, 4)
        self.assertEqual(paragraphs[0].source_ids, ["s000001", "s000002"])
        self.assertEqual(paragraphs[1].source_ids, ["s000003"])
        self.assertIn("重新启动后", paragraphs[0].text)
        self.assertEqual(
            coverage["editing_architecture"],
            "whole_transcript_plan_visual_reconcile_write_restore_copyedit",
        )
        self.assertEqual(coverage["represented_source_count"], 3)

    def test_direct_document_requires_chapters_for_long_video(self):
        segments = [
            Segment("s000001", 0.0, 100.0, "第一部分。"),
            Segment("s000002", 100.0, 220.0, "第二部分。"),
        ]
        payload = {
            "sections": [
                {
                    "title": "全部内容",
                    "paragraphs": [
                        {"start_source_id": "s000001", "text": "把所有内容放在一节。"}
                    ],
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "长视频必须"):
            _paragraphs_from_document(payload, segments)

    def test_direct_prompts_assign_semantics_to_deepseek(self):
        self.assertIn("只规划文章，不写正文", DIRECT_OUTLINE_PROMPT)
        self.assertIn("must_keep", DIRECT_OUTLINE_PROMPT)
        self.assertIn("visual_requests", DIRECT_OUTLINE_PROMPT)
        self.assertIn("相邻的“概述/详解”", DIRECT_OUTLINE_PROMPT)
        self.assertIn("只有进入新的处理阶段", DIRECT_OUTLINE_PROMPT)

    def test_asr_reconciliation_release_uses_reviewed_text_prompts(self):
        expected = {
            "outline": "d9821d98606f92316162421a932f80d45d04e313663f5058a010bd94a2a749cc",
            "writer": "35a82480f03ad66ef15804c7ca9f75f7d6452b8e03d773ade19cd16bae28ee6d",
            "detail": "40db15ed73c920bce274baca61aff4ebc05dfab212be992d6e1e6efd965b9497",
            "review": "f9e22917db7f869582d86f6f2f69aedc724a0174804b95befbd5963a50f2d3f1",
        }
        actual = {
            "outline": hashlib.sha256(DIRECT_OUTLINE_PROMPT.encode()).hexdigest(),
            "writer": hashlib.sha256(DIRECT_WRITER_PROMPT.encode()).hexdigest(),
            "detail": hashlib.sha256(DIRECT_DETAIL_PROMPT.encode()).hexdigest(),
            "review": hashlib.sha256(DIRECT_REVIEW_PROMPT.encode()).hexdigest(),
        }
        self.assertEqual(actual, expected)
        self.assertIn("严格按照规划写成", DIRECT_WRITER_PROMPT)
        self.assertIn("完整带时间戳字幕", DIRECT_WRITER_PROMPT)
        self.assertIn("保留原字幕中的观点", DIRECT_WRITER_PROMPT)
        self.assertIn("golden_style_reference", DIRECT_WRITER_PROMPT)
        self.assertIn("详细不等于重复", DIRECT_WRITER_PROMPT)
        self.assertIn("source_excerpt", DIRECT_WRITER_PROMPT)
        self.assertIn("点赞关注请求", DIRECT_OUTLINE_PROMPT)
        self.assertIn("补充、整理后的完整文稿", DIRECT_DETAIL_PROMPT)
        self.assertIn("初稿是否为了简洁而漏掉", DIRECT_DETAIL_PROMPT)
        self.assertIn("golden_style_reference", DIRECT_REVIEW_PROMPT)
        self.assertIn("一句话能说清不用两句", DIRECT_REVIEW_PROMPT)
        self.assertIn("补回 current_draft 遗漏", DIRECT_REVIEW_PROMPT)
        self.assertIn("相邻片段", DIRECT_OUTLINE_PROMPT)
        self.assertIn("ASR 不确定只影响精确措辞", DIRECT_OUTLINE_PROMPT)
        self.assertIn("不得连同周围明确的背景", DIRECT_WRITER_PROMPT)
        self.assertIn("不能因为局部术语不确定", DIRECT_DETAIL_PROMPT)
        self.assertIn("只删去不可靠的精确措辞", DIRECT_REVIEW_PROMPT)
        self.assertIn("article_plan 只负责结构，不是事实来源", DIRECT_WRITER_PROMPT)
        self.assertIn("否定词、限制词和比较方向不得被反转", DIRECT_DETAIL_PROMPT)
        self.assertIn("可以少改或不改", DIRECT_REVIEW_PROMPT)

    def test_writer_receives_visual_evidence_and_bundled_golden_style_before_drafting(self):
        segments = [
            Segment("s000001", 0.0, 3.0, "打开设置并选择语言。"),
            Segment("s000002", 3.0, 6.0, "重新打开后生效。"),
        ]
        plan = {
            "sections": [{
                "title": "设置",
                "objective": "完成设置",
                "paragraphs": [{
                    "start_source_id": "s000001",
                    "focus": "语言设置",
                    "must_keep": ["重新打开后生效"],
                    "visual_requests": [{
                        "time_start": 0.0,
                        "time_end": 4.0,
                        "purpose": "确认设置入口",
                        "expected_kind": "ui",
                    }],
                }],
            }]
        }
        document = {
            "sections": [{
                "title": "设置",
                "paragraphs": [{
                    "start_source_id": "s000001",
                    "text": "打开设置并选择语言，重新打开后生效。",
                }],
            }]
        }

        class CapturingClient(SequenceClient):
            def __init__(self, responses):
                super().__init__(responses)
                self.messages = []

            def chat(self, messages, **kwargs):
                self.messages.append(messages)
                return super().chat(messages, **kwargs)

        client = CapturingClient([
            plan,
            {"corrections": []},
            document,
            document,
            document,
        ])
        prepared = create_direct_plan(segments, client, context="设置教程")
        frames = [Frame(
            2.0,
            "/tmp/frame.png",
            source_ids=["s000001"],
            ocr_text="设置 常规 语言",
            ocr_confidence=88,
            vision_description="设置界面显示常规与语言选项",
        )]
        _paragraphs, coverage = complete_direct_manuscript(
            segments, client, prepared, context="设置教程", frames=frames
        )
        writer_payload = json.loads(client.messages[2][1]["content"])
        detail_payload = json.loads(client.messages[3][1]["content"])
        final_payload = json.loads(client.messages[4][1]["content"])
        self.assertEqual(writer_payload["visual_evidence"][0]["timestamp"], 2.0)
        self.assertIn("设置界面", writer_payload["visual_evidence"][0]["vision_description"])
        self.assertIn(
            "重新打开后生效",
            writer_payload["article_plan"]["sections"][0]["paragraphs"][0][
                "source_excerpt"
            ],
        )
        self.assertIn("one clear purpose per paragraph", writer_payload["golden_style_reference"])
        self.assertEqual(
            detail_payload["paragraph_evidence_packets"][0]["current_text"],
            "打开设置并选择语言，重新打开后生效。",
        )
        self.assertIn("source_excerpt", final_payload["paragraph_evidence_packets"][0])
        self.assertIn("golden_style_reference", final_payload)
        self.assertTrue(coverage["visual_evidence_before_writing"])
        self.assertTrue(coverage["golden_style_reference"])
        self.assertEqual(coverage["llm_targeted_asr_reconciliation_passes"], 1)

    def test_targeted_reconciliation_overrides_bad_plan_term_and_restores_split_sentence(self):
        segments = [
            Segment("s000001", 0.0, 2.0, "最终切换到他们叫 EGICS 的方式。"),
            Segment("s000002", 2.0, 4.0, "Rap so process payment。"),
            Segment("s000003", 4.0, 6.0, "就精确命中不存在于语义漂移的问题。"),
        ]
        plan = {
            "sections": [{
                "title": "精确搜索",
                "objective": "解释精确搜索",
                "paragraphs": [{
                    "start_source_id": "s000001",
                    "focus": "说明 EGICS 和精确匹配",
                    "must_keep": ["最终改用 EGICS", "武装太设计"],
                    "asr_suspects": [{
                        "source_text": "EGICS",
                        "conservative_repair": "EGICS",
                        "confidence": "medium",
                    }, {
                        "source_text": "Rap so process payment 就精确命中不存在于语义漂移的问题",
                        "conservative_repair": None,
                        "confidence": "low",
                    }, {
                        "source_text": "武装太设计",
                        "conservative_repair": "无状态设计",
                        "confidence": "high",
                    }],
                    "visual_requests": [],
                }],
            }],
        }
        reconciliation = {
            "items": [
                {
                    "item_id": "r001",
                    "action": "correct",
                    "replacement": "agentic search",
                    "required_anchors": ["agentic search"],
                    "confidence": "high",
                    "basis": "visible_text",
                },
                {
                    "item_id": "r002",
                    "action": "correct",
                    "replacement": "grep 搜 processPayment 就是精确命中，不存在语义漂移问题",
                    "required_anchors": ["processPayment", "精确命中", "不存在语义漂移"],
                    "confidence": "medium",
                    "basis": "adjacent_context",
                },
            ]
        }
        document = {
            "sections": [{
                "title": "精确搜索",
                "paragraphs": [{
                    "start_source_id": "s000001",
                    "text": "最终改用 agentic search。grep 搜 processPayment 就是精确命中，不存在语义漂移问题。",
                }],
            }],
        }
        client = SequenceClient([reconciliation, document, document, document])
        frames = [Frame(
            1.0,
            "/tmp/agentic.png",
            source_ids=["s000001"],
            ocr_text="agentic search",
            ocr_confidence=95,
            vision_description="Boris 原文清晰显示 agentic search。",
        )]
        paragraphs, coverage = complete_direct_manuscript(
            segments,
            client,
            plan,
            context="Claude Code 搜索方式",
            frames=frames,
        )
        self.assertEqual(client.calls, 4)
        self.assertIn("agentic search", paragraphs[0].text)
        self.assertIn("不存在语义漂移问题", paragraphs[0].text)
        self.assertNotIn("EGICS", paragraphs[0].text)
        self.assertEqual(
            coverage["asr_reconciliation"]["corrections"][0]["basis"],
            "visible_text",
        )
        self.assertNotIn(
            "UNIX 设计",
            [
                item["replacement"]
                for item in coverage["asr_reconciliation"]["corrections"]
            ],
        )

    def test_verified_visual_clues_accept_qwen_bold_exact_text(self):
        clues = _verified_visual_clues([{
            "timestamp": 96.4,
            "source_ids": ["s000054"],
            "ocr": "",
            "ocr_confidence": 0,
            "vision_description": (
                "纯文字。中央黄色横幅内文字：**不存在语义漂移的问题**"
            ),
        }])
        self.assertEqual(
            clues[0]["exact_visible_text"],
            ["不存在语义漂移的问题"],
        )

    def test_visible_conflict_adds_unflagged_uppercase_phonetic_term_to_same_reconciliation(self):
        segments = [
            Segment("s000001", 0, 2, "最终切换到了他们叫 EGICS 的方式。"),
        ]
        client = SequenceClient([{
            "items": [{
                "item_id": "r001",
                "action": "correct",
                "replacement": "agentic search",
                "required_anchors": ["agentic search"],
                "confidence": "high",
                "basis": "visible_text",
            }],
        }])
        result = _call_asr_reconciliation(
            client,
            segments=segments,
            plan={"sections": []},
            visual_evidence=[{
                "timestamp": 1,
                "source_ids": ["s000001"],
                "ocr": "agentic search",
                "ocr_confidence": 95,
                "vision_description": "",
            }],
            suspect_contexts=[],
        )
        self.assertEqual(client.calls, 1)
        self.assertEqual(
            result["corrections"][0]["replacement"], "agentic search"
        )

    def test_reconciliation_accepts_equivalent_yolo_anchor_punctuation(self):
        source_text = "C1C2C3代表类别，比如可能是person、car和traffic light。"
        client = SequenceClient([{
            "items": [{
                "item_id": "r001",
                "action": "correct",
                "replacement": "C1、C2、C3代表类别，bh＝0.7，bw＝0.25",
                "required_anchors": ["C1,C2,C3", "bh=0.7,bw=0.25"],
                "confidence": "medium",
                "basis": "adjacent_context",
            }],
        }])
        result = _call_asr_reconciliation(
            client,
            segments=[Segment("s000001", 0, 2, source_text)],
            plan={"sections": []},
            visual_evidence=[],
            suspect_contexts=[{
                "source_ids": ["s000001"],
                "context": source_text,
                "asr_suspects": [{
                    "source_text": source_text,
                    "conservative_repair": None,
                    "confidence": "medium",
                }],
            }],
        )
        self.assertEqual(client.calls, 1)
        self.assertEqual(
            result["corrections"][0]["required_anchors"],
            ["C1、C2、C3", "bh＝0.7，bw＝0.25"],
        )
        _require_reconciliation_applied(
            {"sections": [{"paragraphs": [{
                "text": "类别采用 C1,C2,C3；其中 bh=0.7，bw=0.25。"
            }]}]},
            result,
        )

    def test_reconciliation_allows_neutral_particle_but_not_polarity_change(self):
        reconciliation = {"corrections": [{
            "required_anchors": ["参数为零"],
        }]}
        _require_reconciliation_applied(
            {"sections": [{"paragraphs": [{"text": "第2、3个格子的参数也为零。"}]}]},
            reconciliation,
        )
        with self.assertRaisesRegex(ValueError, "参数为零"):
            _require_reconciliation_applied(
                {"sections": [{"paragraphs": [{"text": "第2、3个格子的参数不为零。"}]}]},
                reconciliation,
            )

    def test_reconciliation_drops_one_unsupported_anchor_instead_of_failing_document(self):
        source_text = "这个类别可能是car。"
        client = SequenceClient([{
            "items": [{
                "item_id": "r001",
                "action": "correct",
                "replacement": "该类别是 car",
                "required_anchors": ["traffic light"],
                "confidence": "medium",
                "basis": "adjacent_context",
            }],
        }])
        result = _call_asr_reconciliation(
            client,
            segments=[Segment("s000001", 0, 2, source_text)],
            plan={"sections": []},
            visual_evidence=[],
            suspect_contexts=[{
                "source_ids": ["s000001"],
                "context": source_text,
                "asr_suspects": [{
                    "source_text": source_text,
                    "conservative_repair": None,
                    "confidence": "medium",
                }],
            }],
        )
        self.assertEqual(client.calls, 1)
        self.assertEqual(result, {"corrections": []})

    def test_asr_suspect_deterministically_adds_visual_request(self):
        segments = [
            Segment("s000001", 10.0, 12.0, "先解释背景。"),
            Segment("s000002", 12.0, 14.0, "Rap so process payment。"),
            Segment("s000003", 14.0, 16.0, "再进入下一部分。"),
        ]
        plan = {
            "sections": [{
                "paragraphs": [{
                    "start_source_id": "s000001",
                    "asr_suspects": [{
                        "source_text": "Rap so process payment",
                        "conservative_repair": None,
                        "confidence": "low",
                    }],
                    "visual_requests": [],
                }],
            }],
        }
        requests = visual_requests_from_plan(plan, segments)
        self.assertEqual(len(requests), 1)
        self.assertIn("ASR 可疑", requests[0]["purpose"])
        self.assertAlmostEqual(requests[0]["time_start"], 10.5)
        self.assertAlmostEqual(requests[0]["time_end"], 15.5)

    def test_planner_visual_requests_are_flattened_and_prioritized_within_budget(self):
        plan = {
            "sections": [{
                "paragraphs": [{
                    "visual_requests": [{
                        "time_start": 40,
                        "time_end": 50,
                        "purpose": "确认流程图",
                        "expected_kind": "diagram",
                    }]
                }]
            }]
        }
        requests = visual_requests_from_plan(plan)
        self.assertEqual(requests[0]["purpose"], "确认流程图")
        outside = Frame(10.0, "a.png")
        requested = Frame(45.0, "b.png")
        selected = [(outside, 99.0), (requested, 1.0)]
        chosen = vision_priority_ids_for_requests(selected, 1, 60.0, requests)
        self.assertEqual(chosen, {id(requested)})

    def test_asr_visual_ranges_get_both_endpoints_without_increasing_budget(self):
        before = Frame(10.0, "before.png")
        first_clause = Frame(94.8, "identifier.png")
        middle = Frame(95.5, "middle.png")
        polarity = Frame(96.5, "polarity.png")
        ordinary = Frame(150.0, "ordinary.png")
        selected = [
            (before, 9.0),
            (first_clause, 3.0),
            (middle, 8.0),
            (polarity, 2.0),
            (ordinary, 10.0),
        ]
        requests = [{
            "time_start": 94.0,
            "time_end": 97.0,
            "purpose": "核验 ASR 可疑术语或断句",
            "expected_kind": "text",
        }, {
            "time_start": 145.0,
            "time_end": 155.0,
            "purpose": "查看普通架构图",
            "expected_kind": "diagram",
        }]
        chosen = vision_priority_ids_for_requests(
            selected, 3, 200.0, requests
        )
        self.assertEqual(
            chosen,
            {id(first_clause), id(polarity), id(ordinary)},
        )

    def test_dedicated_visual_planner_can_request_dense_ppt_range_without_fixed_interval(self):
        segments = [
            Segment("s1", 0, 8, "这是课程大纲。"),
            Segment("s2", 8, 24, "下面演示完整流程和最终成品图。"),
        ]
        client = FakeClient({
            "requests": [
                {
                    "time_start": 0,
                    "time_end": 24,
                    "purpose": "读取课程大纲、操作流程和成品图中的独有细节",
                    "expected_kind": "ui",
                }
            ]
        })
        requests = create_visual_request_plan(
            segments,
            client,
            {"sections": [{"title": "课程", "paragraphs": []}]},
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["time_end"], 24)
        self.assertIn("成品图", requests[0]["purpose"])

    def test_visual_request_merge_removes_same_kind_near_duplicate_windows(self):
        merged = merge_visual_requests(
            [{"time_start": 10, "time_end": 20, "purpose": "A", "expected_kind": "text"}],
            [
                {"time_start": 10.5, "time_end": 20.5, "purpose": "B", "expected_kind": "text"},
                {"time_start": 10, "time_end": 20, "purpose": "C", "expected_kind": "diagram"},
            ],
        )
        self.assertEqual(len(merged), 2)

    def test_final_copyedit_rejects_promotional_outro(self):
        segments = [Segment("s000001", 0.0, 2.0, "如果有用请点赞三连。")]
        payload = {
            "sections": [{
                "title": "片尾",
                "paragraphs": [{
                    "start_source_id": "s000001",
                    "text": "如果有用请点赞三连。",
                }],
            }]
        }
        with self.assertRaisesRegex(ValueError, "点赞"):
            _require_final_copyedit(payload, payload, segments)

    def test_compose_markdown_renders_real_tutorial_subheading(self):
        with tempfile.TemporaryDirectory() as temp:
            note = Path(temp) / "note.md"
            compose_markdown(
                note,
                {"title": "教程", "url": "https://example.com", "bvid": "BV1", "owner": "UP"},
                [Paragraph(["s1"], "进入设置并选择语言。", heading="准备", subheading="切换中文界面")],
                [],
            )
            text = note.read_text(encoding="utf-8")
            self.assertIn("## 准备", text)
            self.assertIn("### 切换中文界面", text)

    def test_identical_final_with_obvious_editorial_residue_is_rejected(self):
        segments = [Segment("s000001", 0.0, 2.0, "同学们先打开设置。")]
        unchanged = {
            "sections": [{
                "title": "设置",
                "paragraphs": [{
                    "start_source_id": "s000001",
                    "text": "同学们先打开设置。",
                }],
            }]
        }
        with self.assertRaisesRegex(ValueError, "实际修订"):
            _require_final_copyedit(unchanged, unchanged, segments)

    def test_identical_clean_final_is_allowed(self):
        segments = [Segment("s000001", 0.0, 2.0, "打开设置。")]
        unchanged = {
            "sections": [{
                "title": "设置",
                "paragraphs": [{
                    "start_source_id": "s000001",
                    "text": "打开设置。",
                }],
            }]
        }
        _require_final_copyedit(unchanged, unchanged, segments)

    def test_changed_final_with_confirmed_asr_residue_is_repaired(self):
        segments = [Segment("s000001", 0.0, 2.0, "作者标题年份来源卷积页码。")]
        current = {
            "sections": [{
                "title": "字段",
                "paragraphs": [{
                    "start_source_id": "s000001",
                    "text": "文件包含作者、标题和年份。",
                }],
            }]
        }
        candidate = {
            "sections": [{
                "title": "字段",
                "paragraphs": [{
                    "start_source_id": "s000001",
                    "text": "文件包含作者、标题、年份、来源和卷积页码。",
                }],
            }]
        }
        _require_final_copyedit(candidate, current, segments)
        self.assertEqual(
            candidate["sections"][0]["paragraphs"][0]["text"],
            "文件包含作者、标题、年份、来源和卷、期、页码。",
        )

    def test_regression_confirmed_platform_asr_fragment_is_repaired_conservatively(self):
        payload = {
            "sections": [{
                "title": "平台",
                "paragraphs": [{
                    "start_source_id": "s000001",
                    "text": "作者引入自带超期刊文献的智能化学术写作平台。",
                }],
            }]
        }
        count = _apply_narrow_final_repairs(payload)
        self.assertEqual(count, 1)
        self.assertIn(
            "具备文献处理能力",
            payload["sections"][0]["paragraphs"][0]["text"],
        )

    def test_real_task_38_final_residues_are_normalized_without_rewriting_paragraph(self):
        payload = {
            "sections": [{
                "title": "总结",
                "paragraphs": [{
                    "start_source_id": "s000001",
                    "text": (
                        "作者引入具备文献处理能力能力的 SY paper，"
                        "三十分钟完成一篇参考论文。"
                    ),
                }],
            }]
        }
        count = _apply_narrow_final_repairs(payload)
        self.assertEqual(count, 3)
        self.assertEqual(
            payload["sections"][0]["paragraphs"][0]["text"],
            "作者引入具备文献处理能力的 SY Paper，三十分钟完成一篇论文初稿。",
        )

    def test_final_audit_adjudication_can_dismiss_style_only_objection(self):
        sections = [
            OutlineSection(
                id="sec001",
                title="执行与等待",
                objective="保留操作过程",
                format_hint="prose",
                unit_ids=["u000001"],
            )
        ]
        units = [
            InformationUnit(
                id="u000001",
                source_ids=["s000001"],
                start=0.0,
                end=2.0,
                action="keep",
                kind="example",
                topic="等待期间",
                text="任务运行时可以去食堂吃饭。",
                details=[],
                drop_reason=None,
            )
        ]
        paragraphs = {
            "sec001": [
                Paragraph(
                    source_ids=["s000001"],
                    text="任务运行期间，用户可以去食堂吃饭。",
                    start=0.0,
                    end=2.0,
                    heading="执行与等待",
                )
            ]
        }
        client = FakeClient(
            {
                "decisions": [
                    {
                        "issue_index": 0,
                        "valid": False,
                        "reason": "这是来源明确表达的场景例子，不是编辑腔。",
                    }
                ]
            }
        )
        result = _adjudicate_final_audit(
            sections,
            units,
            paragraphs,
            [
                {
                    "section_id": "sec001",
                    "kind": "presenter_voice",
                    "instruction": "删除去食堂吃饭的内容",
                }
            ],
            client,
            "论文教程",
        )
        self.assertEqual(result["valid_issue_indices"], [])
        self.assertFalse(result["decisions"][0]["valid"])

    def test_asr_residue_can_replace_every_repeated_bad_fragment(self):
        sections = [
            OutlineSection(
                id="sec001", title="步骤", unit_ids=["u000001"],
                objective="说明操作", format_hint="prose",
            )
        ]
        units = [
            InformationUnit(
                id="u000001", source_ids=["s000001"], start=0.0, end=2.0,
                action="keep", kind="step", topic="参数",
                text="切换指文后再次检查切换指文。", details=["检查相关参数"],
            )
        ]
        paragraphs = {
            "sec001": [
                Paragraph(
                    source_ids=["s000001"],
                    text="切换指文后，再次检查切换指文。",
                    start=0.0, end=2.0, heading="步骤",
                )
            ]
        }
        client = FakeClient(
            {
                "checked_section_ids": ["sec001"],
                "corrections": [
                    {
                        "section_id": "sec001", "paragraph_index": 0,
                        "original": "切换指文", "replacement": "相关参数",
                        "replace_all": True, "reason": "同一乱码重复出现",
                    }
                ],
                "unresolved": [],
            }
        )
        result = _proofread_asr_residue(sections, units, paragraphs, client, "教程")
        self.assertEqual(paragraphs["sec001"][0].text, "相关参数后，再次检查相关参数。")
        self.assertEqual(result["corrections"][0]["replaced_count"], 2)

    def test_asr_residue_can_replace_one_numbered_occurrence(self):
        sections = [
            OutlineSection(
                id="sec001", title="步骤", unit_ids=["u000001"],
                objective="说明操作", format_hint="prose",
            )
        ]
        units = [
            InformationUnit(
                id="u000001", source_ids=["s000001"], start=0.0, end=2.0,
                action="keep", kind="step", topic="参数",
                text="检查选项，保留另一个选项。", details=["检查选项"],
            )
        ]
        paragraphs = {
            "sec001": [
                Paragraph(
                    source_ids=["s000001"],
                    text="选项用于输入，另一个选项用于输出。",
                    start=0.0, end=2.0, heading="步骤",
                )
            ]
        }
        client = FakeClient(
            {
                "checked_section_ids": ["sec001"],
                "corrections": [
                    {
                        "section_id": "sec001", "paragraph_index": 0,
                        "original": "选项", "replacement": "输入项",
                        "replace_all": False, "occurrence": 1,
                        "reason": "只修正第一次出现的位置",
                    }
                ],
                "unresolved": [],
            }
        )
        result = _proofread_asr_residue(sections, units, paragraphs, client, "教程")
        self.assertEqual(paragraphs["sec001"][0].text, "输入项用于输入，另一个选项用于输出。")
        self.assertEqual(result["corrections"][0]["replaced_count"], 1)

    def test_create_manuscript_uses_final_adjudication_after_repair_limit(self):
        issue = {
            "section_id": "sec001",
            "kind": "presenter_voice",
            "instruction": "删除来源中的生活化等待场景",
        }
        repair = audit_repair({"sec001": "repair"}, [issue])
        client = SequenceClient(
            [
                {
                    "units": [
                        {
                            "start_source_id": "s000001",
                            "action": "keep",
                            "kind": "example",
                            "topic": "等待期间",
                            "text": "任务运行时可以去食堂吃饭。",
                            "details": [],
                            "drop_reason": None,
                        }
                    ]
                },
                {"corrections": []},
                {
                    "sections": [
                        {
                            "start_unit_id": "u000001",
                            "title": "执行与等待",
                            "objective": "保留操作过程和等待场景",
                            "format_hint": "prose",
                        }
                    ]
                },
                {
                    "paragraphs": [
                        {
                            "start_unit_id": "u000001",
                            "text": "任务运行期间，用户可以去食堂吃饭。",
                        }
                    ]
                },
                repair,
                {
                    "paragraphs": [
                        {
                            "start_unit_id": "u000001",
                            "text": "任务运行期间，用户可以去食堂吃饭。",
                        }
                    ]
                },
                repair,
                {
                    "paragraphs": [
                        {
                            "start_unit_id": "u000001",
                            "text": "任务运行期间，用户可以去食堂吃饭。",
                        }
                    ]
                },
                repair,
                {
                    "paragraphs": [
                        {
                            "start_unit_id": "u000001",
                            "text": "任务运行期间，用户可以去食堂吃饭。",
                        }
                    ]
                },
                repair,
                {
                    "decisions": [
                        {
                            "issue_index": 0,
                            "valid": False,
                            "reason": "等待场景来自来源，不应因生活化而删除。",
                        }
                    ]
                },
            ]
        )
        paragraphs, coverage = create_manuscript(
            [Segment("s000001", 0.0, 2.0, "任务运行时可以去食堂吃饭。")],
            client,
            context="论文教程",
        )
        self.assertEqual(paragraphs[0].text, "任务运行期间，用户可以去食堂吃饭。")
        final_audit = coverage["audit_history"][-1]
        self.assertEqual(final_audit["verdict"], "pass")
        self.assertTrue(final_audit["accepted_after_adjudication"])
        self.assertEqual(final_audit["final_issue_adjudication"]["valid_issue_indices"], [])

    def test_overbroad_information_unit_is_returned_to_model_for_semantic_split(self):
        segments = [
            Segment(f"s{index:06d}", (index - 1) * 6.0, index * 6.0, f"第{index}项内容")
            for index in range(1, 7)
        ]
        client = SequenceClient(
            [
                {
                    "units": [
                        {
                            "start_source_id": "s000001",
                            "action": "keep",
                            "kind": "other",
                            "topic": "全部内容",
                            "text": "六项内容。",
                            "details": ["六项内容"],
                            "drop_reason": None,
                        }
                    ]
                },
                {
                    "units": [
                        {
                            "start_source_id": "s000001",
                            "action": "keep",
                            "kind": "step",
                            "topic": "前三项",
                            "text": "前三项内容。",
                            "details": ["第一项", "第二项", "第三项"],
                            "drop_reason": None,
                        },
                        {
                            "start_source_id": "s000004",
                            "action": "keep",
                            "kind": "step",
                            "topic": "后三项",
                            "text": "后三项内容。",
                            "details": ["第四项", "第五项", "第六项"],
                            "drop_reason": None,
                        },
                    ]
                },
            ]
        )
        units = _extract_units(segments, client, "操作教程")
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0].source_ids, ["s000001", "s000002", "s000003"])
        self.assertEqual(units[1].source_ids, ["s000004", "s000005", "s000006"])

    def test_publication_copyedit_runs_before_final_audit(self):
        client = SequenceClient(
            [
                {
                    "units": [
                        {
                            "start_source_id": "s000001",
                            "action": "keep",
                            "kind": "conclusion",
                            "topic": "结果",
                            "text": "作者认为流程简单。",
                            "details": ["作者的主观评价"],
                            "drop_reason": None,
                        }
                    ]
                },
                {"corrections": []},
                {
                    "sections": [
                        {
                            "start_unit_id": "u000001",
                            "title": "流程结果",
                            "objective": "说明作者结论",
                            "format_hint": "prose",
                        }
                    ]
                },
                {
                    "paragraphs": [
                        {"start_unit_id": "u000001", "text": "总结：流程非常简单。"}
                    ]
                },
                {
                    "paragraphs": [
                        {
                            "start_unit_id": "u000001",
                            "text": "作者认为，这一流程较为简、单。",
                        }
                    ]
                },
                {
                    "checked_section_ids": ["sec001"],
                    "corrections": [
                        {
                            "section_id": "sec001",
                            "paragraph_index": 0,
                            "original": "较为简、单",
                            "replacement": "较为简单",
                            "reason": "修复 ASR 错误断词",
                        }
                    ],
                    "unresolved": [],
                },
                {
                    **audit_pass("sec001"),
                },
                {
                    "checked_section_ids": ["sec001"],
                    "corrections": [],
                    "unresolved": [],
                },
            ]
        )
        paragraphs, coverage = create_manuscript(
            [Segment("s000001", 0.0, 2.0, "作者认为这个流程非常简单。")],
            client,
            context="操作教程",
            publication_copyedit=True,
        )
        self.assertEqual(paragraphs[0].text, "作者认为，这一流程较为简单。")
        self.assertEqual(coverage["audit_history"][0]["verdict"], "pass")

    def test_asr_residue_is_rechecked_after_audit_driven_rewrite(self):
        issue = {
            "section_id": "sec001",
            "unit_ids": ["u000001"],
            "kind": "structure",
            "instruction": "保留动作并改成自然段",
        }
        client = SequenceClient(
            [
                {
                    "units": [
                        {
                            "start_source_id": "s000001",
                            "action": "keep",
                            "kind": "step",
                            "topic": "复制内容",
                            "text": "复制串软络并粘贴。",
                            "details": ["复制并粘贴"],
                            "drop_reason": None,
                        }
                    ]
                },
                {"corrections": []},
                {
                    "sections": [
                        {
                            "start_unit_id": "u000001",
                            "title": "复制内容",
                            "objective": "说明复制步骤",
                            "format_hint": "prose",
                        }
                    ]
                },
                {"paragraphs": [{"start_unit_id": "u000001", "text": "复制相应内容并粘贴。"}]},
                {"paragraphs": [{"start_unit_id": "u000001", "text": "复制相应内容并粘贴。"}]},
                {"checked_section_ids": ["sec001"], "corrections": [], "unresolved": []},
                audit_repair({"sec001": "repair"}, [issue]),
                {"paragraphs": [{"start_unit_id": "u000001", "text": "复制串软络并粘贴。"}]},
                {
                    "checked_section_ids": ["sec001"],
                    "corrections": [
                        {
                            "section_id": "sec001",
                            "paragraph_index": 0,
                            "original": "复制串软络",
                            "replacement": "复制相应内容",
                            "reason": "保留动作并移除无法恢复的乱码宾语",
                        }
                    ],
                    "unresolved": [],
                },
                audit_pass("sec001"),
                {"checked_section_ids": ["sec001"], "corrections": [], "unresolved": []},
            ]
        )
        paragraphs, coverage = create_manuscript(
            [Segment("s000001", 0.0, 2.0, "复制串软络并粘贴。")],
            client,
            context="操作教程",
            publication_copyedit=True,
        )
        self.assertEqual(paragraphs[0].text, "复制相应内容并粘贴。")
        self.assertEqual(len(coverage["audit_history"]), 2)
        self.assertEqual(
            coverage["repair_asr_residue_history"][0]["corrections"][0]["original"],
            "复制串软络",
        )

    def test_skill_contract_documents_runtime_repair_and_record_delete_rules(self):
        skill = Path(__file__).resolve().parents[2] / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        self.assertIn("four whole-document DeepSeek editorial passes", text)
        self.assertIn("bounded DeepSeek reconciliation", text)
        self.assertIn("complete timed transcript", text)
        self.assertIn("complete timed transcript plus the complete first draft", text)
        self.assertIn("DeepSeek, not Python, decides semantic importance", text)
        self.assertIn("record-only soft deletions", text)
        self.assertIn("Every source subtitle is assigned exactly once", text)
        self.assertIn("start_source_id", text)
        self.assertIn("entry path, exact button/option names", text)
        self.assertIn("[6/6 · FAILED]", text)

    def test_skill_requires_detached_submit_and_single_bulk_delete_operation(self):
        skill = Path(__file__).resolve().parents[2] / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        self.assertIn("scripts/vtm submit", text)
        self.assertIn("chat and task-management commands remain available", text)
        self.assertIn("scripts/vtm delete-many --all-history", text)
        self.assertIn("scripts/vtm delete-many --confirm-token", text)
        self.assertNotIn("terminal(background=true, notify_on_complete=false)", text)

    def test_launcher_skips_funasr_import_for_management_commands(self):
        launcher = Path(__file__).resolve().parents[1] / "vtm"
        text = launcher.read_text(encoding="utf-8")
        self.assertIn("run|prepare-asr", text)
        self.assertIn('if [ "$needs_asr_runtime" = false ]', text)

    def test_progress_is_labelled_for_concurrent_jobs(self):
        label = progress_label(2, "20260716-2")
        self.assertEqual(label, "今日任务 2 · 20260716-2")
        self.assertEqual(
            format_progress(label, "正在获取或识别字幕。"),
            "[今日任务 2 · 20260716-2] [2/6] 正在获取或识别字幕。",
        )

    def test_information_units_remove_filler_without_losing_meaningful_details(self):
        segments = [
            Segment("s000001", 0, 1, "嗯啊，然后我们开始。"),
            Segment("s000002", 1, 3, "打开 Codex，选择允许访问项目目录。"),
            Segment("s000003", 3, 5, "输入研究主题，等待 10 分钟生成文献列表。"),
        ]
        client = SequenceClient([
            {"units": [
                {"start_source_id": "s000001", "action": "drop", "kind": "other", "topic": "", "text": "", "details": [], "drop_reason": "filler"},
                {"start_source_id": "s000002", "action": "keep", "kind": "step", "topic": "配置 Codex", "text": "打开 Codex 并允许访问项目目录，输入研究主题后等待 10 分钟生成文献列表。", "details": ["允许访问项目目录", "等待 10 分钟"], "drop_reason": None},
            ]},
            {"corrections": []},
            {"sections": [{"start_unit_id": "u000002", "title": "配置 Codex 并生成文献列表", "objective": "保留完整操作", "format_hint": "steps"}]},
            {"paragraphs": [{"start_unit_id": "u000002", "text": "在 Codex 中允许访问项目目录，输入研究主题后等待 10 分钟，系统会生成文献列表。"}]},
            audit_pass("sec001"),
        ])
        paragraphs, coverage = create_manuscript(segments, client, context="研究论文教程")
        self.assertEqual(paragraphs[0].source_ids, ["s000002", "s000003"])
        self.assertNotIn("嗯啊", paragraphs[0].text)
        self.assertIn("10 分钟", paragraphs[0].text)
        self.assertEqual(coverage["dropped_unit_count"], 1)
        self.assertEqual(coverage["editing_architecture"], "information_units_outline_sections_audit")

    def test_information_unit_anchor_gate_ignores_open_and_unknown_sr(self):
        anchors = manuscript_anchors("open the file，SR 只是识别噪声，但 Codex 和 10 分钟必须保留。")
        self.assertNotIn("open", anchors)
        self.assertNotIn("SR", anchors)
        self.assertIn("Codex", anchors)
        self.assertIn("10 分钟", anchors)

    def test_chinese_and_arabic_quantities_are_equivalent_without_relaxing_gate(self):
        evidence = "需要二十条文献，其中十五条中文、五条英文，消耗不到百分之十。"
        self.assertTrue(manuscript_anchor_supported(evidence, "20条"))
        self.assertTrue(manuscript_anchor_supported(evidence, "15条"))
        self.assertTrue(manuscript_anchor_supported(evidence, "5条"))
        self.assertTrue(manuscript_anchor_supported(evidence, "10%"))
        self.assertFalse(manuscript_anchor_supported(evidence, "30条"))

    def test_video_title_can_support_corrected_technical_name(self):
        segments = [Segment("s000001", 0, 2, "使用Goodex生成论文大纲。")]
        client = SequenceClient([
            {"units": [{"start_source_id": "s000001", "action": "keep", "kind": "step", "topic": "生成大纲", "text": "使用Codex生成论文大纲。", "details": [], "drop_reason": None}]},
            {"corrections": []},
            {"sections": [{"start_unit_id": "u000001", "title": "使用Codex生成论文大纲", "objective": "说明操作", "format_hint": "prose"}]},
            {"paragraphs": [{"start_unit_id": "u000001", "text": "使用 Codex 生成论文大纲。"}]},
            audit_pass("sec001"),
        ])
        paragraphs, _coverage = create_manuscript(segments, client, context="标题：Codex论文教程")
        self.assertIn("Codex", paragraphs[0].text)

    def test_global_audit_repairs_only_the_failed_section(self):
        segments = [
            Segment("s000001", 0, 2, "先设置项目权限。"),
            Segment("s000002", 2, 4, "再生成论文大纲。"),
        ]
        client = SequenceClient([
            {"units": [
                {"start_source_id": "s000001", "action": "keep", "kind": "step", "topic": "权限", "text": "设置项目权限。", "details": [], "drop_reason": None},
                {"start_source_id": "s000002", "action": "keep", "kind": "step", "topic": "大纲", "text": "生成论文大纲。", "details": [], "drop_reason": None},
            ]},
            {"corrections": []},
            {"sections": [
                {"start_unit_id": "u000001", "title": "设置项目权限", "objective": "说明权限", "format_hint": "prose"},
                {"start_unit_id": "u000002", "title": "生成论文大纲", "objective": "说明大纲", "format_hint": "prose"},
            ]},
            {"paragraphs": [{"start_unit_id": "u000001", "text": "先完成项目权限设置。"}]},
            {"paragraphs": [{"start_unit_id": "u000002", "text": "随后处理论文。"}]},
            audit_repair(
                {"sec001": "pass", "sec002": "repair"},
                [{"section_id": "sec002", "unit_ids": ["u000002"], "kind": "missing", "instruction": "补回生成论文大纲"}],
            ),
            {"paragraphs": [{"start_unit_id": "u000002", "text": "权限设置完成后，生成论文大纲。"}]},
            audit_pass("sec001", "sec002"),
        ])
        paragraphs, coverage = create_manuscript(segments, client, context="论文教程")
        self.assertEqual(client.calls, 8)
        self.assertEqual(paragraphs[0].text, "先完成项目权限设置。")
        self.assertIn("论文大纲", paragraphs[1].text)
        self.assertEqual(len(coverage["audit_history"]), 2)
        label = progress_label(2, "20260716-2")
        completion = format_gateway_completion(
            label,
            {
                "id": 2,
                "note": "/vault/note.md",
                "status": "complete",
                "transcript_source": "funasr_paraformer",
                "frames": 3,
            },
        )
        self.assertIn("[6/6]", completion)
        self.assertIn("处理完成", completion)
        self.assertIn("funasr_paraformer", completion)
        self.assertIn("保留画面：3", completion)
        self.assertIn("已暂存服务器", completion)
        self.assertIn("下载 2", completion)

    def test_targeted_audit_repair_is_persisted_to_checkpoint(self):
        segments = [
            Segment("s000001", 0, 2, "设置项目权限。"),
            Segment("s000002", 2, 4, "生成论文大纲。"),
        ]
        client = SequenceClient([
            {"units": [
                {"start_source_id": "s000001", "action": "keep", "kind": "step", "topic": "权限", "text": "设置项目权限。", "details": [], "drop_reason": None},
                {"start_source_id": "s000002", "action": "keep", "kind": "step", "topic": "大纲", "text": "生成论文大纲。", "details": [], "drop_reason": None},
            ]},
            {"corrections": []},
            {"sections": [
                {"start_unit_id": "u000001", "title": "设置项目权限", "objective": "说明权限", "format_hint": "prose"},
                {"start_unit_id": "u000002", "title": "生成论文大纲", "objective": "说明大纲", "format_hint": "prose"},
            ]},
            {"paragraphs": [{"start_unit_id": "u000001", "text": "设置项目权限。"}]},
            {"paragraphs": [{"start_unit_id": "u000002", "text": "处理论文内容。"}]},
            audit_repair(
                {"sec001": "pass", "sec002": "repair"},
                [{"section_id": "sec002", "unit_ids": ["u000002"], "kind": "missing", "instruction": "补回论文大纲"}],
            ),
            {"paragraphs": [{"start_unit_id": "u000002", "text": "生成论文大纲。"}]},
            audit_pass("sec001", "sec002"),
        ])
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "checkpoint.json"
            create_manuscript(segments, client, context="论文教程", checkpoint_path=checkpoint)
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(saved["sections"]["sec002"][0]["text"], "生成论文大纲。")
        self.assertTrue(saved["completed"])

    def test_global_terminology_pass_repairs_a_context_supported_asr_name(self):
        segments = [Segment("s000001", 0, 2, "指令放在五幺SCI点top。")]
        client = SequenceClient([
            {"units": [{"start_source_id": "s000001", "action": "keep", "kind": "step", "topic": "领取指令", "text": "指令放在五幺SCI点top。", "details": [], "drop_reason": None}]},
            {"corrections": []},
            {"sections": [{"start_unit_id": "u000001", "title": "领取配套指令", "objective": "说明领取位置", "format_hint": "prose"}]},
            {"paragraphs": [{"start_unit_id": "u000001", "text": "配套指令放在 51SCI.top。"}]},
            audit_pass("sec001"),
        ])
        paragraphs, coverage = create_manuscript(segments, client, context="学术写作教程")
        self.assertIn("51SCI.top", paragraphs[0].text)
        self.assertEqual(coverage["terminology_corrections"][0]["original"], "五幺SCI点top")

    def test_global_terminology_pass_unifies_cross_unit_entity_variants(self):
        segments = [
            Segment("s000001", 0, 2, "打开SY Paper输入选题。"),
            Segment("s000002", 2, 4, "把参考文献粘贴到SR pick。"),
        ]
        client = SequenceClient([
            {"units": [
                {"start_source_id": "s000001", "action": "keep", "kind": "step", "topic": "输入选题", "text": "打开SY Paper输入选题。", "details": [], "drop_reason": None},
                {"start_source_id": "s000002", "action": "keep", "kind": "step", "topic": "粘贴文献", "text": "把参考文献粘贴到SR pick。", "details": [], "drop_reason": None},
            ]},
            {"corrections": [
                {"unit_id": "u000002", "original": "SR pick", "replacement": "SY Paper", "confidence": "high", "reason": "同一工作流前文给出完整平台名"},
            ]},
            {"sections": [{"start_unit_id": "u000001", "title": "在SY Paper中输入选题并上传文献", "objective": "说明连续操作", "format_hint": "steps"}]},
            {"paragraphs": [{"start_unit_id": "u000001", "text": "在 SY Paper 中输入选题，再将参考文献粘贴到 SY Paper。"}]},
            audit_pass("sec001"),
        ])
        paragraphs, coverage = create_manuscript(segments, client, context="学术写作平台教程")
        self.assertNotIn("SR pick", paragraphs[0].text)
        self.assertEqual(coverage["terminology_corrections"][0]["replacement"], "SY Paper")

    def test_transcript_style_paragraph_opening_is_removed_deterministically(self):
        segments = [Segment("s000001", 0, 2, "接下来生成论文大纲。")]
        client = SequenceClient([
            {"units": [{"start_source_id": "s000001", "action": "keep", "kind": "step", "topic": "论文大纲", "text": "生成论文大纲。", "details": [], "drop_reason": None}]},
            {"corrections": []},
            {"sections": [{"start_unit_id": "u000001", "title": "生成论文大纲", "objective": "说明生成动作", "format_hint": "prose"}]},
            {"paragraphs": [{"start_unit_id": "u000001", "text": "然后生成论文大纲。"}]},
            audit_pass("sec001"),
        ])
        paragraphs, _coverage = create_manuscript(segments, client, context="论文教程")
        self.assertEqual(client.calls, 5)
        self.assertFalse(paragraphs[0].text.startswith("然后"))
        self.assertEqual(paragraphs[0].text, "生成论文大纲。")

    def test_failed_terminology_batch_is_atomic(self):
        segments = [Segment("s000001", 0, 2, "产品甲用于生成大纲。")]
        client = SequenceClient([
            {"units": [{"start_source_id": "s000001", "action": "keep", "kind": "step", "topic": "生成大纲", "text": "产品甲用于生成大纲。", "details": [], "drop_reason": None}]},
            {"corrections": [
                {"unit_id": "u000001", "original": "产品甲", "replacement": "产品乙", "confidence": "high", "reason": "测试"},
                {"unit_id": "invented", "original": "大纲", "replacement": "提纲", "confidence": "high", "reason": "测试"},
            ]},
            {"corrections": []},
            {"sections": [{"start_unit_id": "u000001", "title": "生成论文大纲", "objective": "说明用途", "format_hint": "prose"}]},
            {"paragraphs": [{"start_unit_id": "u000001", "text": "产品甲用于生成论文大纲。"}]},
            audit_pass("sec001"),
        ])
        paragraphs, coverage = create_manuscript(segments, client, context="教程")
        self.assertIn("产品甲", paragraphs[0].text)
        self.assertNotIn("产品乙", paragraphs[0].text)
        self.assertEqual(coverage["terminology_corrections"], [])

    def test_contextual_normalization_repairs_known_alias_and_malformed_phrases(self):
        source = "Credex配合自带超期一篇文献的平台，生成真实参考文献和三大纲的论文初稿。"
        segments = [Segment("s000001", 0, 2, source)]
        client = SequenceClient([
            {"units": [{"start_source_id": "s000001", "action": "keep", "kind": "explanation", "topic": "平台能力", "text": source, "details": [], "drop_reason": None}]},
            {"corrections": []},
            {"sections": [{"start_unit_id": "u000001", "title": "平台能力与初稿生成", "objective": "说明平台能力", "format_hint": "prose"}]},
            {"paragraphs": [{"start_unit_id": "u000001", "text": "Codex 配合自带大量文献的平台，生成依托真实参考文献和大纲生成的论文初稿。"}]},
            audit_pass("sec001"),
        ])
        paragraphs, coverage = create_manuscript(segments, client, context="标题：Codex论文教程")
        self.assertIn("Codex", paragraphs[0].text)
        self.assertIn("自带大量文献", paragraphs[0].text)
        self.assertNotIn("三大纲", paragraphs[0].text)
        self.assertGreaterEqual(len(coverage["terminology_corrections"]), 3)

    def test_outline_rejects_over_fragmentation_and_retries(self):
        units = [
            InformationUnit(
                id=f"u{index:06d}", source_ids=[f"s{index:06d}"], start=index,
                end=index + 1, action="keep", kind="step", topic=f"步骤{index}",
                text=f"完成步骤{index}。",
            )
            for index in range(1, 14)
        ]
        client = SequenceClient([
            {"sections": [
                {"start_unit_id": "u000001", "title": "准备项目环境", "objective": "准备", "format_hint": "steps"},
                {"start_unit_id": "u000004", "title": "搜索参考文献", "objective": "搜索", "format_hint": "steps"},
                {"start_unit_id": "u000007", "title": "生成论文大纲", "objective": "大纲", "format_hint": "steps"},
                {"start_unit_id": "u000010", "title": "生成论文初稿", "objective": "初稿", "format_hint": "steps"},
            ]},
            {"sections": [
                {"start_unit_id": "u000001", "title": "准备项目并搜索文献", "objective": "准备和搜索", "format_hint": "steps"},
                {"start_unit_id": "u000006", "title": "生成并核对论文大纲", "objective": "大纲", "format_hint": "steps"},
                {"start_unit_id": "u000010", "title": "生成论文初稿", "objective": "初稿", "format_hint": "steps"},
            ]},
        ])
        sections = _plan_outline(units, client, "论文教程")
        self.assertEqual(client.calls, 2)
        self.assertEqual(len(sections), 3)

    def test_presenter_style_audience_language_is_rewritten(self):
        segments = [Segment("s000001", 0, 2, "今天手把手教同学们使用Codex。")]
        client = SequenceClient([
            {"units": [{"start_source_id": "s000001", "action": "keep", "kind": "claim", "topic": "教程目标", "text": "视频将演示如何使用Codex。", "details": [], "drop_reason": None}]},
            {"corrections": []},
            {"sections": [{"start_unit_id": "u000001", "title": "教程目标", "objective": "说明教程目标", "format_hint": "prose"}]},
            {"paragraphs": [{"start_unit_id": "u000001", "text": "今天手把手教同学们使用 Codex。"}]},
            audit_pass("sec001"),
        ])
        paragraphs, _coverage = create_manuscript(segments, client, context="Codex教程")
        self.assertEqual(paragraphs[0].text, "视频演示如何使用 Codex。")

    def test_malformed_global_audit_json_is_retried(self):
        responses = manuscript_responses("完整内容。")
        responses[-1:] = ["{\"verdict\":\"pass\"", audit_pass("sec001")]
        client = RawSequenceClient(responses)
        paragraphs, coverage = create_manuscript(
            [Segment("s000001", 0, 2, "完整内容。")], client, context="测试"
        )
        self.assertEqual(paragraphs[0].text, "完整内容。")
        self.assertEqual(client.calls, 6)
        self.assertEqual(coverage["audit_history"][0]["verdict"], "pass")

    def test_audit_without_per_section_reviews_is_retried(self):
        responses = manuscript_responses("完整内容。")
        responses[-1:] = [
            {"verdict": "pass", "issues": []},
            audit_pass("sec001"),
        ]
        client = SequenceClient(responses)
        _paragraphs, coverage = create_manuscript(
            [Segment("s000001", 0, 2, "完整内容。")], client, context="测试"
        )
        self.assertEqual(client.calls, 6)
        self.assertEqual(
            coverage["audit_history"][0]["section_reviews"][0]["section_id"],
            "sec001",
        )

    def test_deterministic_editorial_diagnostics_detect_presenter_voice(self):
        sections = [
            type("Section", (), {"id": "sec001"})(),
        ]
        paragraphs = {
            "sec001": [Paragraph(["s000001"], "同学们，大家可以点击设置。")]
        }
        report = manuscript_quality_report(sections, paragraphs)
        self.assertEqual(report["status"], "fail")
        self.assertIn("presenter_voice", report["blockers"])

    def test_evaluate_reuses_raw_transcript_without_task_or_vault_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "metadata.json").write_text(
                json.dumps({"title": "测试视频", "owner": "测试作者"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (source / "raw-transcript.json").write_text(
                json.dumps(
                    {
                        "metadata": {"source": "fixture"},
                        "segments": [
                            {"id": "s000001", "start": 0, "end": 2, "text": "完整内容。"}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client = SequenceClient(
                direct_manuscript_responses("完整内容。")
            )
            with patch("video_manuscript.text_client", return_value=client), patch.dict(
                os.environ, {"VTM_STATE_DIR": str(root / "state")}
            ):
                result = evaluate_text_core(source)
            self.assertFalse(result["task_reserved"])
            self.assertFalse(result["vault_written"])
            self.assertTrue(Path(str(result["preview"])).is_file())
            self.assertFalse((root / "state" / "tasks.sqlite3").exists())

    def test_resume_copies_matching_semantic_checkpoint_for_signature_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            resume = Path(temp) / "old"
            target = Path(temp) / "new" / "manuscript-checkpoint.json"
            resume.mkdir()
            target.parent.mkdir()
            source = resume / "manuscript-checkpoint.json"
            source.write_text('{"signature":"abc"}', encoding="utf-8")
            _resume_checkpoint(resume, target)
            self.assertEqual(target.read_text(encoding="utf-8"), '{"signature":"abc"}')

    def test_gateway_reporter_emits_and_delivers_each_line_once(self):
        delivered = []
        reporter = GatewayProgressReporter(
            "今日任务 8 · 20260716-8",
            target="feishu",
            sender=lambda target, message: delivered.append((target, message)),
        )
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            reporter.progress("正在读取视频信息。")
            reporter.progress("处理完成。")
            reporter.completion(
                {
                    "id": 8,
                    "note": "/vault/demo.md",
                    "status": "complete",
                    "transcript_source": "native",
                    "frames": 1,
                }
            )
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("[1/6]", lines[0])
        self.assertIn("[6/6]", lines[1])
        self.assertEqual(delivered, [("feishu", lines[0]), ("feishu", lines[1])])

    def test_progress_delivery_failure_does_not_abort_video_job(self):
        def broken_sender(_target, _message):
            raise RuntimeError("offline")

        reporter = GatewayProgressReporter(
            "今日任务 9 · 20260716-9", target="feishu", sender=broken_sender
        )
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            reporter.progress("正在读取视频信息。")
        self.assertIn("[1/6]", output.getvalue())
        self.assertEqual(len(reporter.delivery_errors), 1)

    def test_failure_notification_is_an_explicit_terminal_stage(self):
        delivered = []
        reporter = GatewayProgressReporter(
            "今日任务 8 · 20260716-8",
            target="feishu",
            sender=lambda target, message: delivered.append(message),
        )
        with contextlib.redirect_stderr(io.StringIO()):
            reporter.terminal("FAILED", "任务已结束，未生成笔记。")
        self.assertEqual(len(delivered), 1)
        self.assertIn("[6/6 · FAILED]", delivered[0])
        self.assertIn("任务已结束", delivered[0])

    def test_preflight_failure_is_sent_to_gateway_without_a_task_number(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ), patch.object(
            sys,
            "argv",
            [
                "video_manuscript.py",
                "run",
                "--url",
                "BV1ab411c7DE",
                "--vault",
                str(Path(temp) / "vault"),
                "--max-frames",
                "121",
                "--gateway-output",
                "--progress-target",
                "feishu",
            ],
        ), patch("video_manuscript.load_runtime_env"), patch(
            "video_manuscript.duplicate_skill_paths", return_value=[]
        ), patch("video_manuscript.send_hermes_progress") as sender, contextlib.redirect_stderr(
            io.StringIO()
        ):
            self.assertEqual(main(), 1)
        sender.assert_called_once()
        self.assertIn("[6/6 · FAILED]", sender.call_args.args[1])
        self.assertIn("未生成笔记", sender.call_args.args[1])

    def test_bundle_contains_markdown_and_assets_but_not_source_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            state = vault / "state"
            job = vault / "Sources" / "Videos" / "2026" / "2026-07" / "demo [BV11JNn6mESN-p1]"
            (job / "assets").mkdir(parents=True)
            (job / "demo.md").write_text("![frame](assets/001.png)", encoding="utf-8")
            (job / "assets" / "001.png").write_bytes(b"png")
            with patch.dict(os.environ, {"VTM_STATE_DIR": str(state)}):
                task = reserve_task(vault, url="u", bvid="BV11JNn6mESN")
                update_task(vault, task["task_key"], status="complete", job_dir=str(job), note=str(job / "demo.md"))
                result = bundle_job(vault, task_id=task["task_key"])
            archive = Path(str(result["bundle"]))
            with zipfile.ZipFile(archive) as handle:
                names = handle.namelist()
            self.assertTrue(any(name.endswith("demo.md") for name in names))
            self.assertTrue(any(name.endswith("assets/") for name in names))
            self.assertTrue(any(name.endswith("assets/001.png") for name in names))
            self.assertFalse(any("/source/" in name for name in names))

    def test_document_task_bundle_uses_source_identity_without_bv_parsing(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "vault"
            state = Path(temp) / "state"
            job = vault / "Sources" / "Documents" / "opus"
            job.mkdir(parents=True)
            note = job / "opus.md"
            note.write_text("正文", encoding="utf-8")
            with patch.dict(os.environ, {"VTM_STATE_DIR": str(state)}):
                task = reserve_task(
                    vault,
                    url="https://www.bilibili.com/opus/1226076978200707093",
                    platform="bilibili_opus",
                    source_kind="document",
                    source_id="opus-1226076978200707093",
                    source_key="bilibili_opus:opus-1226076978200707093",
                )
                update_task(
                    vault,
                    task["task_key"],
                    status="complete",
                    job_dir=str(job),
                    note=str(note),
                )
                result = bundle_job(vault, task_id=task["task_key"])
            archive = Path(str(result["bundle"]))
            self.assertTrue(archive.is_file())
            self.assertIn("opus-1226076978200707093-source-manuscript.zip", archive.name)

    def test_bundle_refuses_a_registered_directory_outside_the_vault(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            outside = root / "outside"
            outside.mkdir()
            note = outside / "note.md"
            note.write_text("正文", encoding="utf-8")
            with patch.dict(os.environ, {"VTM_STATE_DIR": str(root / "state")}):
                task = reserve_task(vault, url="u", bvid="BV1outside")
                update_task(
                    vault,
                    task["task_key"],
                    status="complete",
                    job_dir=str(outside),
                    note=str(note),
                )
                with self.assertRaisesRegex(FileNotFoundError, "记录不一致"):
                    bundle_job(vault, task_id=task["task_key"])

    def test_bundle_refuses_symlinked_assets_and_audit_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            state = root / "state"
            job = vault / "Sources" / "Videos" / "2026" / "2026-07" / "demo"
            assets = job / "assets"
            assets.mkdir(parents=True)
            note = job / "demo.md"
            note.write_text("正文", encoding="utf-8")
            outside = root / "outside.txt"
            outside.write_text("不能进入压缩包", encoding="utf-8")
            (assets / "linked.png").symlink_to(outside)
            with patch.dict(os.environ, {"VTM_STATE_DIR": str(state)}):
                task = reserve_task(vault, url="u", bvid="BV1symlink")
                update_task(
                    vault,
                    task["task_key"],
                    status="complete",
                    job_dir=str(job),
                    note=str(note),
                )
                with self.assertRaisesRegex(RuntimeError, "符号链接"):
                    bundle_job(vault, task_id=task["task_key"])

                (assets / "linked.png").unlink()
                audit = state / "tasks" / str(task["task_key"])
                audit.mkdir(parents=True, exist_ok=True)
                (audit / "linked.json").symlink_to(outside)
                with self.assertRaisesRegex(RuntimeError, "符号链接"):
                    bundle_job(vault, task_id=task["task_key"], include_source=True)

    def test_download_sender_uses_media_document_protocol_once(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "note.zip"
            archive.write_bytes(b"zip")
            completed = type("Completed", (), {"returncode": 0, "stderr": ""})()
            with patch("video_manuscript._hermes_binary", return_value="/bin/hermes"), patch(
                "video_manuscript.subprocess.run", return_value=completed
            ) as runner:
                send_hermes_document("feishu", archive)
            runner.assert_called_once()
            argv = runner.call_args.args[0]
            self.assertEqual(argv[:5], ["/bin/hermes", "send", "--to", "feishu", "--quiet"])
            self.assertEqual(argv[5], f"[[as_document]] MEDIA:{archive.resolve()}")

    def test_needs_review_draft_is_not_downloadable(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            state = vault / "state"
            job = vault / "Sources" / "Videos" / "draft"
            job.mkdir(parents=True)
            note = job / "draft.md"
            note.write_text("未通过的草稿", encoding="utf-8")
            with patch.dict(os.environ, {"VTM_STATE_DIR": str(state)}):
                task = reserve_task(vault, url="u", bvid="BV1review")
                update_task(vault, task["task_key"], status="needs_review", job_dir=str(job), note=str(note))
                with self.assertRaisesRegex(RuntimeError, "不可下载"):
                    bundle_job(vault, task_id=task["task_key"])

    def test_existing_jobs_get_persistent_numbers_and_download_by_task(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            state = vault / "state"
            job = vault / "Sources" / "Videos" / "2026" / "2026-07" / "demo [BV11JNn6mESN-p1]"
            (job / "assets").mkdir(parents=True)
            (job / "demo.md").write_text("正文", encoding="utf-8")
            with patch.dict(os.environ, {"VTM_STATE_DIR": str(state)}):
                task = reserve_task(vault, url="u", bvid="BV11JNn6mESN")
                update_task(vault, task["task_key"], status="complete", job_dir=str(job), note=str(job / "demo.md"))
                result = bundle_job(vault, task_id=1)
                self.assertTrue(Path(str(result["bundle"])).is_file())
                self.assertEqual(list_tasks(vault)[0]["bundle"], result["bundle"])

    def test_reserved_task_is_not_duplicated_when_job_appears(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            with patch.dict(os.environ, {"VTM_STATE_DIR": str(vault / "state")}):
                task = reserve_task(vault, url="u", bvid="BV11JNn6mESN")
                update_task(vault, task["task_key"], status="complete")
                self.assertEqual(len(list_tasks(vault)), 1)
                self.assertRegex(task["task_key"], r"^\d{8}-1$")

    def test_failed_video_blocks_blind_retry_and_preserves_first_task_number(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            with patch.dict(os.environ, {"VTM_STATE_DIR": str(vault / "state")}):
                task = reserve_task(vault, url="u", bvid="BV1failed")
                update_task(vault, task["task_key"], status="failed", error="ASR failed")
                found = find_existing_video_task(vault, "BV1failed")
                self.assertIsNotNone(found)
                self.assertEqual(found["task_key"], task["task_key"])
                self.assertEqual(len(list_tasks(vault)), 1)

    def test_duplicate_detection_distinguishes_video_parts(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            with patch.dict(os.environ, {"VTM_STATE_DIR": str(vault / "state")}):
                task = reserve_task(vault, url="u", bvid="BV1parts", part=1)
                update_task(vault, task["task_key"], status="complete")
                self.assertIsNotNone(find_existing_video_task(vault, "BV1parts", 1))
                self.assertIsNone(find_existing_video_task(vault, "BV1parts", 2))

    def test_funasr_batch_is_bounded_for_reference_server(self):
        self.assertEqual(FUNASR_BATCH_SECONDS, 60)

    def test_current_server_json_registry_migrates_without_installing_v3_first(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ):
            vault = Path(temp) / "vault"
            job = vault / "Video Notes" / "旧版笔记 [BV11JNn6mESN-p1]"
            job.mkdir(parents=True)
            note = job / "旧版笔记.md"
            note.write_text("旧版正文", encoding="utf-8")
            (vault / ".video-manuscript-tasks.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "tasks": [
                            {
                                "id": 1,
                                "bvid": "BV11JNn6mESN",
                                "title": "旧版笔记",
                                "status": "complete",
                                "created_at": "2026-07-15T21:28:00+00:00",
                                "updated_at": "2026-07-15T21:57:32+00:00",
                                "job_dir": str(job),
                                "note": str(note),
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            migrated = list_tasks(vault, all_tasks=True)
            self.assertEqual(len(migrated), 1)
            self.assertEqual(migrated[0]["task_key"], "20260716-1")
            self.assertEqual(Path(migrated[0]["note"]), note)

    def test_runtime_env_loader_is_allowlisted_and_sets_default_vault(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            env_dir = home / ".hermes"
            env_dir.mkdir()
            (env_dir / ".env").write_text(
                "DEEPSEEK_API_KEY='secret-value'\n"
                "VTM_VAULT=/tmp/example-vault\n"
                "VTM_FINAL_VISUAL_HEIGHT=1080\n"
                "UNRELATED_SECRET=must-not-load\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True), patch(
                "video_manuscript.Path.home", return_value=home
            ):
                load_runtime_env()
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "secret-value")
                self.assertNotIn("UNRELATED_SECRET", os.environ)
                self.assertEqual(default_vault(), Path("/tmp/example-vault"))
                self.assertEqual(os.environ["VTM_FINAL_VISUAL_HEIGHT"], "1080")

    def test_source_adapter_registry_wraps_frozen_bilibili_identity(self):
        adapter = adapter_for("BV1ab411c7DE")
        self.assertIsInstance(adapter, SourceAdapter)
        self.assertIsInstance(adapter, BilibiliSourceAdapter)
        info = VideoInfo(
            url="https://www.bilibili.com/video/BV1ab411c7DE/?p=2",
            bvid="BV1ab411c7DE",
            cid=99,
            part=2,
            title="主标题",
            part_title="第二部分",
            duration=120.0,
            owner="作者",
            cover="",
        )
        reference = adapter.reference(info)
        self.assertEqual(reference.source_key, "bilibili:BV1ab411c7DE:p2")
        self.assertEqual(reference.title, "主标题 - 第二部分")
        self.assertEqual(reference.author, "作者")
        self.assertIsInstance(
            adapter_for("https://example.com/article"), GenericWebSourceAdapter
        )

    def test_bilibili_opus_and_column_route_as_documents_not_bv_videos(self):
        opus = "https://www.bilibili.com/opus/1226076978200707093?spm_id_from=333.1365.0.0"
        adapter = adapter_for(opus)
        self.assertIsInstance(adapter, BilibiliDocumentSourceAdapter)
        self.assertEqual(adapter.source_kind, "document")
        self.assertEqual(adapter.platform, "bilibili_opus")
        self.assertEqual(
            adapter.canonicalize_input(opus),
            "https://www.bilibili.com/opus/1226076978200707093",
        )
        self.assertEqual(
            adapter.source_id_from_url(opus),
            "opus-1226076978200707093",
        )
        column = adapter_for("https://www.bilibili.com/read/cv123456")
        self.assertIsInstance(column, BilibiliDocumentSourceAdapter)
        self.assertEqual(column.source_id_from_url("https://www.bilibili.com/read/cv123456"), "article-123456")
        self.assertIsInstance(
            adapter_by_platform("bilibili_opus"),
            BilibiliDocumentSourceAdapter,
        )

    def test_bilibili_video_and_wechat_article_keep_separate_routes(self):
        self.assertIsInstance(
            adapter_for("https://www.bilibili.com/video/BV1ab411c7DE"),
            BilibiliSourceAdapter,
        )
        wechat = adapter_for("https://mp.weixin.qq.com/s/example-public-article")
        self.assertIsInstance(wechat, GenericWebSourceAdapter)
        self.assertNotIsInstance(wechat, BilibiliDocumentSourceAdapter)

    def test_youtube_adapter_normalizes_supported_public_urls(self):
        client = YouTubeClient()
        video_id = "BaW_jenozKc"
        for value in (
            video_id,
            f"https://youtu.be/{video_id}?si=test",
            f"https://www.youtube.com/watch?v={video_id}&list=ignored",
            f"https://www.youtube.com/shorts/{video_id}",
        ):
            self.assertEqual(
                client.normalize_input_url(value),
                f"https://www.youtube.com/watch?v={video_id}",
            )
        adapter = adapter_for(f"https://youtu.be/{video_id}")
        self.assertIsInstance(adapter, YouTubeSourceAdapter)
        self.assertEqual(adapter.source_id_from_url(video_id), video_id)
        with self.assertRaises(ValueError):
            client.video_id(f"https://youtube.com.evil.example/watch?v={video_id}")

    def test_youtube_json3_and_vtt_parsers_preserve_timestamps_and_text(self):
        json3 = json.dumps(
            {
                "events": [
                    {"tStartMs": 1000, "dDurationMs": 1500, "segs": [{"utf8": "第一句"}]},
                    {"tStartMs": 2500, "dDurationMs": 1000, "segs": [{"utf8": "第二句"}]},
                ]
            },
            ensure_ascii=False,
        )
        parsed_json = parse_youtube_json3(json3)
        self.assertEqual([item.text for item in parsed_json], ["第一句", "第二句"])
        self.assertEqual((parsed_json[0].start, parsed_json[0].end), (1.0, 2.5))

        vtt = """WEBVTT

00:00:01.000 --> 00:00:02.500
<c>第一句</c>

00:00:02.500 --> 00:00:03.500 align:start
第二句 &amp; 细节
"""
        parsed_vtt = parse_youtube_vtt(vtt)
        self.assertEqual([item.text for item in parsed_vtt], ["第一句", "第二句 & 细节"])
        self.assertEqual(parsed_vtt[1].start, 2.5)

    def test_youtube_prefers_manual_subtitles_over_automatic_captions(self):
        video_id = "BaW_jenozKc"
        client = YouTubeClient()
        payload = {
            "id": video_id,
            "subtitles": {
                "zh-CN": [
                    {
                        "ext": "json3",
                        "url": "https://www.youtube.com/api/timedtext?manual=1",
                    }
                ]
            },
            "automatic_captions": {
                "zh-CN": [
                    {
                        "ext": "json3",
                        "url": "https://www.youtube.com/api/timedtext?auto=1",
                    }
                ]
            },
        }
        client._cache[f"{video_id}:metadata"] = payload
        response_body = json.dumps(
            {
                "events": [
                    {"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "人工字幕"}]}
                ]
            },
            ensure_ascii=False,
        ).encode()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return "https://www.youtube.com/api/timedtext?manual=1"

            def read(self):
                return response_body

        info = YouTubeVideoInfo(
            url=f"https://www.youtube.com/watch?v={video_id}",
            video_id=video_id,
            title="测试",
            duration=1,
            owner="频道",
            language="zh-CN",
        )
        with patch("vtm_core.youtube.urllib.request.urlopen", return_value=Response()):
            segments, metadata = client.subtitles(info)
        self.assertEqual(segments[0].text, "人工字幕")
        self.assertEqual(metadata["source"], "youtube_manual_subtitle")

    def test_youtube_automatic_caption_selection_prefers_original_language(self):
        tracks = {
            "zh-Hans": [{"ext": "json3", "url": "https://www.youtube.com/translated"}],
            "en-orig": [{"ext": "json3", "url": "https://www.youtube.com/original"}],
        }
        language, entry = YouTubeClient._track(tracks, "", prefer_original=True)
        self.assertEqual(language, "en-orig")
        self.assertTrue(entry["url"].endswith("/original"))

    def test_task_registry_persists_generic_source_identity(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ):
            vault = Path(temp) / "vault"
            task = reserve_task(
                vault,
                url="https://www.youtube.com/watch?v=BaW_jenozKc",
                platform="youtube",
                source_kind="video",
                source_id="BaW_jenozKc",
                source_key="youtube:BaW_jenozKc",
            )
            self.assertEqual(task["platform"], "youtube")
            self.assertEqual(task["source_key"], "youtube:BaW_jenozKc")
            self.assertIsNone(task["bvid"])

    def test_configuration_menu_is_deterministic_and_never_returns_secret_values(self):
        env = {
            "VTM_LLM_API_KEY": "text-secret",
            "BILIBILI_COOKIE": "SESSDATA=private",
        }
        menu = configuration_menu(env)
        self.assertTrue(menu["core"]["text_llm_key"])
        self.assertEqual(menu["secret_delivery"], "never_send_in_chat")
        encoded = json.dumps(menu, ensure_ascii=False)
        self.assertNotIn("text-secret", encoded)
        self.assertNotIn("SESSDATA=private", encoded)
        bilibili = menu["platforms"][0]
        self.assertEqual(bilibili["number"], 1)
        self.assertTrue(bilibili["adapter_installed"])
        self.assertTrue(bilibili["credentials"]["bilibili_cookie"])
        zhihu = platform_configuration("3", {})
        self.assertEqual(zhihu["platform"], "zhihu")
        self.assertTrue(zhihu["adapter_installed"])
        self.assertEqual(zhihu["credentials"][0]["id"], "zhihu_z_c0")
        self.assertFalse(zhihu["credentials"][0]["configured"])
        douyin = platform_configuration("5", {})
        self.assertEqual(douyin["platform"], "douyin")
        self.assertTrue(douyin["adapter_installed"])
        self.assertEqual(douyin["credentials"], [])
        self.assertIn("无需配置", douyin["secret_instruction"])
        xhs = platform_configuration("6", {})
        self.assertEqual(xhs["platform"], "xiaohongshu")
        self.assertTrue(xhs["adapter_installed"])
        self.assertEqual(xhs["credentials"], [])
        self.assertEqual(platform_configuration("B站", {})["platform"], "bilibili")

    def test_dedicated_secret_store_is_private_allowlisted_and_removable(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            path = Path(temp) / "config" / "secrets.env"
            result = set_secret("bilibili_cookie", "SESSDATA=private", path=path)
            self.assertTrue(result["configured"])
            self.assertFalse(result["value_printed"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            stored = path.read_text(encoding="utf-8")
            self.assertIn("BILIBILI_COOKIE=SESSDATA=private", stored)
            self.assertNotIn("UNRELATED_SECRET", stored)
            removed = remove_secret("bilibili_cookie", path=path)
            self.assertTrue(removed["removed"])
            self.assertNotIn("BILIBILI_COOKIE=", path.read_text(encoding="utf-8"))
            with self.assertRaises(KeyError):
                set_secret("unapproved_secret", "value", path=path)

    def test_runtime_env_loads_dedicated_store_then_legacy_hermes_env(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            dedicated = secret_store_path(home)
            dedicated.parent.mkdir(parents=True)
            dedicated.write_text("BILIBILI_COOKIE=dedicated-cookie\n", encoding="utf-8")
            hermes = home / ".hermes" / ".env"
            hermes.parent.mkdir(parents=True)
            hermes.write_text(
                "BILIBILI_COOKIE=legacy-cookie\nDEEPSEEK_API_KEY=model-key\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True), patch(
                "video_manuscript.Path.home", return_value=home
            ):
                load_runtime_env()
                self.assertEqual(os.environ["BILIBILI_COOKIE"], "dedicated-cookie")
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "model-key")

    def test_configure_secret_refuses_chat_or_pipe_input(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}, clear=True
        ), patch("video_manuscript.install_cancel_handlers"), patch.object(
            sys, "argv", ["video_manuscript.py", "configure", "secret", "bilibili_cookie"]
        ), patch.object(sys.stdin, "isatty", return_value=False), contextlib.redirect_stderr(
            io.StringIO()
        ):
            self.assertEqual(main(), 1)
            self.assertFalse(secret_store_path(Path(temp)).exists())

    def test_duplicate_skill_installations_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            first = home / ".hermes" / "skills" / "manuloom"
            backup = home / ".hermes" / "skills" / "manuloom.pre-v4"
            first.mkdir(parents=True)
            backup.mkdir(parents=True)
            manifest = "---\nname: manuloom\ndescription: test\n---\n"
            (first / "SKILL.md").write_text(manifest, encoding="utf-8")
            (backup / "SKILL.md").write_text(manifest, encoding="utf-8")
            with patch("video_manuscript.Path.home", return_value=home):
                paths = duplicate_skill_paths()
            self.assertEqual(len(paths), 2)

    def test_url_validation_and_bvid(self):
        client = BilibiliClient(cookie="")
        self.assertEqual(
            client.extract_bvid("https://www.bilibili.com/video/BV1ab411c7DE?p=2"),
            "BV1ab411c7DE",
        )
        client._validate_url("https://b23.tv/abc")
        self.assertEqual(
            client.normalize_input_url("BV1ab411c7DE"),
            "https://www.bilibili.com/video/BV1ab411c7DE/",
        )
        self.assertEqual(
            client.normalize_input_url("av123"),
            "https://www.bilibili.com/video/av123/",
        )
        self.assertEqual(
            client.extract_part("https://www.bilibili.com/video/BV1ab411c7DE?p=2"),
            2,
        )
        with self.assertRaises(ValueError):
            client._validate_url("https://bilibili.com.evil.example/video/BV1ab411c7DE")

    def test_short_link_resolution_does_not_forward_login_cookie(self):
        client = BilibiliClient(cookie="SESSDATA=private")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return "https://www.bilibili.com/video/BV1ab411c7DE/"

        with patch("vtm_core.bilibili.urllib.request.urlopen", return_value=Response()) as opener:
            resolved = client.resolve("https://b23.tv/demo")
        request = opener.call_args.args[0]
        self.assertEqual(resolved, "https://www.bilibili.com/video/BV1ab411c7DE/")
        self.assertNotIn("Cookie", request.headers)

    def test_av_url_is_resolved_through_metadata(self):
        client = BilibiliClient(cookie="")
        requests = []
        def fake_request(url, params=None):
            requests.append(params or {})
            return {
            "code": 0,
            "data": {
                "bvid": "BV1ab411c7DE",
                "title": "demo",
                "cid": 42,
                "duration": 10,
                "pages": [{"cid": 42, "duration": 10, "part": "demo"}],
            },
        }
        client._request = fake_request
        info = client.inspect("av123")
        self.assertEqual(info.bvid, "BV1ab411c7DE")
        self.assertEqual(info.cid, 42)

    def test_player_api_selects_audio_and_bounded_video(self):
        info = VideoInfo(
            url="https://www.bilibili.com/video/BV1ab411c7DE",
            bvid="BV1ab411c7DE",
            cid=42,
            part=1,
            title="demo",
            part_title="demo",
            duration=10,
            owner="",
            cover="",
        )
        client = BilibiliClient(cookie="")
        requests = []
        def fake_request(url, params=None):
            requests.append(params or {})
            return {
                "code": 0,
                "data": {
                    "dash": {
                        "audio": [
                            {"id": 1, "bandwidth": 64000, "baseUrl": "https://a.bilivideo.com/a"},
                            {"id": 2, "bandwidth": 128000, "baseUrl": "https://a.bilivideo.com/b"},
                        ],
                        "video": [
                            {"id": 16, "height": 360, "bandwidth": 100, "baseUrl": "https://v.bilivideo.com/360"},
                            {"id": 64, "height": 720, "bandwidth": 200, "baseUrl": "https://v.bilivideo.com/720"},
                            {"id": 80, "height": 1080, "bandwidth": 300, "baseUrl": "https://v.bilivideo.com/1080"},
                        ],
                    }
                },
            }
        client._request = fake_request
        self.assertEqual(client.media_stream(info, audio_only=True)["quality"], 2)
        video = client.media_stream(info, audio_only=False, max_height=720)
        self.assertEqual(video["height"], 720)
        self.assertEqual(requests[-1]["qn"], 64)
        high = client.media_stream(info, audio_only=False, max_height=1080)
        self.assertEqual(high["height"], 1080)
        self.assertEqual(requests[-1]["qn"], 80)
        with self.assertRaises(RuntimeError):
            client._validate_media_url("https://bilivideo.com.evil.example/video.m4s")

        client._request = lambda url, params=None: {
            "code": 0,
            "data": {
                "dash": {
                    "video": [
                        {
                            "id": 80,
                            "height": 1080,
                            "bandwidth": 300,
                            "baseUrl": "https://v.bilivideo.com/1080",
                        }
                    ]
                }
            },
        }
        with self.assertRaisesRegex(RuntimeError, "within the requested height"):
            client.media_stream(info, audio_only=False, max_height=720)

    def test_cookie_ai_transcript_parses_timestamped_fragments(self):
        info = VideoInfo(
            url="https://www.bilibili.com/video/BV1ab411c7DE",
            bvid="BV1ab411c7DE",
            cid=42,
            part=1,
            title="demo",
            part_title="demo",
            duration=10,
            owner="",
            cover="",
        )
        client = BilibiliClient(cookie="SESSDATA=test")
        client._wbi_keys = lambda: ("a" * 32, "b" * 32)
        client._request = lambda url, params=None: {
            "code": 0,
            "data": {
                "stid": "123",
                "model_result": {
                    "result_type": 2,
                    "subtitle": [
                        {
                            "part_subtitle": [
                                {
                                    "content": "带时间的完整句子",
                                    "start_timestamp": 1,
                                    "end_timestamp": 3,
                                }
                            ]
                        }
                    ],
                },
            },
        }
        segments, metadata = client.ai_transcript(info)
        self.assertEqual(segments[0].text, "带时间的完整句子")
        self.assertEqual(segments[0].start, 1)
        self.assertEqual(metadata["source"], "bilibili_ai_conclusion")

    def test_parse_funasr_srt(self):
        segments = parse_srt(
            "1\n00:00:00,500 --> 00:00:02,000\n第一句话。\n\n"
            "2\n00:00:02.100 --> 00:00:05.250\n第二句话，保留解释。\n"
        )
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].start, 0.5)
        self.assertEqual(segments[1].end, 5.25)
        self.assertIn("保留解释", segments[1].text)

    def test_model_alias_survives_inaccessible_inherited_working_directory(self):
        with patch("vtm_core.asr.Path.is_dir", side_effect=[PermissionError(), False]):
            self.assertIsNone(faster_whisper_model_path("medium"))

    def test_safe_name(self):
        self.assertEqual(safe_name('bad:/name*?"'), "bad name")
        self.assertEqual(safe_name("   "), "video")

    def test_missing_text_model_refuses_to_publish_raw_asr(self):
        segments = [
            Segment("s000001", 0, 1, "嗯，第一点"),
            Segment("s000002", 1, 2, "这是解释。"),
        ]
        with self.assertRaisesRegex(RuntimeError, "拒绝发布"):
            edit_transcript(segments, None)

    def test_model_omission_blocks_publication(self):
        segments = [
            Segment("s000001", 0, 1, "第一点。"),
            Segment("s000002", 1, 2, "重要的例子。"),
        ]
        client = FakeClient(
            {
                "paragraphs": [{"start_source_id": "s000001", "text": "第一点。"}],
            }
        )
        with self.assertRaisesRegex(RuntimeError, "质量门禁"):
            edit_transcript(segments, client)

    def test_model_cannot_delete_meaningful_segment_as_filler(self):
        segments = [Segment("s000001", 0, 2, "这个限制条件非常重要。")]
        client = FakeClient({"paragraphs": []})
        with self.assertRaisesRegex(RuntimeError, "质量门禁"):
            edit_transcript(segments, client)

    def test_filler_words_may_be_omitted_but_source_id_stays_accounted(self):
        segments = [
            Segment("s000001", 0, 1, "好，OK，接下来我们来看一下。"),
            Segment("s000002", 1, 3, "点击插件，然后允许浏览器连接网络。"),
        ]
        client = FakeClient({
            "paragraphs": [{
                "start_source_id": "s000001",
                "text": "点击“插件”，然后允许浏览器连接网络。",
                "heading": None,
            }],
        })
        paragraphs, coverage = edit_transcript(segments, client)
        self.assertEqual(coverage["quality_status"], "pass")
        self.assertEqual(coverage["accounted_count"], 2)
        self.assertIn("允许", paragraphs[0].text)

    def test_batch_edit_gets_three_total_attempts(self):
        segments = [Segment("s000001", 0, 3, "点击插件，然后允许浏览器连接网络。")]
        invalid = {"paragraphs": []}
        valid = {
            "paragraphs": [
                {
                    "start_source_id": "s000001",
                    "text": "点击插件，然后允许浏览器连接网络。",
                    "heading": None,
                }
            ],
        }
        client = SequenceClient([invalid, invalid, valid])
        paragraphs, coverage = edit_transcript(segments, client)
        self.assertEqual(client.calls, BATCH_EDIT_MAX_ATTEMPTS + 1)
        self.assertEqual(coverage["quality_status"], "pass")
        self.assertEqual(len(paragraphs), 1)

    def test_required_faithful_batch_fails_fast_with_its_own_error(self):
        segments = [
            Segment("s000001", 0, 2, "必须保留这一项具体操作。"),
            Segment("s000002", 2, 4, "还必须保留检查方法。"),
        ]
        client = SequenceClient([{"paragraphs": []}])
        with self.assertRaisesRegex(
            RuntimeError,
            r"失败分批：1；第 1 批忠实校订失败：",
        ):
            edit_transcript(segments, client)
        self.assertEqual(client.calls, BATCH_EDIT_MAX_ATTEMPTS)

    def test_oversized_faithful_draft_reaches_structure_refinement(self):
        repeated = "必须完整保留这一项具体操作、参数、解释和检查方法。" * 9
        segments = [
            Segment(f"s{index:06d}", (index - 1) * 20, index * 20, repeated)
            for index in range(1, 5)
        ]
        faithful = {
            "paragraphs": [
                {
                    "start_source_id": "s000001",
                    "text": "".join(segment.text for segment in segments),
                    "heading": "完整操作",
                }
            ]
        }
        refined = {
            "paragraphs": [
                {
                    "start_source_id": segment.id,
                    "text": segment.text,
                    "heading": "操作步骤" if index == 0 else None,
                }
                for index, segment in enumerate(segments)
            ]
        }
        client = SequenceClient([faithful, refined])
        paragraphs, coverage = edit_transcript(segments, client)
        self.assertEqual(client.calls, 3)
        self.assertEqual(coverage["quality_status"], "pass")
        self.assertEqual(len(paragraphs), 4)
        self.assertTrue(all(len(item.text) <= 700 for item in paragraphs))

    def test_oversized_model_prose_is_split_without_losing_source_ids(self):
        sentence = "必须保留具体按钮、参数、解释和检查方法。"
        segments = [
            Segment(f"s{index:06d}", (index - 1) * 10, index * 10, sentence * 5)
            for index in range(1, 9)
        ]
        source_text = "".join(segment.text for segment in segments)
        paragraphs, _ = _paragraphs_from_response(
            {
                "paragraphs": [
                    {
                        "start_source_id": "s000001",
                        "text": source_text,
                        "heading": "完整操作",
                    }
                ]
            },
            segments,
        )
        self.assertGreater(len(paragraphs), 1)
        self.assertTrue(all(len(item.text) <= 700 for item in paragraphs))
        self.assertEqual(
            [source_id for paragraph in paragraphs for source_id in paragraph.source_ids],
            [segment.id for segment in segments],
        )
        self.assertEqual("".join(paragraph.text for paragraph in paragraphs), source_text)

    def test_program_assigns_omitted_filler_range_without_model_id_bookkeeping(self):
        segments = [
            Segment("s000001", 0, 1, "好，OK，接下来我们来看一下。"),
            Segment("s000002", 1, 3, "点击插件，然后允许浏览器连接网络。"),
        ]
        client = FakeClient({
            "paragraphs": [{
                "start_source_id": "s000001",
                "text": "点击“插件”，然后允许浏览器连接网络。",
                "heading": None,
            }],
        })
        paragraphs, coverage = edit_transcript(segments, client)
        self.assertEqual(paragraphs[0].source_ids, ["s000001", "s000002"])
        self.assertEqual(coverage["assignment_method"], "deterministic_start_ranges")

    def test_program_expands_multiple_start_boundaries_without_model_id_lists(self):
        segments = [
            Segment(f"s{index:06d}", index, index + 1, f"第 {index} 条包含具体解释。")
            for index in range(1, 6)
        ]
        paragraphs, removed = _paragraphs_from_response(
            {
                "paragraphs": [
                    {
                        "start_source_id": "s000001",
                        "text": "第 1 条包含具体解释。第 2 条包含具体解释。第 3 条包含具体解释。",
                        "heading": "第一阶段",
                    },
                    {
                        "start_source_id": "s000004",
                        "text": "第 4 条包含具体解释。第 5 条包含具体解释。",
                        "heading": "第二阶段",
                    },
                ]
            },
            segments,
        )
        self.assertEqual(paragraphs[0].source_ids, ["s000001", "s000002", "s000003"])
        self.assertEqual(paragraphs[1].source_ids, ["s000004", "s000005"])
        self.assertEqual(
            [source_id for paragraph in paragraphs for source_id in paragraph.source_ids],
            [segment.id for segment in segments],
        )
        self.assertEqual(removed, set())

    def test_start_boundary_must_begin_with_first_segment(self):
        segments = [
            Segment("s000001", 0, 1, "第一条内容。"),
            Segment("s000002", 1, 2, "第二条内容。"),
        ]
        with self.assertRaisesRegex(ValueError, "first start_source_id must be s000001"):
            _paragraphs_from_response(
                {"paragraphs": [{"start_source_id": "s000002", "text": "第二条内容。"}]},
                segments,
            )

    def test_start_boundaries_reject_duplicate_out_of_order_and_unknown_ids(self):
        segments = [
            Segment("s000001", 0, 1, "第一条内容。"),
            Segment("s000002", 1, 2, "第二条内容。"),
            Segment("s000003", 2, 3, "第三条内容。"),
        ]
        invalid_payloads = [
            {
                "paragraphs": [
                    {"start_source_id": "s000001", "text": "第一条内容。"},
                    {"start_source_id": "s000001", "text": "第二条内容。"},
                ]
            },
            {
                "paragraphs": [
                    {"start_source_id": "s000001", "text": "第一条内容。"},
                    {"start_source_id": "s000003", "text": "第三条内容。"},
                    {"start_source_id": "s000002", "text": "第二条内容。"},
                ]
            },
            {"paragraphs": [{"start_source_id": "invented", "text": "虚构内容。"}]},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    _paragraphs_from_response(payload, segments)

    def test_duplicate_input_source_ids_are_rejected_before_assignment(self):
        segments = [
            Segment("s000001", 0, 1, "第一条内容。"),
            Segment("s000001", 1, 2, "第二条内容。"),
        ]
        with self.assertRaisesRegex(ValueError, "duplicate source IDs"):
            _paragraphs_from_response(
                {"paragraphs": [{"start_source_id": "s000001", "text": "完整内容。"}]},
                segments,
            )

    def test_malformed_model_field_types_are_rejected(self):
        segments = [Segment("s000001", 0, 1, "第一条内容。")]
        payloads = [
            {"paragraphs": [{"start_source_id": ["s000001"], "text": "第一条内容。"}]},
            {"paragraphs": [{"start_source_id": "s000001", "text": {"bad": True}}]},
            {
                "paragraphs": [
                    {"start_source_id": "s000001", "text": "第一条内容。", "heading": 1}
                ]
            },
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    _paragraphs_from_response(payload, segments)

    def test_string_null_heading_does_not_count_as_a_real_heading(self):
        segments = [Segment("s000001", 0, 1, "第一条内容。")]
        paragraphs, _ = _paragraphs_from_response(
            {
                "paragraphs": [
                    {"start_source_id": "s000001", "text": "第一条内容。", "heading": "null"}
                ]
            },
            segments,
        )
        self.assertIsNone(paragraphs[0].heading)

    def test_legacy_per_id_output_cannot_bypass_start_boundary_contract(self):
        segments = [Segment("s000001", 0, 1, "第一条具体内容。")]
        with self.assertRaisesRegex(ValueError, "start_source_id must be a string"):
            _paragraphs_from_response(
                {"paragraphs": [{"source_ids": ["s000001"], "text": "第一条具体内容。"}]},
                segments,
            )

    def test_nonempty_legacy_removal_list_is_rejected(self):
        segments = [Segment("s000001", 0, 1, "第一条具体内容。")]
        with self.assertRaisesRegex(ValueError, "removed_filler_ids is obsolete"):
            _paragraphs_from_response(
                {
                    "paragraphs": [
                        {"start_source_id": "s000001", "text": "第一条具体内容。"}
                    ],
                    "removed_filler_ids": ["s000001"],
                },
                segments,
            )

    def test_local_range_gate_rejects_detail_loss_hidden_by_other_long_paragraphs(self):
        segments = [
            Segment("s000001", 0, 2, "第一阶段包含必须保留的具体解释。" * 16),
            Segment("s000002", 2, 4, "第二阶段也包含必须保留的具体操作。" * 16),
        ]
        with self.assertRaisesRegex(ValueError, "local detail retention is too low"):
            _paragraphs_from_response(
                {
                    "paragraphs": [
                        {
                            "start_source_id": "s000001",
                            "text": "第一阶段包含必须保留的具体解释。" * 16,
                        },
                        {"start_source_id": "s000002", "text": "第二阶段。"},
                    ]
                },
                segments,
            )

    def test_task11_regression_model_boundaries_cannot_leave_source_ids_unaccounted(self):
        segments = [
            Segment(
                f"s{index:06d}",
                index * 2,
                (index + 1) * 2,
                f"步骤 {index} 包含一项具体操作和必要解释。",
            )
            for index in range(1, 13)
        ]
        response = {
            "paragraphs": [
                {
                    "start_source_id": "s000001",
                    "text": " ".join(segment.text for segment in segments[:4]),
                    "heading": "准备阶段",
                },
                {
                    "start_source_id": "s000005",
                    "text": " ".join(segment.text for segment in segments[4:8]),
                    "heading": "执行阶段",
                },
                {
                    "start_source_id": "s000009",
                    "text": " ".join(segment.text for segment in segments[8:]),
                    "heading": "检查阶段",
                },
            ]
        }
        paragraphs, coverage = edit_transcript(segments, FakeClient(response))
        self.assertEqual(coverage["missing_ids"], [])
        self.assertEqual(coverage["accounted_count"], len(segments))
        self.assertEqual(coverage["assignment_method"], "deterministic_start_ranges")
        self.assertEqual([item for p in paragraphs for item in p.source_ids], [s.id for s in segments])

    def test_system_prompt_assigns_content_to_ai_and_bookkeeping_to_cli(self):
        self.assertIn("长视频内容编辑", SYSTEM_PROMPT)
        self.assertIn("每个段落只填写 `start_source_id`", SYSTEM_PROMPT)
        self.assertIn("程序会自动", SYSTEM_PROMPT)
        self.assertIn("不要输出 `source_ids`", SYSTEM_PROMPT)
        self.assertIn("previous_context", FAITHFUL_SYSTEM_PROMPT)
        self.assertIn("忠实初稿", REFINEMENT_SYSTEM_PROMPT)

    def test_chunk_context_is_read_only_neighbouring_evidence(self):
        segments = [
            Segment(f"s{index:06d}", index, index + 1, f"第 {index} 条字幕。")
            for index in range(1, 10)
        ]
        previous, subsequent = _chunk_context(segments, segments[3:6], window=2)
        self.assertIn("s000002", previous)
        self.assertIn("s000003", previous)
        self.assertNotIn("s000004", previous)
        self.assertIn("s000007", subsequent)
        self.assertIn("s000008", subsequent)
        self.assertNotIn("s000006", subsequent)

    def test_semantic_checkpoint_requires_matching_source_signature(self):
        segments = [
            Segment("s000001", 0, 2, "保留完整设置步骤。"),
            Segment("s000002", 2, 4, "保留检查方法和失败条件。"),
        ]
        chunks = chunk_segments(segments)
        paragraph = Paragraph(
            ["s000001", "s000002"],
            "保留完整设置步骤，以及检查方法和失败条件。",
            0,
            4,
            heading="设置与检查",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            _save_checkpoint_chunks(path, segments, "原上下文", {1: [paragraph]})
            restored = _load_checkpoint_chunks(path, segments, chunks, "原上下文")
            self.assertEqual(restored[1][0].source_ids, ["s000001", "s000002"])
            self.assertEqual(
                _load_checkpoint_chunks(path, segments, chunks, "不同上下文"),
                {},
            )

    def test_long_video_uses_faithful_refinement_and_whole_compose(self):
        segments = [
            Segment(
                f"s{index:06d}",
                index * 10,
                (index + 1) * 10,
                f"第{index}阶段需要保留入口、选项、原因、检查方法和失败条件。" * 4,
            )
            for index in range(1, 31)
        ]

        chunks = chunk_segments(segments)
        self.assertGreater(len(chunks), 1)

        def manuscript(scope: list[Segment], prefix: str) -> dict:
            return {
                "paragraphs": [
                    {
                        "start_source_id": scope[start].id,
                        "text": prefix + "".join(item.text for item in scope[start : start + 4]),
                        "heading": f"阶段 {start // 4 + 1}",
                    }
                    for start in range(0, len(scope), 4)
                ]
            }

        responses: list[dict] = []
        for chunk in chunks:
            responses.extend([manuscript(chunk, ""), manuscript(chunk, "整理后：")])
        responses.append(manuscript(segments, "全文编排："))
        client = SequenceClient(responses)
        paragraphs, coverage = edit_transcript(segments, client, context="操作教程")
        self.assertEqual(client.calls, len(chunks) * 2 + 2)
        self.assertEqual(
            coverage["editing_pipeline"],
            "faithful_then_refine_then_compose_then_bounded_proofread",
        )
        self.assertTrue(paragraphs[0].text.startswith("全文编排："))

    def test_single_batch_skips_redundant_whole_compose(self):
        segments = [
            Segment(
                f"s{index:06d}",
                index * 10,
                (index + 1) * 10,
                f"第{index}阶段需要保留入口、选项、原因、检查方法和失败条件。" * 4,
            )
            for index in range(1, 21)
        ]
        self.assertEqual(len(chunk_segments(segments)), 1)

        def manuscript(prefix: str) -> dict:
            return {
                "paragraphs": [
                    {
                        "start_source_id": segments[start].id,
                        "text": prefix + "".join(item.text for item in segments[start : start + 5]),
                        "heading": f"阶段 {start // 5 + 1}",
                    }
                    for start in range(0, 20, 5)
                ]
            }

        client = SequenceClient(
            [manuscript(""), manuscript("整理后："), manuscript("不应调用：")]
        )
        paragraphs, coverage = edit_transcript(segments, client, context="操作教程")
        self.assertEqual(client.calls, 3)
        self.assertEqual(coverage["quality_status"], "pass")
        self.assertTrue(paragraphs[0].text.startswith("整理后："))

    def test_optional_refinement_failure_keeps_valid_faithful_draft(self):
        segments = [
            Segment(
                f"s{index:06d}",
                index * 10,
                (index + 1) * 10,
                f"第{index}阶段需要保留入口、选项、原因、检查方法和失败条件。" * 4,
            )
            for index in range(1, 21)
        ]
        faithful = {
            "paragraphs": [
                {
                    "start_source_id": segments[start].id,
                    "text": "".join(item.text for item in segments[start : start + 5]),
                    "heading": f"阶段 {start // 5 + 1}",
                }
                for start in range(0, 20, 5)
            ]
        }
        invalid = {"paragraphs": []}
        client = SequenceClient([faithful, invalid, invalid, invalid, invalid, invalid])
        paragraphs, coverage = edit_transcript(segments, client, context="操作教程")
        self.assertEqual(coverage["quality_status"], "pass")
        self.assertEqual(len(paragraphs), 4)
        self.assertTrue(any("已保留" in warning for warning in coverage["warnings"]))

    def test_llm_retries_transient_transport_failure_without_leaking_response(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'

        client = OpenAICompatibleClient(
            api_key="secret", base_url="https://example.test", model="test",
            retry_attempts=3, retry_backoff=0,
        )
        with patch(
            "vtm_core.llm.urllib.request.urlopen",
            side_effect=[urllib.error.URLError("temporary"), Response()],
        ) as urlopen:
            self.assertEqual(client.chat([{"role": "user", "content": "hi"}]), "ok")
        self.assertEqual(urlopen.call_count, 2)

    def test_ocr_gibberish_filter_preserves_useful_chinese_and_code(self):
        self.assertTrue(ocr_text_is_usable("设置 API Key 并点击保存", 82))
        self.assertTrue(ocr_text_is_usable("python main.py --force", 76))
        self.assertFalse(ocr_text_is_usable("□□□◆◆�", 90))
        self.assertFalse(ocr_text_is_usable("abc", 90))

    def test_ai_cannot_add_editor_commentary_absent_from_the_source(self):
        segments = [Segment("s000001", 0, 2, "作者介绍了配置步骤和命令。")]
        client = FakeClient(
            {
                "paragraphs": [
                    {
                        "start_source_id": "s000001",
                        "text": "作者介绍了配置步骤和命令。这说明它适合所有人。",
                    }
                ]
            }
        )
        with self.assertRaisesRegex(RuntimeError, "质量门禁"):
            edit_transcript(segments, client)

    def test_speaker_owned_commentary_phrase_is_not_removed_by_the_gate(self):
        segments = [Segment("s000001", 0, 2, "作者说，这说明配置已经成功。")]
        client = FakeClient(
            {
                "paragraphs": [
                    {"start_source_id": "s000001", "text": "作者说，这说明配置已经成功。"}
                ]
            }
        )
        paragraphs, coverage = edit_transcript(segments, client)
        self.assertEqual(coverage["quality_status"], "pass")
        self.assertIn("这说明", paragraphs[0].text)

    def test_full_gate_detects_missing_duplicate_and_out_of_order_source_ids(self):
        segments = [
            Segment("s000001", 0, 1, "第一条。"),
            Segment("s000002", 1, 2, "第二条。"),
            Segment("s000003", 2, 3, "第三条。"),
        ]
        invalid = [
            [Paragraph(["s000001", "s000003"], "第一条。第三条。", 0, 3)],
            [Paragraph(["s000001", "s000002", "s000002", "s000003"], "完整内容。", 0, 3)],
            [Paragraph(["s000002", "s000001", "s000003"], "完整内容。", 0, 3)],
        ]
        for paragraphs in invalid:
            with self.subTest(source_ids=paragraphs[0].source_ids):
                with self.assertRaisesRegex(ValueError, "incomplete, duplicated, or out of order"):
                    _validate_full_manuscript(segments, paragraphs)

    def test_transition_prefix_cannot_hide_meaningful_operation(self):
        segments = [Segment("s000001", 0, 2, "好，接下来点击插件并允许网络访问。")]
        client = FakeClient({"paragraphs": []})
        with self.assertRaisesRegex(RuntimeError, "质量门禁"):
            edit_transcript(segments, client)

    def test_overcompressed_model_output_blocks_publication(self):
        source = "这里包含一个很长而且不能删除的具体解释和操作步骤。"
        segments = [Segment("s000001", 0, 2, source)]
        client = FakeClient(
            {"paragraphs": [{"start_source_id": "s000001", "text": "步骤。"}]}
        )
        with self.assertRaisesRegex(RuntimeError, "质量门禁"):
            edit_transcript(segments, client)

    def test_long_video_cannot_become_one_giant_paragraph(self):
        segments = [
            Segment(f"s{index:06d}", index * 10, (index + 1) * 10, "这是必须保留的详细步骤和解释。" * 8)
            for index in range(60)
        ]
        giant = "".join(segment.text for segment in segments)
        client = FakeClient({
            "paragraphs": [{"start_source_id": segments[0].id, "text": giant, "heading": None}],
        })
        with self.assertRaisesRegex(RuntimeError, "质量门禁"):
            edit_transcript(segments, client)

    def test_full_draft_is_sent_back_to_llm_for_structural_rework(self):
        segments = [
            Segment(f"s{index:06d}", index * 50, (index + 1) * 50, f"第 {index} 步包含必须保留的操作和解释。")
            for index in range(1, 5)
        ]
        first = {
            "paragraphs": [
                {"start_source_id": segment.id, "text": segment.text, "heading": None}
                for segment in segments
            ],
        }
        repaired = {
            "paragraphs": [
                {
                    "start_source_id": segment.id,
                    "text": segment.text,
                    "heading": f"操作阶段 {index}" if index <= 2 else None,
                }
                for index, segment in enumerate(segments, start=1)
            ],
        }
        client = SequenceClient([first, repaired])
        paragraphs, coverage = edit_transcript(segments, client, context="测试操作视频")
        self.assertEqual(client.calls, 3)
        self.assertEqual(coverage["quality_status"], "pass")
        self.assertGreaterEqual(sum(bool(item.heading) for item in paragraphs), 2)

    def test_full_draft_gets_two_rework_attempts(self):
        segments = [
            Segment(
                f"s{index:06d}",
                index * 50,
                (index + 1) * 50,
                f"第 {index} 步包含必须保留的操作和解释。",
            )
            for index in range(1, 5)
        ]
        unstructured = {
            "paragraphs": [
                {"start_source_id": segment.id, "text": segment.text, "heading": None}
                for segment in segments
            ],
        }
        repaired = {
            "paragraphs": [
                {
                    "start_source_id": segment.id,
                    "text": segment.text,
                    "heading": f"操作阶段 {index}" if index <= 2 else None,
                }
                for index, segment in enumerate(segments, start=1)
            ],
        }
        client = SequenceClient([unstructured, unstructured, repaired])
        paragraphs, coverage = edit_transcript(segments, client, context="测试操作视频")
        self.assertEqual(client.calls, 2 + FULL_MANUSCRIPT_REPAIR_ATTEMPTS)
        self.assertEqual(coverage["quality_status"], "pass")
        self.assertGreaterEqual(sum(bool(item.heading) for item in paragraphs), 2)

    def test_reported_v5_raw_asr_dump_fails_release_gate(self):
        source = [
            Segment(
                "s000001",
                0,
                160,
                "今天手把手教同学们使用 Codex 查找论文参考文献。" * 80,
            )
        ]
        bad = [
            Paragraph(
                ["s000001"],
                "goodex 连接网络后一个个文文真毫毫地运行。" * 80,
                0,
                160,
                heading=None,
            )
        ]
        with self.assertRaisesRegex(ValueError, "paragraph longer than 700"):
            _validate_full_manuscript(source, bad)

    def test_irrelevant_nearby_screen_is_not_inserted(self):
        paragraphs = [Paragraph(["s1"], "UP 主正在介绍 Codex 的设置。", 0, 4)]
        frames = [Frame(
            2,
            "/tmp/unrelated.png",
            ["s1"],
            ocr_text="PubMed Save Email Send to",
            ocr_confidence=90,
        )]
        client = FakeClient({
            "relevance": "low",
            "content_kind": "ui",
            "information_gain": "none",
            "publish_mode": "drop",
            "replacement_markdown": None,
            "display_note": None,
            "confidence": "high",
            "completeness": "complete",
            "information_density": "low",
        })
        enrich_with_visual_evidence(paragraphs, frames, client)
        self.assertIsNone(paragraphs[0].visual_note)
        self.assertEqual(frames[0].paragraph_index, 0)
        self.assertFalse(frames[0].keep_image)

    def test_decorative_programmer_cartoon_is_discarded(self):
        paragraphs = [Paragraph(["s1"], "有人提交新代码后，旧索引会与实际代码漂移。", 0, 4)]
        frames = [Frame(
            2,
            "/tmp/programmer-cartoon.png",
            ["s1"],
            ocr_text="提交新代码 有人新提交了代码",
            ocr_confidence=91,
            vision_description="程序员敲键盘的卡通插画，旁边重复显示提交新代码。",
        )]
        client = FakeClient({
            "relevance": "low",
            "content_kind": "decorative",
            "information_gain": "none",
            "publish_mode": "drop",
            "replacement_markdown": None,
            "display_note": None,
            "confidence": "high",
            "completeness": "complete",
            "information_density": "low",
        })
        warnings = enrich_with_visual_evidence(paragraphs, frames, client)
        self.assertEqual(warnings, [])
        self.assertFalse(frames[0].keep_image)
        self.assertEqual(frames[0].content_kind, "decorative")
        self.assertEqual(frames[0].information_gain, "none")
        self.assertIsNone(paragraphs[0].visual_note)

    def test_zero_information_gain_simple_arrow_is_discarded(self):
        paragraphs = [Paragraph(["s1"], "RAG 方案会生成 embedding，存在信息泄露风险。", 0, 4)]
        frames = [Frame(
            2,
            "/tmp/simple-arrow.png",
            ["s1"],
            ocr_text="RAG 方案 → embedding 信息泄露风险",
            ocr_confidence=93,
        )]
        client = FakeClient({
            "relevance": "high",
            "content_kind": "comparison",
            "information_gain": "none",
            "publish_mode": "drop",
            "replacement_markdown": None,
            "display_note": None,
            "confidence": "high",
            "completeness": "complete",
            "information_density": "low",
        })
        enrich_with_visual_evidence(paragraphs, frames, client)
        self.assertFalse(frames[0].keep_image)
        self.assertIsNone(paragraphs[0].visual_note)

    def test_verbose_vision_description_does_not_make_simple_text_dense(self):
        paragraphs = [Paragraph(["s1"], "作者展示了 Claude Code 与 Cursor 的取舍。", 0, 4)]
        frames = [Frame(
            2,
            "/tmp/simple-comparison.png",
            ["s1"],
            ocr_text="Claude Code → 极简；Cursor → 向量索引",
            ocr_confidence=93,
            vision_description="视觉模型对简单画面给出了很长的逐字说明。" * 40,
        )]
        client = FakeClient({
            "relevance": "high",
            "content_kind": "text",
            "information_gain": "partial",
            "publish_mode": "note_only",
            "replacement_markdown": "Claude Code → 极简；Cursor → 向量索引。",
            "display_note": None,
            "confidence": "high",
            "completeness": "complete",
            "information_density": "low",
        })
        enrich_with_visual_evidence(paragraphs, frames, client)
        self.assertFalse(frames[0].keep_image)
        self.assertEqual(frames[0].publish_mode, "note_only")
        self.assertIn("Cursor", frames[0].replacement_markdown)
        self.assertIsNone(paragraphs[0].visual_note)

    def test_talking_head_text_overlay_becomes_text_without_portrait_or_subtitle(self):
        paragraph = Paragraph(
            ["s1"],
            "作者建议把明确的要求交给模型执行。",
            30,
            45,
            heading="四个象限详解",
            subheading="象限一：我知道，AI也知道",
        )
        frozen = (
            paragraph.text,
            paragraph.heading,
            paragraph.subheading,
            list(paragraph.source_ids),
        )
        portrait = Frame(
            37,
            "/tmp/talking-head.png",
            ["s1"],
            ocr_text="1. 共识区 让 AI 执行",
            ocr_confidence=91,
            vision_description=(
                "画面为短视频平台视频截图，人物口述，含叠加文字与字幕。"
                "顶部标题为 1. 共识区 让 AI 执行。"
                "无代码、公式、表格、图表、流程图或结构图。"
            ),
        )
        client = FakeClient({
            "relevance": "high",
            "content_kind": "text",
            "information_gain": "partial",
            "publish_mode": "image_with_note",
            "replacement_markdown": None,
            "display_note": "标题显示“1. 共识区 让 AI 执行”；字幕为“全部都跟他说”。",
            "confidence": "high",
            "completeness": "complete",
            "information_density": "low",
        })

        self.assertEqual(enrich_with_visual_evidence([paragraph], [portrait], client), [])
        self.assertEqual(portrait.publish_mode, "note_only")
        self.assertFalse(portrait.keep_image)
        self.assertEqual(portrait.replacement_markdown, "1. 共识区 让 AI 执行")
        self.assertNotIn("字幕", portrait.replacement_markdown)
        self.assertNotIn("全部都跟他说", portrait.replacement_markdown)
        self.assertEqual(
            (
                paragraph.text,
                paragraph.heading,
                paragraph.subheading,
                paragraph.source_ids,
            ),
            frozen,
        )

    def test_talking_head_repeated_overlay_drops_but_diagram_remains_image(self):
        paragraph = Paragraph(
            ["s1"],
            "本节标题已经写明：共识区让 AI 执行。",
            20,
            45,
            heading="四个象限详解",
            subheading="象限一：共识区",
        )
        portrait = Frame(
            37,
            "/tmp/talking-head.png",
            ["s1"],
            ocr_text="1. 共识区 让 AI 执行",
            ocr_confidence=93,
            vision_description=(
                "画面为纯文字加人物实拍的视频帧，顶部标题和字幕叠加在人物画面。"
                "未出现流程图、结构图、图表、表格、代码或公式。"
            ),
        )
        diagram = Frame(
            28,
            "/tmp/quadrants.png",
            ["s1"],
            ocr_text="我知道 AI知道 共识区 隐藏区 盲区 未知区",
            ocr_confidence=95,
            vision_description="四象限结构图，坐标轴和四个区域关系完整可见。",
        )
        client = FakeClient({"items": [
            {
                "item_id": "v001",
                "relevance": "high",
                "content_kind": "text",
                "information_gain": "partial",
                "publish_mode": "image_with_note",
                "replacement_markdown": None,
                "display_note": "标题显示“1. 共识区 让 AI 执行”。",
                "confidence": "high",
                "completeness": "complete",
                "information_density": "low",
            },
            {
                "item_id": "v002",
                "relevance": "high",
                "content_kind": "diagram",
                "information_gain": "substantial",
                "publish_mode": "image_only",
                "replacement_markdown": None,
                "display_note": None,
                "confidence": "high",
                "completeness": "complete",
                "information_density": "high",
            },
        ]})

        self.assertEqual(enrich_with_visual_evidence([paragraph], [portrait, diagram], client), [])
        self.assertEqual(portrait.publish_mode, "drop")
        self.assertFalse(portrait.keep_image)
        self.assertEqual(diagram.publish_mode, "image_only")
        self.assertTrue(diagram.keep_image)

    def test_short_transcribed_text_slide_does_not_keep_image_for_cautious_partial_label(self):
        paragraphs = [Paragraph(["s1"], "精确搜索不存在语义漂移。", 0, 4)]
        frames = [Frame(
            2,
            "/tmp/polarity-slide.png",
            ["s1"],
            vision_description="画面写着不存在语义漂移的问题。",
        )]
        client = FakeClient({
            "relevance": "high",
            "content_kind": "text",
            "information_gain": "partial",
            "publish_mode": "note_only",
            "replacement_markdown": "不存在语义漂移的问题。",
            "display_note": None,
            "confidence": "high",
            "completeness": "partial",
            "information_density": "low",
        })
        enrich_with_visual_evidence(paragraphs, frames, client)
        self.assertTrue(frames[0].keep_image)
        self.assertEqual(frames[0].publish_mode, "image_only")
        self.assertIsNone(paragraphs[0].visual_note)

    def test_relevant_screen_without_complete_transcription_keeps_original_image(self):
        paragraphs = [Paragraph(["s1"], "UP 主正在展示配置界面。", 0, 4)]
        frames = [
            Frame(
                2,
                "/tmp/config.png",
                ["s1"],
                ocr_text="API Key Advanced Settings",
                ocr_confidence=90,
            )
        ]
        client = FakeClient(
            {
                "relevance": "high",
                "content_kind": "ui",
                "information_gain": "substantial",
                "publish_mode": "image_only",
                "replacement_markdown": None,
                "display_note": None,
                "confidence": "medium",
                "completeness": "partial",
                "information_density": "high",
            }
        )
        enrich_with_visual_evidence(paragraphs, frames, client)
        self.assertEqual(frames[0].paragraph_index, 0)
        self.assertTrue(frames[0].keep_image)
        self.assertEqual(plan_frame_evidence(paragraphs, frames)[0], frames[0])

    def test_visual_classification_error_keeps_the_aligned_original(self):
        class BrokenClient:
            def chat(self, messages, **kwargs):
                raise RuntimeError("temporary model failure")

        paragraphs = [Paragraph(["s1"], "UP 主正在展示流程图。", 0, 4)]
        frames = [
            Frame(
                2,
                "/tmp/diagram.png",
                ["s1"],
                vision_description="画面是一张多节点流程图。",
            )
        ]
        warnings = enrich_with_visual_evidence(paragraphs, frames, BrokenClient())
        self.assertEqual(frames[0].paragraph_index, 0)
        self.assertTrue(frames[0].keep_image)
        self.assertTrue(warnings)

    def test_visual_evidence_for_multiple_paragraphs_uses_one_text_model_call(self):
        class BatchClient:
            def __init__(self):
                self.calls = 0

            def chat(self, messages, **kwargs):
                self.calls += 1
                request = json.loads(messages[-1]["content"])
                return json.dumps(
                    {
                        "items": [
                            {
                                "item_id": item["item_id"],
                                "relevance": "high",
                                "content_kind": "ui",
                                "information_gain": "substantial",
                                "publish_mode": "image_only",
                                "replacement_markdown": None,
                                "display_note": None,
                                "confidence": "high",
                                "completeness": "partial",
                                "information_density": "high",
                            }
                            for item in request["items"]
                        ]
                    },
                    ensure_ascii=False,
                )

        paragraphs = [
            Paragraph(["s1"], "第一处操作。", 0, 4),
            Paragraph(["s2"], "第二处操作。", 5, 9),
        ]
        frames = [
            Frame(2, "/tmp/one.png", ["s1"], ocr_text="设置界面第一处包含完整选项名称", ocr_confidence=90),
            Frame(7, "/tmp/two.png", ["s2"], ocr_text="设置界面第二处包含完整参数名称", ocr_confidence=90),
        ]
        client = BatchClient()
        warnings = enrich_with_visual_evidence(paragraphs, frames, client)
        self.assertEqual(warnings, [])
        self.assertEqual(client.calls, 1)
        self.assertEqual([frame.paragraph_index for frame in frames], [0, 1])
        self.assertTrue(all(frame.keep_image for frame in frames))

    def test_exact_episode_identifier_cannot_be_genericized_away(self):
        segments = [Segment("s000001", 0, 2, "推荐 E238 Harness 这一期。")]
        client = FakeClient(
            {"paragraphs": [{"start_source_id": "s000001", "text": "推荐这一期。"}]}
        )
        with self.assertRaisesRegex(RuntimeError, "质量门禁"):
            edit_transcript(segments, client)

    def test_arbitrary_titles_commands_and_person_names_cannot_be_genericized(self):
        segments = [
            Segment(
                "s000001",
                0,
                4,
                "推荐《Harness 时代 AI-First 的组织架构》，对话张咋啦这一期，"
                "配置 Claude.md 后执行 --force。",
            )
        ]
        client = FakeClient(
            {
                "paragraphs": [
                    {
                        "start_source_id": "s000001",
                        "text": "推荐一期节目，并配置文件后执行命令。",
                    }
                ],
            }
        )
        with self.assertRaisesRegex(RuntimeError, "质量门禁"):
            edit_transcript(segments, client)

    def test_known_asr_product_alias_can_be_corrected_without_conflicting_gates(self):
        segments = [Segment("s000001", 0, 2, "使用 goodex 生成论文初稿。")]
        client = FakeClient(
            {
                "paragraphs": [
                    {"start_source_id": "s000001", "text": "使用 Codex 生成论文初稿。"}
                ]
            }
        )
        paragraphs, coverage = edit_transcript(segments, client)
        self.assertEqual(coverage["quality_status"], "pass")
        self.assertIn("Codex", paragraphs[0].text)

    def test_plain_lowercase_english_ui_word_is_not_an_exact_anchor(self):
        segments = [Segment("s000001", 0, 2, "点击 open 打开文件，然后继续操作。")]
        self.assertNotIn("open", exact_anchors(segments))
        client = FakeClient(
            {
                "paragraphs": [
                    {
                        "start_source_id": "s000001",
                        "text": "点击“打开”进入文件，然后继续操作。",
                    }
                ]
            }
        )
        paragraphs, coverage = edit_transcript(segments, client)
        self.assertEqual(coverage["quality_status"], "pass")
        self.assertNotIn("open", paragraphs[0].text.lower())

    def test_unknown_two_letter_asr_fragment_is_not_an_exact_anchor(self):
        segments = [Segment("s000001", 0, 2, "然后 SR 再继续生成论文。")]
        self.assertNotIn("SR", exact_anchors(segments))
        client = FakeClient(
            {
                "paragraphs": [
                    {
                        "start_source_id": "s000001",
                        "text": "然后继续生成论文。",
                    }
                ]
            }
        )
        paragraphs, coverage = edit_transcript(segments, client)
        self.assertEqual(coverage["quality_status"], "pass")
        self.assertNotIn("SR", paragraphs[0].text)

    def test_known_two_letter_technical_acronyms_remain_exact_anchors(self):
        segments = [
            Segment(
                "s000001",
                0,
                2,
                "使用 AI、ML、UI、UX、VR、AR、PE 和 PM 完成这一步。",
            )
        ]
        anchors = exact_anchors(segments)
        for expected in ("AI", "ML", "UI", "UX", "VR", "AR", "PE", "PM"):
            with self.subTest(expected=expected):
                self.assertIn(expected, anchors)

    def test_structured_and_domain_english_anchors_remain_enforced(self):
        segments = [
            Segment(
                "s000001",
                0,
                3,
                "使用 OpenAI、FDE、Claude.md、--force、E238 和 20 分钟作为具体信息。",
            )
        ]
        anchors = exact_anchors(segments)
        for expected in ("OpenAI", "FDE", "Claude.md", "--force", "E238", "20 分钟"):
            with self.subTest(expected=expected):
                self.assertIn(expected, anchors)

    def test_ocr_evidence_is_separate_from_spoken_text(self):
        paragraphs = [Paragraph(["s1"], "UP 主只说推荐这期。", 0, 4)]
        frames = [Frame(2, "/tmp/frame.png", ["s1"], ocr_text="E238 Harness 时代 AI-First", ocr_confidence=90)]
        client = FakeClient({
            "relevance": "high",
            "content_kind": "text",
            "information_gain": "partial",
            "publish_mode": "note_only",
            "replacement_markdown": "完整标题：E238 Harness 时代 AI-First。",
            "display_note": None,
            "confidence": "high",
            "completeness": "complete",
            "information_density": "low",
        })
        warnings = enrich_with_visual_evidence(paragraphs, frames, client)
        self.assertEqual(warnings, [])
        self.assertEqual(paragraphs[0].text, "UP 主只说推荐这期。")
        self.assertIn("E238", frames[0].replacement_markdown)
        self.assertIsNone(paragraphs[0].visual_note)
        self.assertFalse(frames[0].keep_image)

    def test_complete_low_density_text_deterministically_replaces_image(self):
        paragraphs = [Paragraph(["s1"], "作者展示了一行配置。", 0, 4)]
        frames = [Frame(
            2,
            "/tmp/simple-text.png",
            ["s1"],
            ocr_text="VTM_FINAL_VISUAL_HEIGHT=1080",
            ocr_confidence=92,
            vision_description="画面完整显示一行配置：VTM_FINAL_VISUAL_HEIGHT=1080。",
        )]
        client = FakeClient({
            "relevance": "high",
            "content_kind": "text",
            "information_gain": "partial",
            "publish_mode": "note_only",
            "replacement_markdown": "`VTM_FINAL_VISUAL_HEIGHT=1080`",
            "display_note": None,
            "confidence": "high",
            "completeness": "complete",
            "information_density": "low",
        })
        enrich_with_visual_evidence(paragraphs, frames, client)
        self.assertFalse(frames[0].keep_image)
        self.assertIn("1080", frames[0].replacement_markdown)
        self.assertIsNone(paragraphs[0].visual_note)

    def test_redundant_complete_simple_text_without_unique_note_removes_image(self):
        paragraphs = [Paragraph(["s1"], "课程共四个多小时，配有近 200 页讲义。", 0, 4)]
        frames = [Frame(
            2,
            "/tmp/course-duration.png",
            ["s1"],
            ocr_text="课程 4 小时 近 200 页讲义",
            ocr_confidence=87,
            vision_description="画面完整显示课程时长和讲义页数，与口述一致。",
        )]
        client = FakeClient({
            "relevance": "high",
            "content_kind": "text",
            "information_gain": "none",
            "publish_mode": "drop",
            "replacement_markdown": None,
            "display_note": None,
            "confidence": "high",
            "completeness": "complete",
            "information_density": "low",
        })
        enrich_with_visual_evidence(paragraphs, frames, client)
        self.assertFalse(frames[0].keep_image)
        self.assertIsNone(paragraphs[0].visual_note)

    def test_partial_or_dense_screen_cannot_be_deleted(self):
        paragraphs = [Paragraph(["s1"], "UP 主介绍推荐节目。", 0, 4)]
        frames = [Frame(
            2,
            "/tmp/dense.png",
            ["s1"],
            ocr_text="完整提示词 " * 80,
            vision_description="这是一个包含多段配置的长提示词界面。",
        )]
        client = FakeClient({
            "relevance": "high",
            "content_kind": "text",
            "information_gain": "substantial",
            "publish_mode": "image_only",
            "replacement_markdown": None,
            "display_note": None,
            "confidence": "high",
            "completeness": "partial",
            "information_density": "high",
        })
        enrich_with_visual_evidence(paragraphs, frames, client)
        self.assertTrue(frames[0].keep_image)
        self.assertEqual(frames[0].evidence_completeness, "partial")
        self.assertIsNone(paragraphs[0].visual_note)

    def test_medium_confidence_visual_text_is_not_published_but_image_is_kept(self):
        paragraphs = [Paragraph(["s1"], "UP 主展示操作界面。", 0, 4)]
        frames = [Frame(
            2,
            "/tmp/dense.png",
            ["s1"],
            ocr_text="疑似识别出的长提示词",
            vision_description="可能包含不可完全确认的按钮和参数。",
        )]
        client = FakeClient({
            "relevance": "high",
            "content_kind": "ui",
            "information_gain": "partial",
            "publish_mode": "image_only",
            "replacement_markdown": None,
            "display_note": None,
            "confidence": "medium",
            "completeness": "partial",
            "information_density": "high",
        })
        enrich_with_visual_evidence(paragraphs, frames, client)
        self.assertTrue(frames[0].keep_image)
        self.assertIsNone(paragraphs[0].visual_note)
        self.assertEqual(frames[0].extracted_markdown, "")

    def test_nearby_similar_aligned_frames_keep_only_stronger_evidence(self):
        paragraphs = [
            Paragraph(["s1"], "第一段。", 78, 81),
            Paragraph(["s2"], "第二段。", 81, 84),
        ]
        weaker = Frame(
            80.4,
            "/tmp/weak.png",
            ["s1"],
            ahash="0" * 64,
            paragraph_index=0,
            evidence_confidence="medium",
            evidence_completeness="partial",
            ocr_confidence=18.5,
        )
        paragraphs[0].visual_note = "较弱截图的识别文字"
        weaker.extracted_markdown = "较弱截图的识别文字"
        stronger = Frame(
            81.8,
            "/tmp/strong.png",
            ["s2"],
            ahash="0" * 63 + "1",
            paragraph_index=1,
            evidence_confidence="medium",
            evidence_completeness="partial",
            ocr_confidence=19.5,
        )
        plan = plan_frame_evidence(paragraphs, [weaker, stronger])
        self.assertEqual(list(plan), [1])
        self.assertIs(plan[1], stronger)
        self.assertEqual(paragraphs[0].visual_note, "较弱截图的识别文字")
        self.assertEqual(weaker.extracted_markdown, "")

    def test_common_asr_product_and_csv_reversals_are_normalized(self):
        paragraphs, _ = _paragraphs_from_response(
            {
                "paragraphs": [
                    {
                        "start_source_id": "s1",
                        "text": "用 oodex 生成文献，并在 cvs 中打开，再导入 sy paper。",
                        "heading": None,
                    }
                ]
            },
            [Segment("s1", 0, 2, "用Codex生成文献，在CSV中打开，导入SY Paper。")],
            enforce_structure=False,
        )
        self.assertIn("Codex", paragraphs[0].text)
        self.assertIn("CSV", paragraphs[0].text)
        self.assertIn("SY Paper", paragraphs[0].text)
        self.assertNotIn("cvs", paragraphs[0].text.lower())

    def test_bounded_asr_proofread_applies_unique_conservative_correction(self):
        segments = [
            Segment("s1", 0, 3, "上传真实参考文献和三一大纲政策后生成初稿。"),
        ]
        paragraphs = [
            Paragraph(["s1"], "上传真实参考文献和三一大纲政策后生成初稿。", 0, 3)
        ]
        client = FakeClient({
            "corrections": [
                {
                    "original": "三一大纲政策",
                    "replacement": "已经确定的大纲",
                }
            ]
        })
        repaired, warnings = _proofread_asr_artifacts(segments, paragraphs, client)
        self.assertIn("已经确定的大纲", repaired[0].text)
        self.assertTrue(any("应用 1 处" in warning for warning in warnings))

    def test_bounded_asr_proofread_rejects_new_exact_number(self):
        segments = [Segment("s1", 0, 3, "平台自带大型文献库。")]
        paragraphs = [Paragraph(["s1"], "平台自带大型文献库。", 0, 3)]
        client = FakeClient({
            "corrections": [
                {"original": "大型文献库", "replacement": "7 亿篇文献库"}
            ]
        })
        repaired, _warnings = _proofread_asr_artifacts(segments, paragraphs, client)
        self.assertEqual(repaired[0].text, "平台自带大型文献库。")

    def test_bounded_asr_proofread_dequantifies_invented_chinese_count(self):
        segments = [Segment("s1", 0, 3, "平台自带超期一篇文献的数据库。")]
        paragraphs = [Paragraph(["s1"], "平台自带超七篇文献的数据库。", 0, 3)]
        client = FakeClient({
            "corrections": [
                {"original": "自带超七篇文献", "replacement": "自带超七千篇文献"}
            ]
        })
        repaired, _warnings = _proofread_asr_artifacts(segments, paragraphs, client)
        self.assertEqual(repaired[0].text, "平台自带大量文献的数据库。")

    def test_bounded_asr_proofread_dequantifies_unflagged_malformed_count(self):
        segments = [Segment("s1", 0, 3, "平台自带超期一篇文献。")]
        paragraphs = [Paragraph(["s1"], "平台自带超七篇文献。", 0, 3)]
        repaired, warnings = _proofread_asr_artifacts(
            segments,
            paragraphs,
            FakeClient({"corrections": []}),
        )
        self.assertEqual(repaired[0].text, "平台自带大量文献。")
        self.assertTrue(any("不可靠数量去量化 1 处" in item for item in warnings))

    def test_bounded_asr_proofread_repairs_unflagged_outline_gibberish(self):
        segments = [Segment("s1", 0, 3, "参考文献和三一大纲政策论文初稿完成。")]
        paragraphs = [
            Paragraph(["s1"], "参考文献和三大纲政策论文初稿完成。", 0, 3)
        ]
        repaired, warnings = _proofread_asr_artifacts(
            segments,
            paragraphs,
            FakeClient({"corrections": []}),
        )
        self.assertEqual(repaired[0].text, "参考文献和大纲生成的论文初稿完成。")
        self.assertTrue(any("不成句大纲短语" in item for item in warnings))

    def test_retained_frames_are_replaced_from_1080_stream_by_timestamp(self):
        info = VideoInfo(
            url="https://www.bilibili.com/video/BV1ab411c7DE",
            bvid="BV1ab411c7DE",
            cid=42,
            part=1,
            title="demo",
            part_title="demo",
            duration=10,
            owner="",
            cover="",
        )
        class Client:
            def media_stream(self, inspected, *, audio_only, max_height):
                self.requested = max_height
                return {
                    "url": "https://v.bilivideo.com/1080.m4s",
                    "height": 1080,
                }

        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "frame.png"
            image.write_bytes(b"analysis")
            frame = Frame(12.345, str(image), ["s1"], keep_image=True)
            commands = []
            def fake_run(command, **kwargs):
                commands.append(command)
                Path(command[-1]).write_bytes(b"high-resolution")
            client = Client()
            with patch("vtm_core.visual.require_command", return_value="ffmpeg"), patch(
                "vtm_core.visual._run", side_effect=fake_run
            ):
                result = recapture_retained_frames(client, info, [frame], max_height=1080)
            self.assertEqual(image.read_bytes(), b"high-resolution")
            self.assertEqual(frame.final_height, 1080)
            self.assertEqual(result["upgraded_count"], 1)
            self.assertIn("12.345", commands[0])

    def test_visual_asset_filename_uses_only_task_sequence_and_timestamp(self):
        name = asset_filename("20260717-3 中文标题", 1, 122.4)
        self.assertEqual(name, "20260717-3-001-02m02s.png")
        self.assertTrue(name.isascii())

    def test_missing_captured_candidate_is_skipped_instead_of_failing_task(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "vtm_core.visual.probe_duration", return_value=30.0
        ), patch(
            "vtm_core.visual.calibrate_threshold", return_value=0.3
        ), patch(
            "vtm_core.visual.detect_scenes", return_value=[]
        ), patch(
            "vtm_core.visual.candidate_times", return_value=[(2.0, 1.0)]
        ), patch(
            "vtm_core.visual.raw_gray_frame", return_value=bytes(range(256)) * 4
        ), patch(
            "vtm_core.visual.average_hash", return_value=0
        ), patch(
            "vtm_core.visual.quality", return_value=(0.5, 0.5)
        ), patch(
            "vtm_core.visual.capture_frame", return_value=None
        ):
            frames, metadata = extract_useful_frames(
                Path(temp) / "video.mp4",
                Path(temp),
                [Segment("s1", 0, 4, "这里展示一个画面。")],
                max_frames=6,
                task_key="20260717-3",
            )
        self.assertEqual(frames, [])
        self.assertEqual(metadata["candidate_count"], 0)

    def test_configured_vision_model_describes_visible_text(self):
        class Vision:
            def chat(self, messages, **kwargs):
                return "画面显示节目标题 E238。"

        with tempfile.TemporaryDirectory() as temp, patch(
            "vtm_core.visual.vision_client", return_value=Vision()
        ):
            image = Path(temp) / "frame.png"
            image.write_bytes(b"png")
            result = describe_if_needed(image, "附近字幕", "E238")
            self.assertIn("E238", result)

    def test_giant_asr_segment_is_resegmented(self):
        text = "第一句有具体内容。第二句保留解释。第三句包含操作步骤。" * 8
        repaired = normalize_segments([Segment("s1", 0, 120, text)], 120)
        self.assertGreater(len(repaired), 4)
        self.assertLess(max(item.end - item.start for item in repaired), 45)

    def test_synthetic_regression_cannot_remain_one_five_minute_segment(self):
        fixture = Path(__file__).parent / "fixtures" / "synthetic-bad-raw-transcript.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        segments = [Segment(**item) for item in payload["segments"]]
        repaired = normalize_segments(segments, 302.23)
        self.assertGreater(len(repaired), 20)
        self.assertLess(max(item.end - item.start for item in repaired), 35)

    def test_hash_and_quality(self):
        dark = bytes([0] * 1024)
        split = bytes([0] * 512 + [255] * 512)
        self.assertEqual(hash_distance(average_hash(dark), average_hash(dark)), 0)
        self.assertGreater(hash_distance(average_hash(dark), average_hash(split)), 0.4)
        brightness, contrast = quality(split)
        self.assertAlmostEqual(brightness, 0.5, places=2)
        self.assertGreater(contrast, 0.9)

    def test_requested_progressive_evidence_keeps_small_same_template_changes(self):
        progressive_change = 0.06
        for kind in (
            "text", "list", "table", "code", "formula", "diagram",
            "chart", "process", "ui", "paper_figure", "comparison",
        ):
            requests = [{
                "time_start": 90,
                "time_end": 100,
                "purpose": "核验逐步补充后的完整证据",
                "expected_kind": kind,
            }]
            threshold = duplicate_distance_threshold(95, requests)
            self.assertEqual(threshold, 0.045)
            self.assertGreater(progressive_change, threshold)
            self.assertEqual(duplicate_distance_threshold(120, requests), 0.10)

    def test_vision_budget_is_temporally_distributed_and_uses_best_in_each_region(self):
        self.assertEqual(DEFAULT_VISION_FRAME_BUDGET, 6)
        self.assertEqual(MAX_ADAPTIVE_VISION_FRAME_BUDGET, 60)
        self.assertEqual(adaptive_vision_frame_budget(6 * 60, 18), 6)
        self.assertEqual(adaptive_vision_frame_budget(20 * 60, 30), 20)
        self.assertEqual(adaptive_vision_frame_budget(60 * 60, 30), 30)
        self.assertEqual(adaptive_vision_frame_budget(60 * 60, 8), 8)
        early = Frame(1, "/tmp/early.png")
        useful = Frame(20, "/tmp/useful.png")
        clustered = Frame(30, "/tmp/clustered.png")
        later = Frame(90, "/tmp/later.png")
        chosen = vision_priority_ids(
            [(early, 1.0), (useful, 9.0), (clustered, 8.0), (later, 4.0)],
            2,
            duration=120,
        )
        self.assertNotIn(id(early), chosen)
        self.assertIn(id(useful), chosen)
        self.assertIn(id(later), chosen)
        self.assertNotIn(id(clustered), chosen)

    def test_semantic_vision_budget_follows_requested_distinct_slides_not_duration(self):
        selected = [
            (Frame(timestamp, f"/tmp/{index}.png"), 5.0)
            for index, timestamp in enumerate((10, 12, 14, 16, 18, 20, 200), start=1)
        ]
        requests = [
            {
                "time_start": 9,
                "time_end": 21,
                "purpose": "逐页读取研究方法 PPT",
                "expected_kind": "text",
            }
        ]
        with patch.dict(os.environ, {"VTM_MAX_VISION_FRAMES": "60"}):
            self.assertEqual(
                semantic_vision_frame_budget(selected, requests, max_frames=60),
                6,
            )
        chosen = vision_priority_ids_for_requests(
            selected,
            budget=6,
            duration=240,
            visual_requests=requests,
        )
        self.assertEqual(chosen, {id(frame) for frame, _score in selected[:6]})

    def test_semantic_vision_budget_has_dynamic_cost_cap(self):
        self.assertEqual(MAX_PAID_VISION_REVIEWS_PER_MINUTE, 2)
        selected = [
            (Frame(float(index * 20), f"/tmp/{index}.png"), 5.0)
            for index in range(1, 61)
        ]
        requests = [{
            "time_start": 0,
            "time_end": 1200,
            "purpose": "检查 20 分钟技术课程的不同 PPT",
            "expected_kind": "text",
        }]
        self.assertEqual(
            semantic_vision_frame_budget(
                selected, requests, max_frames=60, duration=1200
            ),
            40,
        )

    def test_candidate_times_keeps_multiple_scene_changes_in_one_requested_range(self):
        times = candidate_times(
            scenes=[10, 12, 14, 16],
            segments=[Segment("s1", 9, 17, "这里依次展示四页 PPT")],
            duration=60,
            max_frames=20,
            visual_requests=[{
                "time_start": 9,
                "time_end": 17,
                "purpose": "读取所有不同幻灯片",
                "expected_kind": "text",
            }],
        )
        requested_scene_times = [at for at, score in times if score >= 7]
        self.assertGreaterEqual(len(requested_scene_times), 4)

    def test_multiple_distinct_complex_frames_can_follow_one_paragraph(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            first = assets / "architecture.png"
            second = assets / "flowchart.png"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            paragraphs = [Paragraph(["s1"], "这一段依次解释架构图和流程图。", 0, 20)]
            frames = [
                Frame(5, str(first), ["s1"], paragraph_index=0, content_kind="diagram", ahash="0" * 64),
                Frame(15, str(second), ["s1"], paragraph_index=0, content_kind="process", ahash="f" * 64),
            ]
            groups = plan_frame_evidence_groups(paragraphs, frames)
            self.assertEqual(groups[0], frames)
            note = root / "note.md"
            compose_markdown(
                note,
                {"title": "多图", "url": "https://example.invalid", "part": 1},
                paragraphs,
                frames,
            )
            rendered = note.read_text(encoding="utf-8")
            self.assertIn("architecture.png", rendered)
            self.assertIn("flowchart.png", rendered)
            self.assertLess(rendered.index("架构图和流程图"), rendered.index("architecture.png"))

    def test_same_section_progressive_slide_prefers_strictly_stronger_completion(self):
        paragraphs = [
            Paragraph(["s1"], "介绍图像与标签。", 0, 100, heading="图像与标签处理"),
            Paragraph(["s2"], "说明最终张量。", 100, 220),
        ]
        early = Frame(
            20,
            "/tmp/early.png",
            ["s1"],
            paragraph_index=0,
            content_kind="ui",
            information_gain="partial",
            information_density="medium",
            evidence_confidence="high",
            evidence_completeness="complete",
            ahash="0" * 64,
        )
        complete = Frame(
            200,
            "/tmp/complete.png",
            ["s2"],
            paragraph_index=1,
            content_kind="diagram",
            information_gain="substantial",
            information_density="high",
            evidence_confidence="high",
            evidence_completeness="complete",
            ahash="f" * 4 + "0" * 60,
        )
        groups = plan_frame_evidence_groups(paragraphs, [early, complete])
        self.assertNotIn(0, groups)
        self.assertEqual(groups[1], [complete])

    def test_same_template_visuals_are_not_collapsed_across_sections_or_equal_strength(self):
        paragraphs = [
            Paragraph(["s1"], "训练图。", 0, 100, heading="训练"),
            Paragraph(["s2"], "训练的另一阶段。", 100, 200),
            Paragraph(["s3"], "检测图。", 200, 300, heading="检测"),
        ]
        frames = [
            Frame(20, "/tmp/a.png", ["s1"], paragraph_index=0, content_kind="diagram", information_gain="substantial", evidence_confidence="high", evidence_completeness="complete", information_density="high", ahash="0" * 64),
            Frame(150, "/tmp/b.png", ["s2"], paragraph_index=1, content_kind="diagram", information_gain="substantial", evidence_confidence="high", evidence_completeness="complete", information_density="high", ocr_text="另一阶段包含更多可读文字", ocr_confidence=96, vision_description="描述长度不同但证据等级相同。", ahash="f" * 4 + "0" * 60),
            Frame(250, "/tmp/c.png", ["s3"], paragraph_index=2, content_kind="diagram", information_gain="partial", evidence_confidence="medium", evidence_completeness="partial", information_density="medium", ahash="0" * 64),
        ]
        groups = plan_frame_evidence_groups(paragraphs, frames)
        self.assertEqual(groups[0], [frames[0]])
        self.assertEqual(groups[1], [frames[1]])
        self.assertEqual(groups[2], [frames[2]])

    def test_multiple_complete_text_slides_publish_as_separate_frame_notes(self):
        paragraphs = [Paragraph(["s1"], "作者依次展示两页名单。", 0, 20)]
        frames = [
            Frame(5, "/tmp/a.png", ["s1"], paragraph_index=0, publish_mode="note_only", replacement_markdown="- A\n- B", keep_image=False, ahash="0" * 64),
            Frame(15, "/tmp/b.png", ["s1"], paragraph_index=0, publish_mode="note_only", replacement_markdown="- C\n- D", keep_image=False, ahash="f" * 64),
        ]
        groups = plan_frame_evidence_groups(paragraphs, frames)
        self.assertEqual(len(groups[0]), 2)
        self.assertIsNone(paragraphs[0].visual_note)

    def test_markdown_uses_relative_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            image = assets / "001.png"
            image.write_bytes(b"png")
            note = root / "note.md"
            compose_markdown(
                note,
                {"title": "标题", "url": "https://example.invalid", "part": 1},
                [Paragraph(["s1"], "正文", 0, 2)],
                [Frame(1, str(image), ["s1"], paragraph_index=0, vision_description="与正文相关的界面", extracted_markdown="界面")],
            )
            rendered = note.read_text(encoding="utf-8")
            self.assertIn("(assets/001.png)", rendered)
            self.assertIn("正文", rendered)
            self.assertLess(rendered.index("正文"), rendered.index("(assets/001.png)"))
            self.assertNotIn("补充画面", rendered)

    def test_golden_layout_uses_text_for_complete_list_and_inline_image_for_dense_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            simple = assets / "podcast-list.png"
            dense = assets / "automation-prompt.png"
            simple.write_bytes(b"simple")
            dense.write_bytes(b"dense")
            paragraphs = [
                Paragraph(
                    ["s1"],
                    "作者列出了自己喜欢的 AI 播客。",
                    0,
                    4,
                ),
                Paragraph(
                    ["s2"],
                    "作者展示了每天抓取最近 24 小时 AI 新闻的 Automation 提示词。",
                    4,
                    8,
                ),
            ]
            frames = [
                Frame(
                    2,
                    str(simple),
                    ["s1"],
                    paragraph_index=0,
                    content_kind="list",
                    publish_mode="note_only",
                    replacement_markdown="十字路口 Crossing、硅谷 101、OnBoard!。",
                    keep_image=False,
                    evidence_confidence="high",
                    evidence_completeness="complete",
                    information_density="low",
                ),
                Frame(
                    6,
                    str(dense),
                    ["s2"],
                    paragraph_index=1,
                    content_kind="ui",
                    publish_mode="image_only",
                    keep_image=True,
                    evidence_confidence="high",
                    evidence_completeness="partial",
                    information_density="high",
                ),
            ]
            note = root / "note.md"
            compose_markdown(
                note,
                {"title": "黄金布局", "url": "https://example.invalid", "part": 1},
                paragraphs,
                frames,
            )
            rendered = note.read_text(encoding="utf-8")
            self.assertIn("十字路口 Crossing、硅谷 101、OnBoard!", rendered)
            self.assertNotIn("podcast-list.png", rendered)
            self.assertIn("automation-prompt.png", rendered)
            self.assertLess(
                rendered.index("Automation 提示词"),
                rendered.index("automation-prompt.png"),
            )
            self.assertEqual(rendered.count("!["), 1)

    def test_markdown_labels_visual_only_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            image = assets / "001.png"
            image.write_bytes(b"png")
            note = root / "note.md"
            compose_markdown(
                note,
                {"title": "标题", "url": "https://example.invalid", "part": 1},
                [Paragraph(["s1"], "口述正文。", 0, 2)],
                [Frame(1, str(image), ["s1"], paragraph_index=0, vision_description="完整单集标题", publish_mode="image_with_note", display_note="- 完整单集编号为 E238")],
            )
            rendered = note.read_text(encoding="utf-8")
            self.assertIn("[!info] 画面补充", rendered)
            self.assertLess(rendered.index("口述正文"), rendered.index("画面补充"))
            self.assertLess(rendered.index("assets/001.png"), rendered.index("画面补充"))

    def test_uncertain_vision_description_is_not_used_as_image_alt_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            image = assets / "001.png"
            image.write_bytes(b"png")
            note = root / "note.md"
            compose_markdown(
                note,
                {"title": "标题", "url": "https://example.invalid", "part": 1},
                [Paragraph(["s1"], "口述正文。", 0, 2)],
                [Frame(
                    1,
                    str(image),
                    ["s1"],
                    paragraph_index=0,
                    content_kind="ui",
                    vision_description="画面疑似显示错误的韩文网站名称。",
                    evidence_confidence="medium",
                )],
            )
            rendered = note.read_text(encoding="utf-8")
            self.assertNotIn("韩文网站名称", rendered)
            self.assertIn("![ui 00m01s]", rendered)

    def test_copyable_text_replaces_redundant_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            image = assets / "001.png"
            image.write_bytes(b"png")
            note = root / "note.md"
            paragraph = Paragraph(["s1"], "口述正文。", 0, 2)
            frame = Frame(1, str(image), ["s1"], paragraph_index=0, content_kind="list", publish_mode="note_only", replacement_markdown="- 节目 A\n- 节目 B", keep_image=False)
            compose_markdown(
                note,
                {"title": "标题", "url": "https://example.invalid", "part": 1},
                [paragraph],
                [frame],
            )
            rendered = note.read_text(encoding="utf-8")
            self.assertIn("节目 A", rendered)
            self.assertNotIn("assets/001.png", rendered)
            self.assertFalse(image.exists())

    def test_uncertain_formula_keeps_original_image(self):
        paragraphs = [Paragraph(["s1"], "公式推导。", 0, 4)]
        frames = [Frame(2, "/tmp/formula.png", ["s1"], ocr_text="E = mc2")]
        client = FakeClient({
            "relevance": "high",
            "content_kind": "formula",
            "information_gain": "partial",
            "publish_mode": "note_only",
            "replacement_markdown": "$$E=mc^2$$",
            "display_note": None,
            "confidence": "low",
        })
        enrich_with_visual_evidence(paragraphs, frames, client)
        self.assertTrue(frames[0].keep_image)

    def test_freezedetect_parser_closes_stable_interval_at_window_end(self):
        log = "\n".join([
            "lavfi.freezedetect.freeze_start: 1.25",
            "lavfi.freezedetect.freeze_end: 4.5",
            "lavfi.freezedetect.freeze_start: 6.0",
        ])
        self.assertEqual(
            _parse_freeze_intervals(log, window_start=10, window_duration=8),
            [(11.25, 14.5), (16.0, 18)],
        )

    def test_progressive_candidate_moves_to_latest_complete_stable_state(self):
        requests = [{
            "time_start": 0,
            "time_end": 10,
            "purpose": "读取逐步出现的 PPT 项目",
            "expected_kind": "text",
        }]
        with patch(
            "vtm_core.visual.detect_stable_intervals",
            return_value=[(0.8, 3.0), (5.0, 9.5)],
        ):
            refined, metadata = refine_completion_timestamps(
                Path("video.mp4"), [(0.45, 6.0)], [], requests, 10
            )
        self.assertEqual(refined, [(9.35, 6.0)])
        self.assertEqual(metadata["stable_completion_adjusted_count"], 1)

    def test_completion_timing_uses_midpoint_or_scene_rear_fallback(self):
        short_request = [{
            "time_start": 0,
            "time_end": 3,
            "purpose": "读取静态文字页",
            "expected_kind": "text",
        }]
        ordinary_request = [{
            "time_start": 0,
            "time_end": 10,
            "purpose": "读取完整流程图",
            "expected_kind": "diagram",
        }]
        with patch("vtm_core.visual.detect_stable_intervals", return_value=[]):
            short, _ = refine_completion_timestamps(
                Path("video.mp4"), [(0.45, 6.0)], [], short_request, 3
            )
            ordinary, _ = refine_completion_timestamps(
                Path("video.mp4"), [(0.45, 6.0)], [], ordinary_request, 10
            )
        self.assertEqual(short[0][0], 1.5)
        self.assertEqual(ordinary[0][0], 8.0)

    def test_completion_timing_failure_or_later_original_keeps_original(self):
        requests = [{
            "time_start": 0,
            "time_end": 10,
            "purpose": "读取完整页面",
            "expected_kind": "text",
        }]
        with patch(
            "vtm_core.visual.detect_stable_intervals",
            side_effect=RuntimeError("ffmpeg unavailable"),
        ):
            failed, _ = refine_completion_timestamps(
                Path("video.mp4"), [(0.45, 6.0)], [], requests, 10
            )
        with patch("vtm_core.visual.detect_stable_intervals", return_value=[]):
            later, _ = refine_completion_timestamps(
                Path("video.mp4"), [(9.0, 6.0)], [], requests, 10
            )
        self.assertEqual(failed[0][0], 0.45)
        self.assertEqual(later[0][0], 9.0)

    def test_dynamic_and_comparison_requests_never_move_to_scene_end(self):
        requests = [
            {
                "time_start": 0,
                "time_end": 10,
                "purpose": "机器人动态模拟的关键瞬间",
                "expected_kind": "ui",
            },
            {
                "time_start": 20,
                "time_end": 30,
                "purpose": "保留前后两个状态",
                "expected_kind": "comparison",
            },
        ]
        candidates = [(2.0, 6.0), (22.0, 6.0), (28.0, 6.0)]
        with patch("vtm_core.visual.detect_stable_intervals") as detector:
            refined, _ = refine_completion_timestamps(
                Path("video.mp4"), candidates, [], requests, 30
            )
        self.assertEqual(refined, candidates)
        detector.assert_not_called()

    def test_publish_mode_format_error_falls_back_to_image_only(self):
        paragraphs = [Paragraph(["s1"], "正文保持不变。", 0, 4)]
        frame = Frame(
            2, "/tmp/diagram.png", ["s1"],
            vision_description="包含多个节点的复杂流程图。",
        )
        client = FakeClient({
            "relevance": "high",
            "content_kind": "diagram",
            "information_gain": "substantial",
            "confidence": "high",
            "completeness": "complete",
            "information_density": "high",
        })
        warnings = enrich_with_visual_evidence(paragraphs, [frame], client)
        self.assertTrue(warnings)
        self.assertEqual(frame.publish_mode, "image_only")
        self.assertTrue(frame.keep_image)
        self.assertEqual(frame.display_note, "")

    def test_image_with_note_accepts_two_short_points_and_rejects_long_or_repeated_note(self):
        paragraph = Paragraph(["s1"], "正文只介绍模型输出。", 0, 4)
        good = Frame(1, "/tmp/good.png", ["s1"], vision_description="复杂结果图包含两个隐藏参数。")
        good_client = FakeClient({
            "relevance": "high",
            "content_kind": "chart",
            "information_gain": "partial",
            "publish_mode": "image_with_note",
            "replacement_markdown": None,
            "display_note": "阈值标记为 0.45\n虚线代表人工基准",
            "confidence": "high",
            "completeness": "partial",
            "information_density": "high",
        })
        self.assertEqual(enrich_with_visual_evidence([paragraph], [good], good_client), [])
        self.assertEqual(good.publish_mode, "image_with_note")
        self.assertEqual(good.display_note.count("\n"), 1)

        bad = Frame(2, "/tmp/bad.png", ["s1"], vision_description="复杂结果图包含多项信息。")
        bad_client = FakeClient({
            "relevance": "high",
            "content_kind": "chart",
            "information_gain": "partial",
            "publish_mode": "image_with_note",
            "replacement_markdown": None,
            "display_note": "正文只介绍模型输出。",
            "confidence": "high",
            "completeness": "partial",
            "information_density": "high",
        })
        warnings = enrich_with_visual_evidence([paragraph], [bad], bad_client)
        self.assertTrue(warnings)
        self.assertEqual(bad.publish_mode, "image_only")
        self.assertEqual(bad.display_note, "")

        too_many = Frame(3, "/tmp/many.png", ["s1"], vision_description="复杂结果图包含三项细节。")
        too_many_client = FakeClient({
            "relevance": "high",
            "content_kind": "chart",
            "information_gain": "partial",
            "publish_mode": "image_with_note",
            "replacement_markdown": None,
            "display_note": "阈值为 0.45\n虚线是基准\n圆点是实测值",
            "confidence": "high",
            "completeness": "partial",
            "information_density": "high",
        })
        self.assertTrue(enrich_with_visual_evidence([paragraph], [too_many], too_many_client))
        self.assertEqual(too_many.publish_mode, "image_only")

    def test_drop_and_image_only_emit_no_info(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            dropped = assets / "dropped.png"
            retained = assets / "retained.png"
            dropped.write_bytes(b"drop")
            retained.write_bytes(b"keep")
            frames = [
                Frame(1, str(dropped), ["s1"], paragraph_index=0, publish_mode="drop", keep_image=False),
                Frame(2, str(retained), ["s1"], paragraph_index=0, publish_mode="image_only", keep_image=True),
            ]
            note = root / "note.md"
            compose_markdown(
                note,
                {"title": "互斥发布", "url": "https://example.invalid", "part": 1},
                [Paragraph(["s1"], "正文。", 0, 3)],
                frames,
            )
            rendered = note.read_text(encoding="utf-8")
            self.assertNotIn("dropped.png", rendered)
            self.assertIn("retained.png", rendered)
            self.assertNotIn("[!info] 画面补充", rendered)
            self.assertFalse(dropped.exists())
            self.assertTrue(retained.exists())

    def test_each_frame_publishes_its_own_info_without_paragraph_aggregation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            first = assets / "first.png"
            second = assets / "second.png"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            paragraph = Paragraph(["s1"], "正文。", 0, 10)
            frames = [
                Frame(2, str(first), ["s1"], paragraph_index=0, publish_mode="image_with_note", display_note="- 参数 A 为 10"),
                Frame(8, str(second), ["s1"], paragraph_index=0, publish_mode="image_with_note", display_note="- 参数 B 为 20", ahash="f" * 64),
            ]
            note = root / "note.md"
            compose_markdown(
                note,
                {"title": "逐帧说明", "url": "https://example.invalid", "part": 1},
                [paragraph],
                frames,
            )
            rendered = note.read_text(encoding="utf-8")
            self.assertEqual(rendered.count("[!info] 画面补充"), 2)
            self.assertLess(rendered.index("first.png"), rendered.index("参数 A"))
            self.assertLess(rendered.index("second.png"), rendered.index("参数 B"))
            self.assertIsNone(paragraph.visual_note)

    def test_visual_publication_never_mutates_body_fields(self):
        paragraph = Paragraph(
            ["s1", "s2"],
            "正文、事实和结构保持不变。",
            0,
            4,
            heading="固定章节",
            subheading="固定小节",
        )
        frozen = (
            paragraph.text,
            paragraph.heading,
            paragraph.subheading,
            list(paragraph.source_ids),
        )
        frame = Frame(
            2, "/tmp/text.png", ["s1"],
            ocr_text="额外可复制的配置值 ABC=1",
            ocr_confidence=95,
        )
        client = FakeClient({
            "relevance": "high",
            "content_kind": "text",
            "information_gain": "partial",
            "publish_mode": "note_only",
            "replacement_markdown": "`ABC=1`",
            "display_note": None,
            "confidence": "high",
            "completeness": "complete",
            "information_density": "low",
        })
        enrich_with_visual_evidence([paragraph], [frame], client)
        self.assertEqual(
            (
                paragraph.text,
                paragraph.heading,
                paragraph.subheading,
                paragraph.source_ids,
            ),
            frozen,
        )

    def test_delete_is_soft_and_restore_uses_stable_history_id(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ):
            vault = Path(temp) / "vault"
            job = vault / "Sources" / "Videos" / "2026" / "2026-07" / "demo [BV11JNn6mESN-p1]"
            job.mkdir(parents=True)
            note = job / "demo.md"
            note.write_text("正文", encoding="utf-8")
            task = reserve_task(vault, url="u", bvid="BV11JNn6mESN")
            update_task(vault, task["task_key"], status="complete", job_dir=str(job), note=str(note))
            deleted = delete_job(vault, str(task["id"]))
            self.assertFalse(job.exists())
            self.assertEqual(deleted["status"], "deleted")
            restored = restore_job(vault, task["task_key"])
            self.assertTrue(job.exists())
            self.assertEqual(restored["task_key"], task["task_key"])

    def test_failed_record_without_note_can_be_soft_deleted_and_restored(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ):
            vault = Path(temp) / "vault"
            task = reserve_task(vault, url="u", bvid="BV1failed")
            source = Path(temp) / "state" / "tasks" / str(task["task_key"])
            source.mkdir(parents=True)
            (source / "job.json").write_text('{"status":"failed"}', encoding="utf-8")
            update_task(vault, task["task_key"], status="failed", error="quality gate")

            deleted = delete_job(vault, str(task["id"]))
            self.assertTrue(deleted["record_only"])
            self.assertFalse(source.exists())
            self.assertEqual(list_tasks(vault), [])

            restored = restore_job(vault, task["task_key"])
            self.assertTrue(restored["record_only"])
            self.assertEqual(restored["status"], "failed")
            self.assertTrue(source.is_dir())

    def test_restore_rejects_a_destination_outside_the_vault(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ):
            root = Path(temp)
            vault = root / "vault"
            job = vault / "Sources" / "Videos" / "demo"
            job.mkdir(parents=True)
            note = job / "demo.md"
            note.write_text("正文", encoding="utf-8")
            task = reserve_task(vault, url="u", bvid="BV1restore")
            update_task(vault, task["task_key"], status="complete", job_dir=str(job), note=str(note))
            delete_job(vault, str(task["id"]))
            update_task(vault, task["task_key"], job_dir=str(root / "outside"))

            with self.assertRaisesRegex(RuntimeError, "不在 Obsidian Vault"):
                restore_job(vault, task["task_key"])

    def test_no_visual_pipeline_writes_auditable_note(self):
        info = VideoInfo(
            url="https://www.bilibili.com/video/BV1ab411c7DE",
            bvid="BV1ab411c7DE",
            cid=42,
            part=1,
            title="测试视频",
            part_title="测试视频",
            duration=12,
            owner="测试作者",
            cover="",
        )

        class FakeBilibili:
            def __init__(self):
                pass

            def inspect(self, url, part):
                return info

            def subtitles(self, inspected):
                return [Segment("s000001", 0, 2, "嗯，完整内容。")], {
                    "source": "bilibili_subtitle"
                }

        editor = SequenceClient(direct_manuscript_responses())
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ), patch(
            "vtm_core.pipeline.BilibiliClient", FakeBilibili
        ), patch("vtm_core.pipeline.text_client", return_value=editor):
            progress = []
            result = run(
                Options(
                    url=info.url,
                    vault=Path(temp),
                    no_visual=True,
                    progress=progress.append,
                    task_key="20260716-1",
                    task_date="2026-07-16",
                )
            )
            note = Path(result["note"])
            self.assertTrue(note.exists())
            coverage = json.loads((Path(temp) / "state" / "tasks" / "20260716-1" / "coverage.json").read_text())
            self.assertEqual(
                coverage["editing_architecture"],
                "whole_transcript_plan_visual_reconcile_write_restore_copyedit",
            )
            self.assertEqual(coverage["llm_document_passes"], 4)
            self.assertFalse((Path(temp) / "state" / "tasks" / "20260716-1" / "information-units.json").exists())
            self.assertEqual(result["transcript_source"], "bilibili_subtitle")
            self.assertEqual(result["bvid"], "BV1ab411c7DE")
            self.assertEqual(progress[0], "正在读取视频信息。")
            self.assertIn("正在提取并匹配关键画面。", progress)
            self.assertEqual(progress[-1], "处理完成。")

    def test_youtube_no_visual_pipeline_reuses_the_frozen_manuscript_core(self):
        info = YouTubeVideoInfo(
            url="https://www.youtube.com/watch?v=BaW_jenozKc",
            video_id="BaW_jenozKc",
            title="YouTube 测试视频",
            duration=12,
            owner="测试频道",
            language="zh-CN",
        )

        class FakeYouTubeClient:
            def inspect(self, url):
                return info

            def subtitles(self, inspected):
                return [Segment("s000001", 0, 2, "完整的公开视频内容。")], {
                    "source": "youtube_manual_subtitle",
                    "language": "zh-CN",
                }

        adapter = YouTubeSourceAdapter(FakeYouTubeClient())
        editor = SequenceClient(direct_manuscript_responses("完整的公开视频内容。"))
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ), patch("vtm_core.pipeline.adapter_for", return_value=adapter), patch(
            "vtm_core.pipeline.text_client", return_value=editor
        ):
            result = run(
                Options(
                    url=info.url,
                    vault=Path(temp) / "vault",
                    no_visual=True,
                    task_key="20260717-1",
                    task_date="2026-07-17",
                )
            )
            note = Path(result["note"])
            note_text = note.read_text(encoding="utf-8")
            metadata = json.loads(
                (Path(temp) / "state" / "tasks" / "20260717-1" / "metadata.json").read_text()
            )
        self.assertEqual(result["platform"], "youtube")
        self.assertEqual(result["source_key"], "youtube:BaW_jenozKc")
        self.assertEqual(result["transcript_source"], "youtube_manual_subtitle")
        self.assertIn("tags: [video-manuscript, youtube]", note_text)
        self.assertIn("作者/频道：测试频道", note_text)
        self.assertEqual(metadata["pipeline_version"], PIPELINE_VERSION)
        self.assertEqual(editor.calls, 4)

    def test_failure_after_indexing_rolls_back_note_assets_and_index_rows(self):
        info = VideoInfo(
            url="https://www.bilibili.com/video/BV1ab411c7DE",
            bvid="BV1ab411c7DE",
            cid=42,
            part=1,
            title="回滚测试",
            part_title="回滚测试",
            duration=12,
            owner="测试作者",
            cover="",
        )

        class FakeBilibili:
            def inspect(self, url, part):
                return info

            def subtitles(self, inspected):
                return [Segment("s000001", 0, 2, "必须保留的完整内容。")], {
                    "source": "bilibili_subtitle"
                }

        editor = SequenceClient(
            direct_manuscript_responses("必须保留的完整内容。")
        )

        def fail_after_publication(message):
            if message == "处理完成。":
                raise RuntimeError("completion delivery failed")

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ), patch("vtm_core.pipeline.BilibiliClient", FakeBilibili), patch(
            "vtm_core.pipeline.text_client", return_value=editor
        ):
            vault = Path(temp) / "vault"
            with self.assertRaisesRegex(RuntimeError, "completion delivery failed"):
                run(
                    Options(
                        url=info.url,
                        vault=vault,
                        no_visual=True,
                        progress=fail_after_publication,
                        task_key="20260716-1",
                        task_date="2026-07-16",
                    )
                )
            self.assertFalse(any((vault / "Sources").rglob("*.md")))
            index_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (vault / "Indexes").rglob("*.md")
            )
            self.assertNotIn("20260716-1", index_text)

    def test_no_subtitle_downloads_audio_only_before_asr(self):
        info = VideoInfo(
            url="https://www.bilibili.com/video/BV1ab411c7DE",
            bvid="BV1ab411c7DE",
            cid=42,
            part=1,
            title="无字幕视频",
            part_title="无字幕视频",
            duration=12,
            owner="测试作者",
            cover="",
        )

        class FakeBilibili:
            def __init__(self):
                pass

            def inspect(self, url, part):
                return info

            def subtitles(self, inspected):
                return [], {"warning": "none"}

            def ai_transcript(self, inspected):
                return [], {"warning": "none"}

        calls = []

        def fake_download(url, work_dir, **kwargs):
            calls.append(kwargs)
            path = work_dir / "audio.m4s"
            path.write_bytes(b"audio")
            return path

        editor = SequenceClient(direct_manuscript_responses())
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ), patch(
            "vtm_core.pipeline.BilibiliClient", FakeBilibili
        ), patch("vtm_core.media.download_media", side_effect=fake_download), patch(
            "vtm_core.pipeline.transcribe",
            return_value=([Segment("s000001", 0, 2, "完整内容。")], {"source": "funasr_paraformer"}),
        ), patch("vtm_core.pipeline.text_client", return_value=editor):
            result = run(Options(url=info.url, vault=Path(temp), no_visual=True, task_key="20260716-1", task_date="2026-07-16"))
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["audio_only"])
        self.assertEqual(result["transcript_source"], "funasr_paraformer")

    def test_temporary_media_is_removed_when_asr_fails(self):
        info = VideoInfo(
            url="https://www.bilibili.com/video/BV1ab411c7DE",
            bvid="BV1ab411c7DE",
            cid=42,
            part=1,
            title="失败清理测试",
            part_title="失败清理测试",
            duration=12,
            owner="测试作者",
            cover="",
        )

        class FakeBilibili:
            def __init__(self):
                pass

            def inspect(self, url, part):
                return info

            def subtitles(self, inspected):
                return [], {"warning": "none"}

            def ai_transcript(self, inspected):
                return [], {"warning": "none"}

        def fake_download(url, work_dir, **kwargs):
            path = work_dir / "audio.m4s"
            path.write_bytes(b"temporary audio")
            return path

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ), patch("vtm_core.pipeline.BilibiliClient", FakeBilibili), patch(
            "vtm_core.media.download_media", side_effect=fake_download
        ), patch(
            "vtm_core.pipeline.transcribe", side_effect=RuntimeError("ASR failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "ASR failed"):
                run(
                    Options(
                        url=info.url,
                        vault=Path(temp) / "vault",
                        no_visual=True,
                        task_key="20260716-1",
                        task_date="2026-07-16",
                    )
                )
            self.assertFalse((Path(temp) / "state" / "work" / "20260716-1").exists())

    def test_interrupted_job_is_cancelled_and_temporary_media_is_removed(self):
        info = VideoInfo(
            url="https://www.bilibili.com/video/BV1ab411c7DE",
            bvid="BV1ab411c7DE",
            cid=42,
            part=1,
            title="中止清理测试",
            part_title="中止清理测试",
            duration=12,
            owner="测试作者",
            cover="",
        )

        class FakeBilibili:
            def inspect(self, url, part):
                return info

            def subtitles(self, inspected):
                return [], {"warning": "none"}

            def ai_transcript(self, inspected):
                return [], {"warning": "none"}

        def fake_download(url, work_dir, **kwargs):
            path = work_dir / "audio.m4s"
            path.write_bytes(b"temporary audio")
            return path

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ), patch("vtm_core.pipeline.BilibiliClient", FakeBilibili), patch(
            "vtm_core.media.download_media", side_effect=fake_download
        ), patch("vtm_core.pipeline.transcribe", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run(
                    Options(
                        url=info.url,
                        vault=Path(temp) / "vault",
                        no_visual=True,
                        task_key="20260716-1",
                        task_date="2026-07-16",
                    )
                )

            job = json.loads(
                (Path(temp) / "state" / "tasks" / "20260716-1" / "job.json").read_text()
            )
            self.assertEqual(job["status"], "cancelled")
            self.assertFalse((Path(temp) / "state" / "work" / "20260716-1").exists())
            self.assertFalse(any((Path(temp) / "vault").rglob("*.md")))

    def test_cancel_latest_running_reconciles_an_old_interrupted_task(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"VTM_STATE_DIR": str(Path(temp) / "state"), "VTM_TIMEZONE": "Asia/Shanghai"},
        ):
            vault = Path(temp) / "vault"
            task = reserve_task(vault, url="https://bilibili.com/video/BV1old", bvid="BV1old")
            work = Path(temp) / "state" / "work" / str(task["task_key"])
            work.mkdir(parents=True)
            (work / "audio.m4s").write_bytes(b"temporary")
            incomplete = (
                vault
                / "Sources"
                / "Videos"
                / "2026"
                / "2026-07"
                / f"{task['task_key']}-unfinished"
            )
            incomplete.mkdir(parents=True)
            (incomplete / "partial.txt").write_text("partial", encoding="utf-8")
            indexes = vault / "Indexes"
            indexes.mkdir(parents=True)
            (indexes / "Videos.md").write_text(
                f"# 视频笔记\n\n- [[note|title]] · `{task['task_key']}`\n",
                encoding="utf-8",
            )

            result = cancel_job(vault, latest_running=True)

            self.assertEqual(result["status"], "cancelled")
            self.assertFalse(work.exists())
            self.assertFalse(incomplete.exists())
            self.assertNotIn(
                str(task["task_key"]),
                (indexes / "Videos.md").read_text(encoding="utf-8"),
            )
            rows = list_tasks(vault, all_tasks=True)
            self.assertEqual(rows[0]["status"], "cancelled")

    def test_cancel_latest_running_selects_the_most_recent_task(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"VTM_STATE_DIR": str(Path(temp) / "state"), "VTM_TIMEZONE": "Asia/Shanghai"},
        ):
            vault = Path(temp) / "vault"
            first = reserve_task(vault, url="u1", bvid="BV1first")
            second = reserve_task(vault, url="u2", bvid="BV1second")

            result = cancel_job(vault, latest_running=True)

            self.assertEqual(result["task_key"], second["task_key"])
            self.assertEqual(get_task(vault, first["task_key"])["status"], "running")
            self.assertEqual(get_task(vault, second["task_key"])["status"], "cancelled")

    def test_task_registry_persists_queue_pid_and_stage(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"VTM_STATE_DIR": str(Path(temp) / "state"), "VTM_TIMEZONE": "Asia/Shanghai"},
        ):
            vault = Path(temp) / "vault"
            task = reserve_task(
                vault,
                url="https://bilibili.com/video/BV1queued",
                bvid="BV1queued",
                status="queued",
            )
            updated = update_task(
                vault,
                task["task_key"],
                pid=43210,
                stage=3,
                stage_message="正在清理和重排完整文字稿。",
            )

            self.assertEqual(updated["status"], "queued")
            self.assertEqual(updated["pid"], 43210)
            self.assertEqual(updated["stage"], 3)
            self.assertEqual(updated["stage_message"], "正在清理和重排完整文字稿。")

    def test_submit_detaches_the_worker_and_rewrites_command_to_run(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ), patch("video_manuscript.subprocess.Popen") as popen:
            popen.return_value.pid = 24680

            result = submit_detached(
                [
                    "submit",
                    "--url",
                    "https://www.bilibili.com/video/BV1detached/",
                    "--gateway-output",
                    "--progress-target",
                    "feishu",
                ]
            )

            command = popen.call_args.args[0]
            self.assertEqual(Path(command[0]).name, "vtm")
            self.assertEqual(command[1], "run")
            self.assertNotIn("submit", command[1:])
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertEqual(result["status"], "submitted")
            self.assertTrue(result["chat_session_released"])
            self.assertEqual(
                result["assistant_reply"],
                "已提交后台处理，任务编号将在第一条进度中显示。",
            )
            self.assertEqual(
                result["assistant_reply_contract"],
                "return_assistant_reply_verbatim_and_stop",
            )
            self.assertEqual(result["progress_delivery"], "out_of_band_only")
            self.assertFalse(result["progress_in_this_result"])
            self.assertNotIn("task_key", result)
            self.assertNotIn("title", result)
            self.assertNotIn("note", result)

    def test_bulk_delete_uses_stable_plan_and_protects_active_jobs(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"VTM_STATE_DIR": str(Path(temp) / "state"), "VTM_TIMEZONE": "Asia/Shanghai"},
        ):
            vault = Path(temp) / "vault"
            keep = reserve_task(vault, url="keep", bvid="BV1keep")
            update_task(vault, keep["task_key"], status="failed", error="old")
            remove_one = reserve_task(vault, url="remove1", bvid="BV1remove1")
            update_task(vault, remove_one["task_key"], status="failed", error="old")
            remove_two = reserve_task(vault, url="remove2", bvid="BV1remove2")
            update_task(vault, remove_two["task_key"], status="cancelled", error="old")
            active = reserve_task(
                vault,
                url="active",
                bvid="BV1active",
                status="queued",
            )

            plan = plan_bulk_delete(
                vault,
                keep=[str(keep["task_key"])],
                all_history=True,
            )

            self.assertEqual(plan["status"], "confirmation_required")
            self.assertEqual(plan["delete_count"], 2)
            self.assertEqual(plan["active_protected"], [active["task_key"]])

            messages = []
            with patch(
                "video_manuscript.send_hermes_progress",
                side_effect=lambda target, message: messages.append((target, message)),
            ):
                result = confirm_bulk_delete(
                    vault,
                    str(plan["confirmation_token"]),
                    send_target="feishu",
                )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["deleted_count"], 2)
            self.assertEqual(get_task(vault, keep["task_key"])["status"], "failed")
            self.assertEqual(get_task(vault, active["task_key"])["status"], "queued")
            self.assertTrue(get_task(vault, remove_one["task_key"], include_deleted=True)["deleted_at"])
            self.assertTrue(get_task(vault, remove_two["task_key"], include_deleted=True)["deleted_at"])
            self.assertIn("[RUNNING]", messages[0][1])
            self.assertIn("[COMPLETE]", messages[-1][1])

    def test_douyin_router_data_maps_public_video_without_reimplementing_pipeline(self):
        payload = {
            "loaderData": {
                "video_(id)/page": {
                    "videoInfoRes": {
                        "item_list": [
                            {
                                "aweme_id": "7604129988555574538",
                                "desc": "公开抖音知识视频 #测试",
                                "create_time": 1700000000,
                                "author": {"nickname": "测试作者"},
                                "video": {
                                    "duration": 12500,
                                    "width": 1080,
                                    "height": 1920,
                                    "play_addr": {
                                        "url_list": [
                                            "https://aweme.snssdk.com/aweme/v1/play/?video_id=public"
                                        ]
                                    },
                                    "cover": {
                                        "url_list": ["https://p26-sign.douyinpic.com/cover.jpeg"]
                                    },
                                },
                            }
                        ]
                    }
                }
            }
        }
        info = parse_douyin_router_data(payload, "7604129988555574538")
        self.assertEqual(info.video_id, "7604129988555574538")
        self.assertEqual(info.title, "公开抖音知识视频 #测试")
        self.assertEqual(info.owner, "测试作者")
        self.assertEqual(info.duration, 12.5)
        self.assertEqual(info.height, 1920)
        self.assertTrue(info.video_url.startswith("https://aweme.snssdk.com/"))
        adapter = DouyinSourceAdapter()
        self.assertEqual(adapter.reference(info).source_key, "douyin:7604129988555574538")
        metadata = adapter.metadata(info)
        self.assertNotIn("video_url", metadata)
        self.assertEqual(metadata["extraction_engine"], "social-post-extractor-mcp-router-data")

    def test_douyin_adapter_handles_direct_and_share_text_urls(self):
        adapter = DouyinSourceAdapter()
        direct = "https://www.douyin.com/video/7604129988555574538?previous_page=web_code_link"
        self.assertTrue(adapter.can_handle(direct))
        self.assertTrue(adapter.can_handle(f"复制打开抖音 {direct} 查看视频"))
        self.assertEqual(
            adapter.canonicalize_input(direct),
            "https://www.douyin.com/video/7604129988555574538",
        )
        self.assertEqual(adapter.source_id_from_url(direct), "7604129988555574538")
        self.assertIsInstance(adapter_for(direct), DouyinSourceAdapter)
        self.assertFalse(adapter.can_handle("https://example.com/video/7604129988555574538"))

    def test_douyin_short_link_canonicalization_reuses_public_redirect(self):
        client = DouyinClient()
        with patch.object(
            client,
            "_fetch_html",
            return_value=("", "https://www.douyin.com/video/7604129988555574538?from=share"),
        ):
            canonical = client.canonicalize("https://v.douyin.com/example/")
        self.assertEqual(canonical, "https://www.douyin.com/video/7604129988555574538")

    def test_douyin_router_rejects_image_note_and_untrusted_media_host(self):
        image_payload = {
            "loaderData": {
                "note_(id)/page": {
                    "videoInfoRes": {"item_list": [{"aweme_id": "123", "images": [{}]}]}
                }
            }
        }
        with self.assertRaisesRegex(RuntimeError, "图文作品"):
            parse_douyin_router_data(image_payload, "123")
        with self.assertRaisesRegex(RuntimeError, "不受信任"):
            validate_douyin_media_url("https://evil.example/video.mp4")

    def test_douyin_transcript_contract_falls_back_to_existing_local_asr(self):
        info = DouyinVideoInfo(
            url="https://www.douyin.com/video/7604129988555574538",
            video_id="7604129988555574538",
            title="测试",
            duration=60,
            owner="作者",
            cover="",
            width=1080,
            height=1920,
            published_at="",
        )
        adapter = DouyinSourceAdapter()
        primary, primary_meta = adapter.primary_transcript(info)
        secondary, secondary_meta = adapter.secondary_transcript(info)
        self.assertEqual(primary, [])
        self.assertEqual(secondary, [])
        self.assertIn("没有提供原生字幕", primary_meta["warning"])
        self.assertIn("本地 ASR", secondary_meta["warning"])

    def test_long_source_folder_name_preserves_stable_marker(self):
        marker = "DY-7538955201693994298"
        folder = _source_folder_name("20260717-1", "很长的抖音标题" * 30, marker)
        self.assertLessEqual(len(folder), 100)
        self.assertTrue(folder.endswith(f"[{marker}]"))

    def test_xiaohongshu_initial_state_preserves_text_topics_and_original_image_order(self):
        note_id = "696a395e000000000a02b3c7"
        state = {
            "note": {
                "noteDetailMap": {
                    note_id: {
                        "note": {
                            "noteId": note_id,
                            "title": "四种面条做法合集",
                            "desc": "第一种：准备番茄和鸡蛋。\n第二种：煮面后加入调味汁。",
                            "type": "normal",
                            "time": 1700000000000,
                            "user": {"userId": "user-1", "nickname": "测试作者"},
                            "tagList": [{"name": "家常菜"}],
                            "imageList": [
                                {"urlDefault": "https://sns-web-i10.rednotecdn.com/one.jpg"},
                                {"urlPre": "http://sns-web-i10.rednotecdn.com/two.jpg"},
                            ],
                        }
                    }
                }
            }
        }
        raw_html = (
            "<html><head><title>后备标题</title></head><body>"
            f"<script>window.__INITIAL_STATE__={json.dumps(state, ensure_ascii=False)}</script>"
            "</body></html>"
        )
        info = parse_xhs_initial_state(
            raw_html,
            f"https://www.rednote.com/explore/{note_id}?xsec_token=public",
        )
        rendered = "\n".join(Segment(**item).text for item in info.segments)
        self.assertEqual(info.note_id, note_id)
        self.assertEqual(info.title, "四种面条做法合集")
        self.assertEqual(info.author, "测试作者")
        self.assertEqual(info.content_type, "image_note")
        self.assertIn("第一种", rendered)
        self.assertIn("第二种", rendered)
        self.assertIn("#家常菜", rendered)
        self.assertEqual(len(info.images), 2)
        self.assertTrue(info.images[0]["url"].endswith("/one.jpg"))
        self.assertTrue(info.images[1]["url"].startswith("https://"))
        self.assertEqual([item["order"] for item in info.images], [1, 2])
        self.assertTrue(all(item["after_segment_id"] == info.segments[-1]["id"] for item in info.images))

    def test_xiaohongshu_equal_text_and_image_counts_keep_one_to_one_order(self):
        note_id = "696a395e000000000a02b3c7"
        note = {
            "noteId": note_id,
            "title": "两步教程",
            "desc": "第一步准备材料\n第二步完成制作",
            "type": "normal",
            "imageList": [
                {"urlDefault": "https://sns-web-i10.rednotecdn.com/step-1.jpg"},
                {"urlDefault": "https://sns-web-i10.rednotecdn.com/step-2.jpg"},
            ],
        }
        state = {"note": {"noteDetailMap": {note_id: {"note": note}}}}
        raw_html = f"<script>window.__INITIAL_STATE__={json.dumps(state, ensure_ascii=False)}</script>"
        info = parse_xhs_initial_state(raw_html, f"https://www.rednote.com/explore/{note_id}")
        self.assertEqual(
            [item["after_segment_id"] for item in info.images],
            [item["id"] for item in info.segments],
        )

    def test_xiaohongshu_adapter_supports_current_legacy_and_bare_share_links(self):
        adapter = XiaohongshuSourceAdapter()
        note_id = "696a395e000000000a02b3c7"
        rednote = f"https://www.rednote.com/explore/{note_id}?xsec_token=public&utm_source=chat"
        legacy = f"https://www.xiaohongshu.com/discovery/item/{note_id}"
        self.assertTrue(adapter.can_handle(rednote))
        self.assertTrue(adapter.can_handle(legacy))
        self.assertTrue(adapter.can_handle("复制打开 xhslink.com/m/example 查看笔记"))
        self.assertEqual(
            adapter.canonicalize_input(rednote),
            f"https://www.rednote.com/explore/{note_id}?xsec_token=public",
        )
        self.assertEqual(adapter.source_id_from_url(legacy), f"note-{note_id}")
        self.assertIsInstance(adapter_for(rednote), XiaohongshuSourceAdapter)
        self.assertFalse(GenericWebSourceAdapter().can_handle(rednote))

    def test_xiaohongshu_short_link_reuses_public_redirect(self):
        adapter = XiaohongshuSourceAdapter()
        note_id = "696a395e000000000a02b3c7"
        with patch.object(
            adapter,
            "_fetch_html",
            return_value=("", f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=public"),
        ):
            canonical = adapter.canonicalize_input("xhslink.com/m/example")
        self.assertEqual(
            canonical,
            f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=public",
        )

    def test_xiaohongshu_rejects_video_and_empty_risk_control_state(self):
        note_id = "696a395e000000000a02b3c7"
        video_state = {
            "note": {
                "noteDetailMap": {
                    note_id: {
                        "note": {
                            "noteId": note_id,
                            "title": "视频",
                            "type": "video",
                            "video": {"media": {}},
                            "imageList": [],
                        }
                    }
                }
            }
        }
        video_html = f"<script>window.__INITIAL_STATE__={json.dumps(video_state)}</script>"
        with self.assertRaisesRegex(RuntimeError, "视频笔记"):
            parse_xhs_initial_state(video_html, f"https://www.rednote.com/explore/{note_id}")
        empty_html = '<script>window.__INITIAL_STATE__={"note":{"noteDetailMap":{}}}</script>'
        with self.assertRaisesRegex(RuntimeError, "没有返回公开笔记详情"):
            parse_xhs_initial_state(empty_html, f"https://www.rednote.com/explore/{note_id}")

    def test_xiaohongshu_rejects_image_redirect_to_untrusted_host(self):
        handler = _XhsImageRedirectHandler()
        with self.assertRaisesRegex(RuntimeError, "不受信任"):
            handler.redirect_request(None, None, 302, "Found", {}, "https://example.com/image.jpg")

    def test_generic_web_parser_preserves_ordered_article_text_images_and_tables(self):
        source = """
        <html><head>
          <title>错误后备标题</title>
          <meta property="og:title" content="公开文章标题">
          <meta property="og:site_name" content="测试站点">
        </head><body>
          <span class="author-name">测试作者</span>
          <span class="publish-time">2026-07-17 发布</span>
          <nav>导航与登录入口不应进入正文。</nav>
          <article id="content_views" class="article_content">
            <h2>安装步骤</h2>
            <p>先下载工具，再打开设置页面并启用对应选项。这一段包含足够长度的公开正文内容。</p>
            <img src="/images/step.png" alt="设置页面截图" width="800" height="500">
            <table><tr><th>字段</th><th>含义</th></tr><tr><td>mode</td><td>运行模式</td></tr></table>
            <p>公式 <span class="ztext-math" data-tex="E=mc^2"></span>，参考<a href="https://link.zhihu.com/?target=https%3A%2F%2Fdocs.example.com%2Fguide">官方指南</a>并执行<code>vtm doctor</code>。</p>
            <p>最后检查输出字段，并确认任务状态显示完成；失败时应核对网络权限和输入地址。</p>
          </article>
          <div class="comment-list">评论区内容不能混入文章。</div>
        </body></html>
        """
        metadata, segments, images = parse_html_document(source, "https://blog.example.com/post/1")
        rendered = "\n".join(segment.text for segment in segments)
        self.assertEqual(metadata["title"], "公开文章标题")
        self.assertEqual(metadata["author"], "测试作者")
        self.assertEqual(metadata["published_at"], "2026-07-17")
        self.assertIn(
            metadata["extraction_engine"],
            {
                "readability-lxml",
                "readability-lxml+structured-fidelity",
                "deterministic_html_fallback",
            },
        )
        self.assertIn("## 安装步骤", rendered)
        self.assertIn("| 字段 | 含义 |", rendered)
        self.assertIn("$E=mc^2$", rendered)
        self.assertIn("[官方指南](https://docs.example.com/guide)", rendered)
        self.assertIn("`vtm doctor`", rendered)
        self.assertNotIn("导航与登录", rendered)
        self.assertNotIn("评论区内容", rendered)
        self.assertTrue(all(segment.locator_kind == "document_order" for segment in segments))
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].url, "https://blog.example.com/images/step.png")
        self.assertIn(images[0].after_segment_id, {segment.id for segment in segments})

    def test_zhihu_adapter_reuses_upstream_answer_html_as_document_evidence(self):
        captured = {}

        class FakeClient:
            @staticmethod
            async def get_answer(answer_id, credential=None):
                captured["answer_id"] = answer_id
                captured["credential"] = credential
                return {
                    "id": answer_id,
                    "question": {"id": 12345, "title": "怎样验证一个提取流程？"},
                    "author": {"name": "测试答主"},
                    "created_time": 1700000000,
                    "content": (
                        "<h2>验证步骤</h2>"
                        "<p>先运行测试，再检查输出中的观点、理由、步骤和限制条件。</p>"
                        '<p>公式<span class="ztext-math" data-tex="a^2+b^2=c^2"></span>，'
                        '<a href="https://link.zhihu.com/?target=https%3A%2F%2Fexample.com%2Fdocs">阅读文档</a>。</p>'
                        '<img src="https://picx.zhimg.com/example.png" alt="验证结果截图">'
                    ),
                }

        adapter = ZhihuSourceAdapter()
        with patch("vtm_core.zhihu.zhihu_client", FakeClient), patch(
            "vtm_core.zhihu.ZhihuCredential", object
        ), patch.dict(os.environ, {}, clear=True):
            info = adapter.inspect(
                "https://www.zhihu.com/question/12345/answer/67890?utm_source=chat"
            )
        rendered = "\n".join(Segment(**item).text for item in info.segments)
        self.assertEqual(captured["answer_id"], 67890)
        self.assertIsNone(captured["credential"])
        self.assertEqual(info.url, "https://www.zhihu.com/question/12345/answer/67890")
        self.assertEqual(info.source_id, "answer-67890")
        self.assertEqual(info.author, "测试答主")
        self.assertEqual(info.access_mode, "public")
        self.assertIn("## 验证步骤", rendered)
        self.assertIn("$a^2+b^2=c^2$", rendered)
        self.assertIn("[阅读文档](https://example.com/docs)", rendered)
        self.assertEqual(len(info.images), 1)
        self.assertEqual(adapter.reference(info).source_key, "zhihu:answer-67890")

    def test_zhihu_adapter_uses_only_hidden_z_c0_for_authorized_article(self):
        captured = {}

        class FakeCredential:
            def __init__(self, *, z_c0):
                captured["z_c0"] = z_c0

        class FakeClient:
            @staticmethod
            async def get_article(article_id, credential=None):
                captured["credential"] = credential
                return {
                    "id": article_id,
                    "title": "知乎专栏测试",
                    "author": {"name": "文章作者"},
                    "created": 1700000000,
                    "content": "<p>这是一篇包含完整解释、示例和注意事项的公开文章正文。</p>",
                }

        with patch("vtm_core.zhihu.zhihu_client", FakeClient), patch(
            "vtm_core.zhihu.ZhihuCredential", FakeCredential
        ), patch.dict(os.environ, {"ZHIHU_Z_C0": "private-cookie"}, clear=True):
            info = ZhihuSourceAdapter().inspect("https://zhuanlan.zhihu.com/p/998877")
        self.assertEqual(captured["z_c0"], "private-cookie")
        self.assertIs(captured["credential"].__class__, FakeCredential)
        self.assertEqual(info.access_mode, "authorized_session")
        self.assertNotIn("private-cookie", json.dumps(info.to_dict(), ensure_ascii=False))
        self.assertEqual(info.source_id, "article-998877")

    def test_zhihu_public_risk_control_returns_configuration_instruction(self):
        class BlockedClient:
            @staticmethod
            async def get_answer(answer_id, credential=None):
                raise zhihu_module.AuthenticationError("risk control")

        with patch("vtm_core.zhihu.zhihu_client", BlockedClient), patch(
            "vtm_core.zhihu.ZhihuCredential", object
        ), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "configure secret zhihu_z_c0"):
                ZhihuSourceAdapter().inspect(
                    "https://www.zhihu.com/question/12345/answer/67890"
                )

    def test_zhihu_network_error_does_not_request_a_cookie(self):
        class OfflineClient:
            @staticmethod
            async def get_article(article_id, credential=None):
                raise zhihu_module.NetworkError("offline")

        with patch("vtm_core.zhihu.zhihu_client", OfflineClient), patch(
            "vtm_core.zhihu.ZhihuCredential", object
        ), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "检查当前机器到知乎的网络") as caught:
                ZhihuSourceAdapter().inspect("https://zhuanlan.zhihu.com/p/998877")
        self.assertNotIn("zhihu_z_c0", str(caught.exception))

    def test_zhihu_adapter_claims_only_answers_and_articles(self):
        adapter = ZhihuSourceAdapter()
        self.assertIsInstance(
            adapter_for("https://www.zhihu.com/question/12345/answer/67890"),
            ZhihuSourceAdapter,
        )
        self.assertTrue(adapter.can_handle("https://www.zhihu.com/en/answer/67890"))
        self.assertTrue(adapter.can_handle("https://www.zhihu.com/en/article/998877"))
        self.assertFalse(adapter.can_handle("https://www.zhihu.com/question/12345"))
        self.assertEqual(
            adapter.normalize_input_url("https://www.zhihu.com/en/article/998877"),
            "https://zhuanlan.zhihu.com/p/998877",
        )

    def test_generic_web_url_policy_removes_tracking_and_blocks_private_networks(self):
        canonical = canonicalize_web_url(
            "HTTPS://Example.COM/post?id=7&utm_source=chat#section",
            resolve_dns=False,
        )
        self.assertEqual(canonical, "https://example.com/post?id=7")
        self.assertFalse(GenericWebSourceAdapter().can_handle("BV1ab411c7DE"))
        self.assertFalse(GenericWebSourceAdapter().can_handle("BaW_jenozKc"))
        for url in (
            "http://127.0.0.1/private",
            "http://10.0.0.8/private",
            "http://localhost/private",
            "file:///etc/passwd",
            "https://user:password@example.com/private",
        ):
            with self.assertRaises(ValueError):
                validate_public_url(url, resolve_dns=False)

    def test_generic_web_reuses_standard_jsonld_metadata(self):
        class FakeExtruct:
            @staticmethod
            def extract(raw_html, **kwargs):
                return {
                    "json-ld": [
                        {
                            "@type": "BlogPosting",
                            "headline": "结构化标题",
                            "author": [{"name": "作者甲"}, {"name": "作者乙"}],
                            "datePublished": "2026-07-17T10:30:00+08:00",
                            "publisher": {"name": "结构化站点"},
                        }
                    ]
                }

        with patch("vtm_core.web.extruct", FakeExtruct):
            metadata = _structured_article_metadata("<html></html>", "https://example.com")
        self.assertEqual(metadata["title"], "结构化标题")
        self.assertEqual(metadata["author"], "作者甲, 作者乙")
        self.assertEqual(metadata["published_at"], "2026-07-17T10:30:00+08:00")
        self.assertEqual(metadata["site_name"], "结构化站点")

    def test_document_prompt_adapter_removes_video_timestamp_framing(self):
        captured = []

        class Delegate:
            def chat(self, messages, **kwargs):
                captured.extend(messages)
                return "{}"

        client = _DocumentEditingClient(Delegate())
        client.chat(
            [{"role": "system", "content": "阅读完整带时间戳字幕，生成视频详细文字稿并核对画面。"}],
            json_mode=True,
        )
        prompt = str(captured[0]["content"])
        self.assertIn("按原文顺序排列的完整内容块", prompt)
        self.assertIn("来源详细编辑稿", prompt)
        self.assertNotIn("时间戳字幕", prompt)
        self.assertNotIn("视频详细文字稿", prompt)

    def test_document_locator_fields_do_not_change_legacy_video_artifact_schema(self):
        video_segment = Segment("s1", 0, 1, "视频字幕")
        document_segment = Segment("s1", 0, 1, "网页正文", locator_kind="document_order")
        video_frame = Frame(1, "/tmp/frame.png")
        document_frame = Frame(
            1,
            "/tmp/source.png",
            media_kind="source_image",
            locator_label="原文第 1 张图片",
            source_url="https://example.com/source.png",
        )
        self.assertNotIn("locator_kind", video_segment.to_dict())
        self.assertEqual(document_segment.to_dict()["locator_kind"], "document_order")
        self.assertNotIn("media_kind", video_frame.to_dict())
        self.assertNotIn("locator_label", video_frame.to_dict())
        self.assertNotIn("source_url", video_frame.to_dict())
        self.assertEqual(document_frame.to_dict()["media_kind"], "source_image")
        self.assertEqual(document_frame.to_dict()["locator_label"], "原文第 1 张图片")
        self.assertEqual(document_frame.to_dict()["source_url"], "https://example.com/source.png")

    def test_document_source_image_uses_order_label_not_fake_video_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            image = assets / "source-001.png"
            image.write_bytes(b"png")
            paragraphs = [Paragraph(["s000001"], "网页正文。", 0, 1)]
            frames = [
                Frame(
                    1,
                    str(image),
                    ["s000001"],
                    media_kind="source_image",
                    locator_label="原文第 1 张图片",
                )
            ]
            _align_document_images(paragraphs, frames)
            note = root / "note.md"
            compose_markdown(
                note,
                {
                    "title": "网页笔记",
                    "url": "https://example.com/post",
                    "platform": "generic_web",
                    "source_kind": "document",
                    "source_id": "abc",
                },
                paragraphs,
                frames,
            )
            rendered = note.read_text(encoding="utf-8")
            self.assertIn('type: "source-manuscript"', rendered)
            self.assertIn("tags: [source-manuscript, generic_web]", rendered)
            self.assertIn("![原文第 1 张图片](assets/source-001.png)", rendered)
            self.assertIn("*原文第 1 张图片*", rendered)
            self.assertNotIn("画面时间", rendered)
            self.assertNotIn("00m01s", rendered)

    def test_document_index_is_separate_without_changing_video_daily_heading(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            video_note = vault / "Sources" / "Videos" / "video.md"
            document_note = vault / "Sources" / "Documents" / "document.md"
            update_indexes(
                vault,
                video_note,
                {
                    "title": "视频",
                    "task_key": "20260717-1",
                    "task_date": "2026-07-17",
                    "source_kind": "video",
                },
            )
            update_indexes(
                vault,
                document_note,
                {
                    "title": "文章",
                    "task_key": "20260718-1",
                    "task_date": "2026-07-18",
                    "source_kind": "document",
                },
            )
            self.assertTrue((vault / "Indexes" / "视频资料库.md").exists())
            self.assertTrue((vault / "Indexes" / "来源资料库.md").exists())
            self.assertEqual(
                (vault / "Indexes" / "Daily" / "2026-07-17.md").read_text(encoding="utf-8").splitlines()[0],
                "# 2026-07-17 视频笔记",
            )
            self.assertEqual(
                (vault / "Indexes" / "Daily" / "2026-07-18.md").read_text(encoding="utf-8").splitlines()[0],
                "# 2026-07-18 来源笔记",
            )

    def test_generic_web_downloads_allowlisted_image_type_into_document_frame(self):
        info = GenericWebInfo(
            url="https://example.com/article",
            source_id="0123456789abcdef0123",
            title="图文文章",
            author="作者",
            site_name="站点",
            published_at="",
            extraction_engine="readability-lxml",
            segments=(
                Segment(
                    "s000001", 0, 1, "图片前的正文内容。", locator_kind="document_order"
                ).to_dict(),
            ),
            images=(
                {
                    "url": "https://cdn.example.com/figure",
                    "after_segment_id": "s000001",
                    "order": 1,
                    "alt": "架构图",
                    "caption": "",
                },
            ),
        )
        with tempfile.TemporaryDirectory() as temp, patch(
            "vtm_core.web._open_public_url",
            return_value=(b"png-bytes", "https://cdn.example.com/figure", "image/png", "utf-8"),
        ):
            frames = GenericWebSourceAdapter().download_images(info, Path(temp), limit=1)
            self.assertEqual(len(frames), 1)
            self.assertEqual(Path(frames[0].path).suffix, ".png")
            self.assertTrue(Path(frames[0].path).is_file())
            self.assertEqual(frames[0].media_kind, "source_image")
            self.assertEqual(frames[0].source_ids, ["s000001"])

    def test_generic_web_image_limit_backfills_failed_downloads_and_caps_at_sixty(self):
        images = tuple(
            {
                "url": f"https://cdn.example.com/figure-{index}.png",
                "after_segment_id": "s000001",
                "order": index,
                "alt": "",
                "caption": "",
            }
            for index in range(1, 63)
        )
        info = GenericWebInfo(
            url="https://example.com/article",
            source_id="0123456789abcdef0123",
            title="多图文章",
            author="作者",
            site_name="站点",
            published_at="",
            extraction_engine="readability-lxml",
            segments=(
                Segment(
                    "s000001", 0, 1, "图片前的正文内容。", locator_kind="document_order"
                ).to_dict(),
            ),
            images=images,
        )
        successful = (b"png", "https://cdn.example.com/figure.png", "image/png", "utf-8")
        with tempfile.TemporaryDirectory() as temp, patch(
            "vtm_core.web._open_public_url",
            side_effect=[RuntimeError("first image unavailable")] + [successful] * 60,
        ) as downloader:
            frames = GenericWebSourceAdapter().download_images(info, Path(temp), limit=120)
        self.assertEqual(len(frames), 60)
        self.assertEqual(downloader.call_count, 61)
        self.assertEqual(frames[0].locator_label, "原文第 2 张图片")

    def test_generic_document_pipeline_reuses_four_pass_core_without_asr(self):
        info = GenericWebInfo(
            url="https://example.com/article",
            source_id="0123456789abcdef0123",
            title="公开网页测试",
            author="网页作者",
            site_name="示例站点",
            published_at="2026-07-17",
            extraction_engine="readability-lxml",
            segments=(
                Segment(
                    "s000001",
                    0,
                    1,
                    "完整网页正文包含观点、理由和具体操作步骤。",
                    locator_kind="document_order",
                ).to_dict(),
            ),
            images=(),
        )

        class FakeDocumentAdapter:
            platform = "generic_web"
            source_kind = "document"

            def can_handle(self, value):
                return True

            def normalize_input_url(self, value):
                return value

            def canonicalize_input(self, value):
                return value

            def source_id_from_url(self, value):
                return info.source_id

            def selector_from_url(self, value, explicit=None):
                return None

            def inspect(self, value, selector=None):
                return info

            def reference(self, inspected):
                return SourceReference(
                    self.platform,
                    self.source_kind,
                    inspected.source_id,
                    inspected.url,
                    inspected.title,
                    inspected.author,
                )

            def restore_info(self, metadata):
                return info

            def metadata(self, inspected):
                payload = inspected.to_dict()
                payload.update(self.reference(inspected).to_dict())
                return payload

            def content_segments(self, inspected):
                return [Segment(**item) for item in inspected.segments], {
                    "source": "public_html_document",
                    "locator_kind": "document_order",
                }

            def download_images(self, inspected, assets_dir, *, limit=60):
                return []

            def context(self, inspected):
                return "来源类型：公开网页文章；标题：公开网页测试"

            def folder_marker(self, inspected):
                return "WEB-0123456789ab"

        adapter = FakeDocumentAdapter()
        editor = SequenceClient(direct_manuscript_responses("完整网页正文包含观点、理由和具体操作步骤。"))
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"VTM_STATE_DIR": str(Path(temp) / "state")}
        ), patch("vtm_core.pipeline.adapter_for", return_value=adapter), patch(
            "vtm_core.pipeline.text_client", return_value=editor
        ):
            result = run(
                Options(
                    url=info.url,
                    vault=Path(temp) / "vault",
                    no_visual=True,
                    task_key="20260717-2",
                    task_date="2026-07-17",
                )
            )
            note = Path(result["note"])
            rendered = note.read_text(encoding="utf-8")
        self.assertEqual(result["source_kind"], "document")
        self.assertEqual(result["transcript_source"], "public_html_document")
        self.assertIn("/Sources/Documents/", str(note))
        self.assertIn("tags: [source-manuscript, generic_web]", rendered)
        self.assertEqual(editor.calls, 4)


if __name__ == "__main__":
    unittest.main()
