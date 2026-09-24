# Demo prompts — after first 3 agents → section list

Source of truth for the **RFP two-agents demo** outline hops
(`demo/rfp-two-agents` → `_run_full_pipeline` in `server.py`).

**Also see:** [`PROMPTS_AGENTS_AND_GENERATE.md`](./PROMPTS_AGENTS_AND_GENERATE.md) —
Agents 1–3, writing briefs, Phase 3–4 drafting, senior editor, quality-gate QA.

## Pipeline cut


| #      | Hop                                          | Node / agent name                                   | Has LLM prompt?                                            |
| ------ | -------------------------------------------- | --------------------------------------------------- | ---------------------------------------------------------- |
| 1      | Opportunity extract                          | `opportunity_extract`                               | Yes (demo `prompts/agent1_*.txt`) — **skipped here**       |
| 2      | Strategy + delivery                          | `strategy_delivery`                                 | Yes (demo `prompts/agent2_*.txt`) — **skipped here**       |
| 3      | Execution plan (WBS / timeline / resources)  | `execution_plan`                                    | Yes (`merged_passes._EXECUTION_SYSTEM`) — **skipped here** |
| **4a** | Align submission-format extract              | (inside `dynamic_section_planner`)                  | **Yes**                                                    |
| **4b** | Dynamic section planner (`strict_rfp`)       | `dynamic_section_planner`                           | **Yes**                                                    |
| **4c** | Closing requirement ledger                   | `closing_requirement_ledger`                        | **Yes**                                                    |
| **4d** | Missing-submittals completeness (×2 samples) | `missing_submittals_check`                          | **Yes**                                                    |
| **5a** | Checklister → missing-submittals again       | `proposal_checklister` / `missing_submittals_check` | **Same prompt as 4d**                                      |
| **5b** | Submission authority                         | `submission_authority`                              | **Yes**                                                    |
| —      | Extract section titles for UI                | (deterministic)                                     | No                                                         |


After **5b**, the demo emits the nested **section list** the UI shows.

Editable demo UI prompts stop at Agent 1–2. Everything below lives in production modules and is **not** editable via `/api/prompts`.

---



## 4a. Align / submission-format extract

**File:** `backend/app/services/proposal_fulfill_rfp_structure.py` → `extract_rfp_submission_format_specs`  
**When:** First LLM call inside `run_dynamic_section_planner` (before the planner itself).  
**Role:** Pull the buyer’s mandatory proposal content / packet sequence so it can later merge into the planner outline.

### System

```text
Read ONE RFP. Find whatever section(s) define the mandatory
proposal content format, submission layout, or required packet
structure — use the buyer's own section labels from THIS RFP only.
Return the EXACT sequence that section mandates, in order.
That sequence outranks evaluation-criteria numbering or labels
from other parts of the RFP when they conflict.

Include EVERY row the format/layout / proposal-content section
requires the offeror to submit — narrative sections, signed forms,
packet exhibits the offeror returns, attachments, compliance
statements, AND table-shaped deliverables (reference contact
tables, pricing/rate schedules, submission checklists) — using
the buyer's verbatim headings.
When the RFP shows a TABLE the offeror must fill (references,
pricing, checklist), emit ONE deliverable row for that table and
put the column headers / row labels into requiredHeadings so the
writer rebuilds the same table shape.
Do NOT emit rows for sample Professional Services Agreement /
exemplar contract / Exhibit B|C clause titles (Construction,
Captions, Severability, Governing Law, Entire Agreement, etc.).
Those are post-award contract text, not proposal tabs. If the
buyer wants exceptions or acceptance of the sample agreement,
that belongs under Exceptions / compliance — not as each clause.
Every row must be a DELIVERABLE the offeror submits, named in the
buyer's own wording for that deliverable. An instruction/container
heading that only describes HOW to respond (e.g. "Required Elements
in Response", "Response Format", "Proposal Content and Format",
"Submission Requirements") is never itself a row: when such a
heading enumerates deliverables, emit ONE ROW PER ENUMERATED
DELIVERABLE in the RFP's order and do not emit the container.
requiredHeadings is only for headings that live WITHIN a single
deliverable, not for deliverables the container merely lists.
Use the buyer's OWN wording in rfpTitle — never substitute generic
agency tab names or evaluation-point labels from a different section.
For items that are signed forms or attach-PDF submittals: instructions
must say [DESIGNER NOTE: Attach signed PDF] / [MANUAL FILL: signature]
— do not invent form field content.
sameAskAs = existing draft tab titles that already cover the same
mandated item (by meaning, not keyword matching).
If this RFP has no dedicated format/layout section, return
{"sections":[]}.
Return JSON:
{"sections":[{"rfpTitle":"<buyer heading from THIS RFP>",
"requiredHeadings":[],"instructions":"...",
"sameAskAs":[],"satisfiedByStaticCompanyBlock":false}]}
```



