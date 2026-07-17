# Golden manuscript quality

The accepted manuscript was produced with four distinct editorial decisions. Keep these decisions separate in code and prompts.

## 1. Understand before writing

Read the complete timed transcript and identify what the speaker actually covers. Do not write final prose from arbitrary token batches. Batch boundaries exist only for bounded extraction; they are not article sections.

## 2. Build a complete fact inventory

Convert speech into chronological information units. Preserve claims, reasons, explanations, examples, steps, names, titles, commands, numbers, conditions, warnings, and conclusions. Merge repeated wording into one unit. Mark only genuine filler, false starts, promotional requests, and fully duplicated speech as removable.

Example: repeated phrases about opening Codex and entering a prompt become one operational unit containing the entry point, the prompt's purpose, relevant options, and the expected output—not several transcript sentences and not a one-line summary.

## 3. Organize by the video's real structure

Plan headings from topics or workflow stages. Then write each section from its assigned information units. Natural prose may compress several spoken sentences into one paragraph, but every meaningful detail remains. Lists are for real steps or enumerations only.

Do not add a second editor voice. Phrases such as “价值在于”“这说明”“适合用来” are unsupported unless the speaker said them.

## 4. Treat frames as evidence

Compare selected frames with the matching spoken passage. Fully transcribe a simple list, table, formula, or short code block when confidence and completeness are high; then the redundant image may be removed. Keep dense prompts, complex interfaces, diagrams, paper figures, partial OCR, and visually meaningful demonstrations. Put retained images immediately after the passage they support. Screen-only facts use a `画面补充` callout and are never attributed to the speaker.

Use a new section only for a real topic, argument, or workflow-stage transition. A short overview, input definition, or route map followed immediately by expansion of the same object stays in one section as separate paragraphs or genuine step subheadings; avoid adjacent overview/detail headings at the same semantic level. For a progressive PPT, whiteboard, UI, diagram, or chart, prefer a later completed state only when evidence classification proves it is a stronger same-template replacement. Do not mechanically choose the last frame, and preserve equal-strength stages, before/after comparisons, and distinct visuals.

Judge information gain, not visual attractiveness. A programmer cartoon, presenter avatar, watermark, repeated subtitle/title, or simple label-and-icon slide is decorative when the matching prose already carries the full meaning. Remove it. A long model-written vision description does not turn a simple frame into dense evidence. Keep a diagram only when its spatial relationships, original figure, interface state, or dense content adds something prose cannot preserve. Conversely, do not discard a second same-template slide before reading it: a one-line change such as “不存在……” can reverse the conclusion. Requested text evidence uses stricter perceptual deduplication and is removed only after its information has been compared.

Vision cost remains bounded after semantic planning: local scene/OCR selection may consider many frames cheaply, but paid visual review is capped at about two distinct candidates per minute (minimum six, hard maximum sixty). A short low/medium-density text or list screen becomes text-only evidence after a high-confidence transcription; a dense or partially unreadable prompt keeps its screenshot.

Do not spend that budget chronologically. First inspect the endpoints of narrow ASR-suspect ranges, because two same-template cards can split one technical sentence and its negation. Then distribute the remaining fixed budget across ordinary transcript-grounded requests.

## Acceptance check

A reader should be able to reconstruct the video's useful content and operational details without watching it, while not having to read filler, repetition, or chronological subtitle narration. The result must look like an edited creator manuscript: concrete headings, coherent paragraphs, exact details, inline evidence, and no invented interpretation.

Give DeepSeek the complete timed transcript, not arbitrary token batches. First request only a concrete article and natural-paragraph plan with must-keep details plus bounded visual time-range requests. Inspect those requested frames with Qwen before prose writing. If the plan flags ASR suspects or visual evidence is available, use one bounded reconciliation call that returns only source-grounded term/sentence corrections plus 1–4 minimal semantic anchors; it does not write prose or change structure. The same call may check an unflagged uppercase phonetic token of at least four characters when exact same-timestamp screen text conflicts with it; unsupported candidates remain unresolved. The anchors preserve exact terms, technical conclusions, and polarity without forcing the final editor to repeat the entire repaired sentence verbatim. Deterministically attach every planned paragraph's complete local subtitle range as `source_excerpt`, so details omitted by the planner remain visible. Next give the transcript, evidence-augmented plan, correction map, visual evidence, and the bundled golden-style example to a document writer. Give a later pass the transcript, evidence-augmented plan, correction map, visual evidence, and draft to restore details compressed by the structural write without duplicating details already present. Give a final pass the transcript, correction map, visual evidence, golden-style example, and detail-complete draft to correct remaining ASR residue, attribution, repetition, and transcript-like phrasing without deleting independent detail. The four whole-document editorial decisions remain unchanged; the correction map is a small evidence-preparation pass.

The golden-style reference teaches prose density, not facts. Prefer direct verbs, one purpose per paragraph, usually two to five sentences, and one occurrence of each conclusion. Preserve every distinct reason, example, setting, number, condition, and limitation, but do not restate it in a section preview and again in a closing summary.

The article plan is a structural aid, not a source of facts. When ASR splits one sentence across adjacent fragments, reconstruct the fragments jointly and preserve negation, limitation, and comparison direction. High-confidence visible spelling may correct a phonetic English error. When neither speech context nor screen evidence supports an exact repair, use a conservative higher-level phrase instead of inventing a precise or opposite claim. Conversely, if the speaker already delivers a coherent, compact, written-quality paragraph, leaving much of its wording intact is acceptable; never rewrite merely to maximize edit distance.

For ordinary videos that fit comfortably in the configured model context and output limit, keep all four passes whole-document. For a genuinely long video, do not cut at fixed time or token intervals. The first pass must still read the complete transcript and produce one global plan. Only the writing and detail passes may work chapter by chapter using the plan's natural boundaries, with the complete plan and neighboring context attached; the final pass then reconciles the assembled document globally. This fallback exists for context/output limits, not as the default architecture.

Do not split semantic judgment among a mechanical information-unit gate, a writing model, several critics, an adjudicator, and a separate proofreader. That architecture makes models argue about garbled surface forms instead of writing. DeepSeek owns semantic decisions; Python owns only response shape, chronological source-to-paragraph ranges, checkpointing, task operations, media, and storage. An uncertain ASR term may be generalized conservatively, but its surrounding source-supported context, case, question, reason, and conclusion remain publishable information.

Every subtitle remains assigned to one chronological paragraph range for time/image alignment, but filler is not forced into prose. A source-provided product name, interface label, or abbreviation does not need external official verification merely because the model is unfamiliar with it. Genuine incoherent ASR fragments should become a conservative source-supported phrase rather than an invented exact term.