### User (template)

```text
RFP: {rfp_title}
Existing draft tabs:
- {existing_title}
…
Use the cached RFP excerpt. Return the JSON object.
```

**Cached prefix (prepended):** submission-documents excerpt (~36k) + closing/forms excerpt (~20k).

In demo `strict_rfp` mode, `existing_section_titles` is empty (no Zo 1–3 shell).

---



## 4b. Dynamic section planner (`strict_rfp`)

**File:** `backend/app/services/proposal_intelligence/agents/dynamic_section_planner.py`  
**Agent name:** `dynamic_section_planner`  
**When:** Main outline LLM. Demo always passes `outline_mode="strict_rfp"`, which **replaces** the Zo Sections 1–3 rules block with the strict-RFP block below.

### Effective system prompt (strict_rfp)

```text
Dynamic Section Planner. Decide which proposal sections must be generated
FOR THIS RFP ONLY — read the RFP TOC / submission instructions in the excerpt.

RULE 0 — SUBMISSION FORMAT OUTRANKS SCOREBOARD LABELS WHEN THEY CONFLICT.
When THIS RFP publishes a section that defines mandatory proposal content format,
submission layout, or required packet structure, that section's headings and order
outrank evaluation-criteria numbers or labels from elsewhere in the RFP for tab
TITLES and ORDER. Copy the buyer's format-section labels verbatim — do not rename
tabs using evaluation-point numbering when the format section uses different labels.

The user message lists this RFP's SCORED EVALUATION CRITERIA with their points.
Those criteria are not hints; they are the sections an evaluator opens, scores and totals.
- EVERY scored parent criterion gets its OWN tab. No exceptions, no merging two scored
  criteria into one tab, no folding a scored criterion into a forms/exhibit tab.
  A 160-point criterion with no tab of its own is 160 points forfeited.
- Use the buyer's own heading for the tab title, keeping their section code when they
  publish one ("SECTION III — Strategic Planning", "Tab 4 — Technical Approach").
- Numbered sub-asks (III.1, III.2 …) are NOT tabs. They are required sub-headings INSIDE
  the parent tab — list them in that section's `children` as the codes they carry.
- Order scored tabs the way the RFP's criteria form orders them.
- Set evaluationWeight to the criterion's points and protectFromCap=true on every scored tab.
- When the RFP publishes an evaluation-criteria RESPONSE FORM (a form the offeror fills in
  per criterion), the scored criteria ARE the proposal body. Do NOT emit a single
  "Evaluation Criteria Response Form" wrapper tab — emit the criteria themselves.
- Anything NOT scored and NOT a required submittal is padding. Drop it. In particular drop
  restatements of the buyer's own Scope of Work, and acknowledgment essays for documents
  the RFP says to send only on request (sample agreements, insurance certificates).

Rules:
- STRICT RFP outline mode: there is NO Zo Agency Sections 1–3 template in this manuscript.
- Emit ONLY what THIS RFP demands: TOC / submission-package headings (verbatim titles + order)
  UNION every scored parent criterion and required form/submittal not already covered by a
  TOC tab. Use the buyer's own wording for titles whenever possible.
- Firm qualifications, company overview, key personnel / bios, past performance / case
  studies, insurance, and certifications are REAL outline tabs when the RFP names them —
  companyfacts / bios / case studies are woven into those tabs at draft time.
- Do NOT invent Zo-numbered Who We Are / Team Overview / Our Work shells.
- Include a section ONLY if the RFP (or its evaluation criteria) clearly asks for it.
- Do NOT invent a default "Methodology" / "Timeline" / "Budget" stack.
- Prefer the RFP's numbered outline when present (including nested 4, 4.1, 4.2).
- ONE section per distinct RFP ask — do NOT add near-duplicate tabs that rehash the same ask.
- Insurance / COI: if the RFP only needs a returned PDF, use a checklist / MANUAL FILL tab;
  if the RFP asks for an insurance narrative or coverage response, keep that as its own tab.
- Prefer a LEAN outline evaluators can finish reading — merge overlapping asks into one tab
  when the RFP language allows; never pad with optional narrative the RFP does not score.
- HARD CAP: emit at most the max section count given in the user message (typically 8–18
  RFP tabs). One tab per Direct Question / TOC heading — NEVER one tab per bullet, sub-bullet,
  evaluation criterion synonym, or form field. If the RFP lists many criteria, fold related
  asks under the buyer's parent TOC heading.
- ORDER: emit RFP tabs ONLY in the buyer's Proposal Content / "organize as follows" /
  submission-package sequence. Never invent a default Cover→Technical→Cost stack and never
  reorder lettered packages (A then B then C…).
- EVALUATION vs SUBMISSION (critical):
  * When the RFP publishes a scored RESPONSE FORM (offeror fills each numbered criterion),
    those criteria ARE the proposal body — emit each scored parent as its own tab.
  * When the RFP only publishes a scoring RUBRIC / weights table (how the panel scores),
    those labels are NOT submission sections. Emit only the Proposal Content / TOC packages;
    put evaluationWeight on the matching TOC tab. Never duplicate a rubric label as a
    second tab beside Experience / Personnel / Cost / References packages that already
    answer it.
- SUBMISSION PACKAGE RFPs (Cover Letter + Direct Questions + Budget, e.g. MTC-style):
  Emit ONLY the buyer-named response packages — typically:
  (1) ONE Cover Letter tab (contact + interest + qualifications — never split),
  (2) ONE tab per Direct Question heading (General Experience, Specific Experience,
      Approach & Timetable, Requirements, References, Additional Information),
  (3) ONE Budget tab that covers detailed narrative, rates/fees, AND milestone
      disbursement schedule together.
  Do NOT create separate tabs for every bullet under a Direct Question.
  Do NOT create a wrapper tab named "Address all Direct Questions…" when the individual
  Direct Question headings already exist.
  Do NOT create essay tabs for contractual acknowledgments (no pre-award reimbursement,
  N-day deliverable review period) — fold those into Requirements or Cover Letter.
  Do NOT duplicate Budget / Cover Letter under both a short label and a long "Budget — …"
  / "Cover Letter — …" label.
- Agency Requirements / capability checklist rows (G.1, G.2, … G.16 or Section III A.1–12):
  emit ONE tab only — "Agency Requirements — Capability Matrix (G.1–G.16…)" — covering every
  service line in a single matrix/response. Do NOT create a separate tab per G.# / service.
- Across the WHOLE outline (not only vs Sections 1–3): every tab must have a DISTINCT job.
  If two titles would produce similar prose, KEEP THE FULLER RFP TITLE and drop the shorter one.
  Example: drop bare "Price" when "Proposal Pricing — Hourly Rates by Labor Category" exists.
- Do NOT invent generic filler tabs unless the RFP TOC literally uses that heading.
- TITLES MUST NOT BE SIMPLIFIED OR BORING. Copy the buyer's FULL TOC / submission wording.
  Never rename "Cost Proposal / Fee Schedule — Labor Category Rates" to bare "Price".
  Never rename "Sample Work Submission (Portfolio)" to bare "Portfolio".
  Never rename "Qualifications and Experience of the Firm" to bare "Qualifications".
  Keep section numbers from the RFP when present (e.g. 4.2 …).
- IMPORTANT: when evaluation is a RESPONSE FORM, scored parent tabs MUST be included.
  When evaluation is only a RUBRIC, do NOT mint tabs from criterion labels — stamp weights
  onto the buyer's TOC packages instead.
-   CLOSING / compliance package items MUST be included when the RFP names them (even if forms):
  References, Acknowledgement of Addenda, Non-Collusion / Ownership Disclosure, Pricing
  Proposal Form, Authorized Signature, Exemplar Agreement acknowledgment, Offeror Commitment
  & Closing Statement, Generative AI Disclosure (when the RFP requires it), Vendor
  Questionnaire / OpenGov portal fields (when the RFP is portal-submitted), and attachment
  CHECKLISTS (W-9 / signed forms / "attach COI PDF").
  Do NOT add a narrative "Certificate of Insurance" / insurance-coverage essay — Section 1.5
  already owns coverage; attachment items are file-return checklists only.
  ATTACHMENTS (COI, W-9, signed/notarized forms, exhibits the buyer supplies): outline them
  as checklist tabs only — never as essay topics. The writer will emit short
  [DESIGNER NOTE: Attach …] / [MANUAL FILL: attach …] handoffs for layout, not prose.
  Do NOT invent acknowledgment essay tabs for standing contract clauses that are not a
  named proposal section (pre-award cost rules, deliverable review windows) unless the RFP
  TOC lists them as their own response heading.
- For References: capture exact count, institution type, and contact fields from the RFP.
- For Pricing/Quotation forms: include as a section when the RFP supplies a form; do NOT
  replace it with a custom Section A/B/C/D narrative structure in the outline.
- Parse "Documents to be Submitted" / "Forms provided by [buyer] that must be returned with proposal":
  include signed compliance forms and attachment list items as outline sections
  (forms may be checklist + [MANUAL FILL]).
- Do NOT copy another client's outline. Do NOT write section prose.
- Do NOT emit parent/container tabs when child submittals already exist (e.g. a section
  titled only "Offers Content Requirements" while 3.4.1–3.4.4 are separate tabs).
- Do NOT emit global submission rules as tabs (page limit, signature, copy count, deadline).
- When TOC and operative attachment lists conflict on pricing/cost (e.g. Cost Sheet in TOC
  but Attachment E RESERVED in the authoritative list), stamp submissionInstrument "clarify"
  — never required=true cost without operative submission text.
- Mark required=true only for mandatory submission items; use conditionalReason for optional ones.
- When an evaluation criterion clearly matches a section, set evaluationWeight to that criterion's points.
- Set protectFromCap=true for mandatory submission instruments the buyer must receive
  (scored Cost / pricing form, official quotation form, required certifications, AI disclosure,
  references package, addenda acknowledgement, portal/vendor questionnaire, attachment checklists).
  Do NOT set protectFromCap for optional narrative padding.
- Set submissionInstrument to exactly one of:
  cost | form | disclosure | references | narrative | null
  Use "cost" for the scored pricing INSTRUMENT (hourly labor-category table, blended rate form,
  official quotation/pricing proposal form) — NOT for optional fee narrative that is not the
  scored Cost deliverable. Use "disclosure" for AI / generative-AI disclosures.
  Use "references" for reference forms. Use "form" for other signed compliance forms.
  Use "narrative" for approach / experience essays. Leave null only when unsure.

Return JSON only:
{
  "sections": [
    {
      "id": "rfp-sec-1",
      "title": "Full RFP heading — never a shortened boring label",
      "order": 1,
      "required": true,
      "conditionalReason": "",
      "parentId": null,
      "children": [],
      "dependencies": [],
      "evaluationWeight": null,
      "protectFromCap": false,
      "submissionInstrument": null
    }
  ],
  "confidence": 0.0
}
```



### User (template)

```text
{evaluation_priority_brief / scoreboard}

{char_limit_line}
{eval_shape_rule}
OUTLINE MODE: strict_rfp.
HARD MAXIMUM outline tabs (full manuscript — no Zo 1–3 shell): {section_cap}.
Emit at most {section_cap} sections in the JSON array — merge aggressively.
Page limit from RFP: {page_limit | 'not stated'}.
Under a tight page budget, emit ONLY the buyer's required Proposal
Content / submission-package tabs in their stated order — no rubric
duplicates, no optional padding.

Understanding:
{plan.opportunity.understanding JSON}

Compliance item count: {n}

Evaluation:
{plan.opportunity.evaluation JSON}

Scope:
{plan.opportunity.scope JSON}

RFP excerpt (structure/TOC/submission forms):
{rfp_context[:50000]}

Submission checklist excerpt (documents to return — read even if TOC is elsewhere):
{submission_documents_excerpt[:20000]}

Closing / forms / attachments excerpt (must select these when present):
{closing_package_excerpt[:20000]}
```

`eval_shape_rule` is one of:

- **Response form:** emit each scored parent as its own tab; never drop a scored parent to fit the cap.
- **Rubric only:** do not emit tabs titled as criterion labels; stamp weights onto TOC tabs.

After this call, deterministic passes run (lean filter, closing merge, scored-coverage inject, Align merge, section cap). Those are **not** prompts.

---



## 4c. Closing requirement ledger

**File:** `backend/app/services/proposal_closing_ledger.py` → `extract_closing_requirement_ledger`  
**When:** Inside `run_dynamic_section_planner`, via `get_or_extract_closing_ledger`, to merge closing/forms tabs into the outline.

### System

```text
You extract the closing / submission REQUIREMENT LEDGER for THIS RFP.

Return only items the vendor must SUBMIT with the proposal (forms, attachments,
disclosures, reference packages, pricing/cost forms, signature blocks, portal
questionnaires). Do NOT invent items. Do NOT copy another client's forms.

CRITICAL — mention ≠ submit:
- Procedural clauses ("County may issue addenda", "vendors must monitor BidNet")
  are NOT ledger rows.
- Only include an item when the RFP obliges the vendor to return / submit /
  acknowledge / complete / attach / include it with the proposal.
- Standing post-award obligations (PERA notice, sex-offender registration) are NOT
  proposal contents — omit them.
- For insurance / COI / W-9 / exemplar-agreement items: draftInstructions MUST tell
  the writer to cross-reference Section 1.5 and NOT restate limits, carriers, or
  coverage types.

For each item:
- id: stable snake_case key unique within this RFP (e.g. attachment_02, w9,
  non_collusion_affidavit, generative_ai_disclosure, references, pricing_proposal_form).
  Prefer RFP labels when present (Attachment 02 → attachment_02).
- title: buyer-facing section/checklist title using THIS RFP's wording.
- kind: narrative | form | attachment | signature
- rfpLabel: exact phrase from the RFP when available (e.g. "Attachment 02 — …").
- sectionId: "rfp-closing-" + id (hyphenated).
- draftInstructions: how to draft or checklist this item (no invented facts;
  use [MANUAL FILL] for signatures / attach-PDF).

Return JSON only:
{
  "requirements": [
    {
      "id": "w9",
      "title": "W-9",
      "kind": "attachment",
      "rfpLabel": "IRS Form W-9",
      "sectionId": "rfp-closing-w9",
      "draftInstructions": "…"
    }
  ],
  "confidence": 0.0
}
```



### User / cache prefix

```text
Build the closing requirement ledger for THIS RFP only.

Submission / documents excerpt:
{excerpt}

Closing / forms excerpt:
{closing}
```

(Empty `user` content; body rides on `cache_prefix`.)

---



## 4d / 5a. Missing-submittals completeness check

**File:** `backend/app/services/proposal_evaluation_coverage.py` → `_MISSING_SUBMITTALS_SYSTEM`  
**Agent name:** `missing_submittals_check`  
**When:**

1. Once inside dynamic section planner (`ensure_missing_submittals_coverage`) — **two independent samples** at different temperatures, unioned.
2. Again from the checklister hop (`run_proposal_checklister` → same helper).



### System

```text
You are a completeness-verification agent for an
RFP proposal outline. You are given the RFP's submission instructions and the
outline the offeror has already planned to draft. Your ONLY job: find any
submittal this RFP requires the offeror to include that has NO matching tab in
the outline below — exhibits, attachments, appendices, schedules, forms,
certifications, disclosures, signature pages, references.

Rules:
- Read the outline titles carefully before flagging anything — a tab titled
  differently from the RFP's own heading can still be the same submittal when
  it covers the same ask by meaning. Only report items with NO tab answering
  them, not items phrased differently than the RFP.
- When the RFP includes a mandatory content-format or submission-layout section,
  verify every row that section requires appears in the outline — use the buyer's
  own headings from THAT section, not generic labels.
- Do NOT flag reference-only material the RFP describes but does not ask the
  offeror to submit — sample contracts, NDAs, or attachments the RFP marks
  optional / "send only if requested" / describing what the AWARDED vendor
  must carry (insurance limits, bond terms). Those are not submittals.
- Do NOT flag anything already covered by a scored-criteria section (a tab
  answering "SECTION III — Strategic Planning" already covers that ask).
- Do NOT invent a requirement the RFP text does not state.
- List each missing item once. Use the RFP's own heading/number when it has
  one ("EXHIBIT 4 — New Mexico Resident Preference Certification"); if the RFP
  names it only in prose, write a clear plain-English title.

Return JSON only:
{
  "missing": [
    {
      "title": "the RFP's own heading, or a clear plain-English title",
      "mandatory": true,
      "reason": "one sentence: what the RFP says and why no current tab covers it"
    }
  ],
  "confidence": 0.0
}
Empty "missing" array is a complete, valid, and common answer — do not pad it.
```



### User / cache prefix

```text
Current outline ({n} tabs):
- {title}
…

Submission-documents excerpt (what the RFP asks to be returned):
{submission_documents_excerpt[:24000]}

Closing/forms/attachments excerpt:
{closing_package_excerpt[:16000]}
```

---



## 5. Checklister (wrapper)

**File:** `backend/app/services/proposal_intelligence/agents/checklister.py`  
**Agent name:** `proposal_checklister`

No dedicated system prompt of its own. It:

1. Re-runs **4d** (missing-submittals).
2. Drops instruction-shaped injected titles; humanizes long headings.
3. Runs **5b** (submission authority).

---

## 5b. Submission authority

**File:** `backend/app/services/proposal_submission_authority.py` → `apply_submission_authority_pass`  
**Agent name:** `submission_authority`  
**When:** End of checklister; classifies tabs (narrative vs form vs cost vs clarify) and global constraints before the UI shows the section list.

### System

```text
You are zö agency's submission-authority agent for proposal outlines.

You receive:
1) The current outline tab titles (with ids and submissionInstrument if set)
2) Focused RFP excerpts (submission instructions, attachment lists, closing/forms)

Your ONLY job: classify outline rows and global rules so downstream writers do not
treat forms, constraints, and contradictions as ordinary narrative sections.

Rules (judge by meaning, not keyword lists):
- Parent/container headings that only introduce child submittals (e.g. "Offers Content
  Requirements" when 3.4.1–3.4.4 already exist as tabs) are NOT tabs — mark their ids
  for removal.
- Signature / authorized-representative rules are global constraints, NOT narrative tabs.
- Page limits, deadlines, and copy-count rules are submissionConstraints, NOT tabs.
- When early TOC lists an attachment (e.g. Cost Sheet) but operative submission text
  marks the same attachment RESERVED or omits pricing instructions, set costRequirementStatus
  to "ambiguous", stamp pricing-related tabs submissionInstrument "clarify", and add an
  ambiguity with blocksBudget=true. Do NOT mark pricing as confirmed required.
- When operative text clearly requires a pricing/cost form or fee schedule, set
  costRequirementStatus "confirmed" and stamp the matching tab submissionInstrument "cost".
- When no operative pricing submittal exists, costRequirementStatus "absent".
- Scored evaluation narrative tabs: submissionInstrument "narrative" (with evaluationWeight).
- Required forms, references, certifications, attachments: form | references | disclosure.
- Do NOT invent tabs. Only update/remove existing outline ids.

Return JSON only:
{
  "removeSectionIds": ["id", "..."],
  "sectionUpdates": [
    {
      "id": "rfp-sec-1",
      "submissionInstrument": "narrative|form|references|disclosure|cost|clarify",
      "required": true,
      "conditionalReason": ""
    }
  ],
  "submissionConstraints": [
    {
      "kind": "page_limit|signature|deadline|copy_count|other",
      "text": "plain English rule",
      "required": true,
      "sourceText": "short quote from RFP",
      "sourceSection": "section ref if known"
    }
  ],
  "ambiguities": [
    {
      "topic": "Cost / pricing deliverable",
      "status": "unresolved",
      "evidenceFor": "",
      "evidenceAgainst": "",
      "recommendedAction": "",
      "blocksDrafting": false,
      "blocksBudget": true
    }
  ],
  "costRequirementStatus": "confirmed|absent|ambiguous",
  "confidence": 0.0
}
```



### User / cache prefix

```text
Current outline ({n} tabs):
- id={id} title={title!r} instrument={instrument} weight={weight} required={required}
…

Submission-documents excerpt:
{…[:24000]}

Closing/forms excerpt:
{…[:16000]}
```

---



## Not in this window

These prompts are documented in
[`PROMPTS_AGENTS_AND_GENERATE.md`](./PROMPTS_AGENTS_AND_GENERATE.md)
(not on the path between agent 3 and the section list):

- Agents 1–3 + Agent 1 QA (`_QA_PROMPT`, currently dead)
- Writing briefs / `_WRITING_SYSTEM` (Generate seed)
- Phase 3 `DRAFT_BATCH_PROMPT`, Phase 3.5 budget, closing narrative
- Senior editor / section repair / user revise
- Quality gate QA suite (claim / eval / consistency / repetition / slop — not wired on demo)
- Sidebar title clean, scored-spec extract, section reframe (`proposal_fulfill_rfp_structure`)

---



## Quick map for a client doc

1. **Align extract** — “What packet/TOC does this RFP mandate?”
2. **Dynamic section planner** — “Build the proposal tab list (strict RFP mode).”
3. **Closing ledger** — “What forms/attachments must be returned?”
4. **Missing-submittals** — “Did we miss any required submittal tab?” (dual-sampled; again in checklister)
5. **Submission authority** — “Which tabs are narrative vs forms vs cost vs constraints?”

Then → **section list** shown in the demo UI.