"""Regional Transit Authority mobility outreach proposal, as generated. Held-out fixture.

Different client and mix from voice_llm_proposal.py: a cover letter, a cost table, and
federal certification forms. Text is verbatim from the stored draft, including its defects.
Write-mode sections only. (register, title, body).
"""

SECTIONS: list[tuple[str, str, str]] = [('cover_letter',
  'Cover Letter',
  '[DESIGNER NOTE: Attach the authorized signature page / wet-ink signature PDF required by the '
  'RFP. Do not invent signature dates, titles, or notary numbers, use only the real signed buyer '
  'form.]\n'
  '\n'
  "The three outcomes you've prioritized,building rider awareness, strengthening driver "
  "recruitment, and establishing RTA's long-term storytelling capacity,are interconnected "
  'strategic imperatives that determine transit system viability. We bring proven expertise in '
  'accessible, community-centered communications to address each one, and we recognize the urgency '
  'of moving these initiatives forward now. This partnership will equip RTA with both immediate '
  'campaign results and the internal capabilities to sustain momentum beyond this engagement.\n'
  '\n'
  "We structure this engagement as a professional services contract aligned with RTA's Purchase of "
  'Transit Service Contract terms, positioning our scope, deliverables, and reporting cadence to '
  'integrate seamlessly with your operations. From contract start, our team will synchronize '
  "milestones directly to RTA's grant reporting windows and implementation timelines, eliminating "
  'coordination friction and ensuring every deliverable advances your funding objectives. We take '
  'on the operational complexity,timeline management, compliance documentation, stakeholder '
  "coordination,so RTA's internal resources remain focused on service delivery and rider "
  'experience.\n'
  '\n'
  'Certification Statement\n'
  'zö agency holds WBENC and WOSB certifications.\n'
  '\n'
  '/Participation Statement\n'
  "zö agency's certifications on file are WBENC (Women's Business Enterprise National Council) and "
  'WOSB (Women-Owned Small Business). [VERIFY: status/number, if applicable, or confirmation that '
  "WBENC/WOSB satisfies RTA's participation reporting requirement]. We are prepared to complete "
  'any utilization forms or good-faith-effort documentation RTA requires as part of contract '
  'execution. [VERIFY: subcontractor/ participation goal, not established in current scope]\n'
  '\n'
  'Respondent Information\n'
  '\n'
  '| Field | Response |\n'
  '|---|---|\n'
  '| Firm Name | zö agency |\n'
  '| Address | [VERIFY: current business address for cover letter] |\n'
  '| Telephone | [VERIFY: main office phone number] |\n'
  '| Fax | [VERIFY: fax number, if applicable, or N/A] |\n'
  '| Email | [VERIFY: primary proposal contact email] |\n'
  '\n'
  "We're ready to partner with the Regional Transit Authority to bring this vision to life. Let's "
  "schedule a conversation with your evaluation committee,we're prepared to dive into strategy, "
  "timeline, and next steps immediately. Reach out, and let's get started.\n"
  '\n'
  'Sincerely\n'
  '\n'
  'Sonja A. [VERIFY: title of authorized signer]\n'
  'zö agency\n'
  '\n'
  '[DESIGNER NOTE: reserve signature block space for wet or digital signature by authorized '
  'principal/partner; attach signed PDF separately per submission instructions]'),
 ('narrative',
  'Cost Information',
  '## Proposed Investment\n'
  '\n'
  '**Professional fees: $103,850**\n'
  '**Direct travel / reimbursables: $4,500**\n'
  '**Total proposed investment: $108,350**\n'
  'zö agency structures fees as transparent project phases aligned to the scope outlined in this '
  'proposal.\n'
  '\n'
  'Cost information is provided to support contract negotiation only and is not a scored factor in '
  'this evaluation. Fee phases: Discovery & Assessment ($16,500), Strategy Development ($20,000), '
  'Materials & Website Content ($37,400), Community Outreach & Partner Engagement ($4,500), '
  'Implementation & Evaluation Support ($6,500), Year 2 Refinement ($7,500), Account & Project '
  'Management ($11,450), and Travel / Reimbursables ($4,500).\n'
  '\n'
  'The not-to-exceed figure for this engagement is the proposed professional fee total above, '
  '$108,350 (professional fees only; travel/reimbursables of $4,500 are additive per the Fee '
  'Detail table). Should RTA request an oral presentation or interview as part of the selection '
  'process, zö agency will attend at its own expense; no additional fee is proposed for interview '
  'participation.\n'
  '\n'
  '## Terms\n'
  '\n'
  '### Investment Framing\n'
  '\n'
  '- zö agency works on a project-based fee schedule.\n'
  '- We provide the cost for the project and we abide by those terms as does our client.\n'
  '- When we are your partner, we work together to assess priorities and the overarching needs, '
  'timelines, and message so that the budget is wisely allocated to the highest-priority '
  'objectives.\n'
  '- The following pricing represents estimates based on current information.\n'
  '- Each engagement is scoped in detail at kickoff to reflect final requirements.\n'
  '\n'
  '### Cost Disclosures & Assumptions\n'
  '\n'
  '- zö agency acknowledges that no reimbursement will be sought or made for any costs incurred '
  "prior to full execution of a contract and issuance of RTA's written notice to proceed; all fees "
  'in this proposal are contingent on contract execution and notice to commence services.\n'
  '- zö agency acknowledges it is solely responsible for all costs of preparing this proposal and '
  'participating in this solicitation; no proposal-preparation costs are included in the fees '
  'above.\n'
  '- zö agency acknowledges and agrees that cost and contract terms for this engagement will '
  "comply with RTA's Purchase of Transit Service Contract terms and conditions, as referenced in "
  'our cover letter.\n'
  '- Assumption: the fees above assume zö agency bears the cost of procuring and maintaining '
  "insurance coverage meeting or exceeding RTA's minimum requirements (General Liability, Auto "
  'Liability, Uninsured/Underinsured Motorist, Medical Payments, Vehicle Comprehensive/Collision, '
  'and Umbrella coverage as specified in the RFP); these insurance costs are borne by zö agency '
  'and are not billed separately to RTA.\n'
  '\n'
  '### Scope Protection\n'
  '\n'
  '- Pricing reflects the scope as understood at proposal stage.\n'
  '- As discovery progresses and priorities sharpen, we will refine specific deliverables with '
  'your team.\n'
  '- Any material change in scope will be documented in a scope addendum before work proceeds.\n'
  '\n'
  '### Reimbursable Expenses\n'
  '\n'
  '- The following expenses will be billed at cost with prior approval: travel (mileage at current '
  'IRS rate, lodging, meals); photography/videography location fees and permits; specialized '
  'software licenses required for project-specific needs; stock photography/video licensing beyond '
  'standard subscriptions.\n'
  '\n'
  '### Revision Rounds\n'
  '\n'
  '- Standard engagement includes three rounds of review and revision on creative deliverables.\n'
  '- Additional rounds available at scope addendum.\n'
  '\n'
  '### Subcontractor Cost Treatment\n'
  '\n'
  'zö agency does not plan to use subcontractors for the services described herein without the '
  "State's prior written consent. If consent is granted, any approved subcontractor costs are "
  'absorbed within the professional phase fees above unless a written scope addendum states '
  'otherwise, no separate subcontractor markup is proposed here.\n'
  '\n'
  '## Fee Detail by Phase\n'
  '\n'
  '| Phase | Scope | Fee |\n'
  '| --- | --- | ---: |\n'
  '| Discovery & Assessment | Phase 1: Discovery & Assessment: review of existing passenger '
  'information, outreach materials, website content, branding, and driver recruitment materials; '
  'stakeholder interviews across staff, riders, and community partners; accessibility review (RFP '
  '§II.B.2). Phase 1: Brand/Communications Audit: audit of existing branding, materials, and '
  'communication practices (RFP §II.B.2). | $16,500 |\n'
  '| Strategy Development | Phase 2: Outreach and Communications Strategy: priority audiences, key '
  'messages, outreach methods, partner list, implementation sequence (RFP §II.B.3). Phase 2: '
  'Driver Recruitment Strategy: review of current recruitment process and messaging, '
  'recommendations for paid/volunteer driver visibility (RFP §II.B.4). Phase 2: Measurement & '
  'Evaluation Framework built before launch to support ridership/recruitment reporting and grant '
  'deadlines (RFP §II.B.1, II.A). | $20,000 |\n'
  '| Materials & Website Content | Phase 3: Messaging & Branding Guide for MOVE Initiative, '
  'including audience-specific language across print, digital, web, social, outreach, partner '
  'materials (RFP §II.B.5). Phase 3: Plain-language rider and first-time-rider education '
  'print/digital materials, ADA/Section 504 accessibility QA gate (RFP §II.B.5). Phase 3: '
  'Instructional video content: how to book/ride/prepare for pickup, plus driver recruitment video '
  '(RFP §II.B.5). Phase 3: Professional photography of RTA system for outreach, website, '
  'recruitment, and educational materials (RFP §II.B.5). Plus 2 more. | $37,400 |\n'
  '| Community Outreach & Partner Engagement | Phase 4: Community outreach coordination, partner '
  'referral tools, joint outreach activities with senior centers, workforce agencies, healthcare '
  'providers, social services, and educational institutions (RFP §II.B.7). | $4,500 |\n'
  '| Implementation & Evaluation Support | Phase 5: Implementation rollout support, benchmark '
  'tracking, and grant-reporting inputs through Month 8 (RFP §II.B.1; RFP schedule substantial '
  'implementation by July 2027). | $6,500 |\n'
  '| Year 2 Refinement | Phase 6: Year 2 Refinement: materials/tools refresh from performance '
  'data, staff training and transition through Month 24 (RFP §II.A annual review/refinement '
  'requirement). | $7,500 |\n'
  '| Account & Project Management | Six-phase project management, bi-weekly working sessions, '
  'monthly status reporting, and phase-gate sign-off tracking across the two-year term (RFP D.6, '
  'Project Management and Scheduling Expertise). | $11,450 |\n'
  '| Travel / Reimbursables | Travel for stakeholder interviews, community outreach site visits, '
  'and photography/video production across Delaware, Dubuque, and Jackson counties, IA (billed at '
  'cost per Reimbursable Expenses terms). | $4,500 |\n'
  '| **Total** | | **$108,350** |'),
 ('narrative',
  'Project Management and Scheduling Expertise',
  "Six phases, six sign-offs. That's the control structure we'll run for the MOVE Initiative: no "
  'phase opens until RTA has approved the one before it in writing, and no deliverable moves to '
  'print or publish until it clears our accessibility QA gate.\n'
  '\n'
  '**Phase-gate schedule**\n'
  '\n'
  '| Phase | Key activity | Gate before next phase | Target |\n'
  '|---|---|---|---|\n'
  '| Discovery & Assessment | Materials/website audit, stakeholder interviews, accessibility '
  'review | RTA sign-off on findings summary | Week 6 |\n'
  '| Strategy Development | Outreach strategy, driver recruitment plan, measurement framework | '
  'RTA approval of strategy document | Week 12 |\n'
  '| Materials & Website Content | Plain-language, ADA/Section 504 materials and web copy | '
  'Accessibility QA gate, then RTA review | Week 20-26 |\n'
  '| Community Outreach & Partner Engagement | Joint outreach activities, partner referral tools | '
  'Feedback log reviewed jointly | Week 28 |\n'
  '| Implementation & Evaluation Support | Rollout, benchmark tracking, grant-reporting inputs | '
  'Mid-point performance review | Through Month 8 (Jul 2027) |\n'
  '| Year 2 Refinement | Materials/tools refresh from performance data, staff training/transition '
  '| Final deliverable acceptance | Through Month 24 |\n'
  '\n'
  "Ella Lindau, Operations Director, runs this schedule day to day. She'll track every deliverable "
  'against the gate dates above in a shared project tracker RTA can view at any time, flag risk to '
  'a gate at least two weeks out, and bring a recovery option to the bi-weekly working session.\n'
  '\n'
  '**Reporting cadence**\n'
  '\n'
  '| Cadence | What RTA gets |\n'
  '|---|---|\n'
  '| Bi-weekly working sessions | Open items, draft review, decisions needed from RTA |\n'
  '| Monthly status report | Educational materials developed and distributed; '
  'website/passenger-information improvements completed; outreach activities, presentations, and '
  'demonstrations completed; community organizations and referral partners engaged; feedback '
  'received from riders, partners, and staff; common questions and barriers identified |\n'
  '| Quarterly performance review | Rides by location, service area, trip purpose, and user type; '
  'changes in rider inquiries, first-time rider participation, partner referrals, and driver '
  'recruitment inquiries/applications over time |\n'
  '\n'
  "The risk we're naming up front: a two-year public-sector schedule slips when review cycles "
  "stall on one person's calendar. We handle that by building sign-off into the gate dates "
  'themselves. If a gate is at risk, Ella brings the fix to that next session, with an adjusted '
  'date and what caused the slip, before it compounds into the next phase.\n'
  '\n'
  'The July 2027 substantial-implementation milestone sits inside the Implementation & Evaluation '
  'Support phase, timed off the Materials/Website Finalization and Community Outreach Launch gates '
  'that precede it, so grant-related reporting inputs are ready when RTA needs them.'),
 ('narrative',
  'Insurance Requirements',
  "We'll carry the insurance coverage this contract requires for the full two-year term, and we'll "
  'name our carrier, policy numbers, and limits on the certificate of insurance we provide before '
  'contract execution.\n'
  '\n'
  '**Coverage acknowledgment**\n'
  '\n'
  '| Coverage type | Our commitment |\n'
  '|---|---|\n'
  "| Commercial General Liability | We'll maintain coverage at the limits RTA specifies and name "
  'RTA as additional insured where required. |\n'
  "| Automobile Liability | We'll maintain coverage at the limits RTA specifies, covering any "
  'vehicle use tied to outreach or site visits. |\n'
  "| Workers' Compensation | We'll maintain statutory coverage for all zö staff assigned to this "
  'engagement. |\n'
  "| Professional Liability (Errors & Omissions) | We'll maintain coverage appropriate to the "
  'strategy, content, and accessibility-review work in this scope. |\n'
  "| Umbrella/Excess Liability | We'll add excess coverage if RTA's underlying limits require it "
  'to meet the stated minimum. |\n'
  '\n'
  "Our current limits are [VERIFY: limit from current COI]. We'll confirm each figure against "
  "RTA's exact minimums once those are provided in the final contract terms, and we'll submit an "
  'updated certificate of insurance reflecting any adjustment needed before work begins. RTA will '
  "be added as additional insured and certificate holder on the applicable policies, and we'll "
  "provide 30 days' notice of cancellation or material change through our carrier, consistent with "
  'standard public-sector transit contract terms.\n'
  '\n'
  "If a required limit exceeds what we currently carry, we'll bind the difference before contract "
  "execution. We won't claim compliance with a limit we haven't verified on our current policy."),
 ('narrative',
  'Reservations and Special Conditions',
  "We've reviewed the special conditions governing this contract and take no exceptions.\n"
  '\n'
  '**Special conditions acknowledgment**\n'
  '\n'
  '| Condition | Our position |\n'
  '|---|---|\n'
  "| Usage reporting on request | We'll furnish a usage report in RTA's approved format, "
  'disclosing activity by contract item, whenever requested. |\n'
  '| Background checks | We understand that zö staff, and any subcontractors we bring onto this '
  "engagement, may need to pass background checks before working with RTA. We'll cooperate fully "
  "and coordinate scheduling so it doesn't slow the project timeline. |\n"
  "| Invoices and payments | We'll submit detailed invoices tied to specific work performed, and "
  "we'll confirm remittance and delivery details with our financial institution so payments post "
  "cleanly on RTA's side. |\n"
  "| Applicable taxes | We'll determine and include any taxes that apply to this engagement in our "
  "pricing. We understand tax treatment isn't a factor in award and won't ask RTA to adjust "
  'pricing after submission based on tax questions. |\n'
  "| Post-award meeting | We're available to meet with RTA's procurement officer after award to "
  "walk through contract terms and confirm how we'll work together. |\n"
  "| Cooperative purchasing (SAVE / ICPAs) | We're open to extending this contract's terms to "
  "other qualifying governmental entities under RTA's cooperative purchasing agreements, subject "
  'to our review and written concurrence on each request. |\n'
  '\n'
  "We don't anticipate any conflict between these conditions and our staffing plan, our insurance "
  "commitments, or the phase-gate schedule we've outlined elsewhere in this proposal. If a "
  "condition changes materially between now and contract execution, we'll flag it to RTA in "
  'writing before signing.\n'
  '\n'
  '[DESIGNER NOTE: render as single acknowledgment table, no additional visual treatment needed]'),
 ('narrative',
  'Hold Harmless / Indemnification Clause',
  'We agree to this term.\n'
  '\n'
  '**Indemnification acknowledgment**\n'
  '\n'
  '| Term | Our commitment |\n'
  '|---|---|\n'
  "| Indemnify, defend, and hold harmless | We'll indemnify, save, and hold harmless RTA, its "
  'officers, employees, and agents from claims, damages, losses, and expenses arising from our '
  'acts, errors, or omissions in performing this contract. |\n'
  "| Legal defense costs | We'll cover reasonable attorney's fees and defense costs tied to claims "
  'covered by this agreement. |\n'
  '| Scope of coverage | This commitment applies to work performed by zö agency staff and any '
  'subcontractor we bring onto this engagement. |\n'
  '| Survival | We understand this obligation survives contract termination for claims arising '
  'from work performed during the term. |\n'
  '\n'
  "We'll sign RTA's indemnification and hold-harmless language as written, without redline, as "
  "part of contract execution. Where RTA's legal counsel requires specific insertion of contract "
  "number, effective dates, or governing law citation, we'll complete those fields at execution "
  "using RTA's final contract template.\n"
  '\n'
  "[VERIFY: RTA's exact hold-harmless clause number/contract template language for execution]"),
 ('procurement',
  'Attachment A – Federal, State and Local Requirements',
  'zö agency acknowledges and will comply with the federal, state, and local requirements '
  'applicable to this contract as a condition of award and performance.\n'
  '\n'
  '**Compliance acknowledgment**\n'
  '\n'
  '| Requirement Area | Vendor Acknowledgement |\n'
  '|---|---|\n'
  '| Federal funding conditions (FTA) | zö agency will comply with all FTA terms and conditions '
  'applicable to this contract, including flow-down clauses required of subrecipients and '
  'third-party contractors. |\n'
  '| Civil Rights Act of 1964, Title VI | zö agency will not discriminate on the basis of race, '
  'color, or national origin in the performance of this contract, as addressed in Attachment D. |\n'
  '| ADA / Section 504 | zö agency will comply with ADA and Section 504 accessibility requirements '
  'in all materials, website content, and services delivered under this scope. |\n'
  "| Program | zö agency acknowledges RTA's program requirements and will comply with applicable "
  'reporting and non-discrimination provisions, as addressed in Attachment B/D. |\n'
  '| Debarment and Suspension | zö agency certifies it is not presently debarred, suspended, or '
  'proposed for debarment from participation in this transaction by any federal department or '
  'agency. |\n'
  "| Lobbying Certification | zö agency's certification regarding lobbying is provided in "
  'Attachment B. |\n'
  '| Iowa state procurement and licensing requirements | zö agency will comply with all applicable '
  'Iowa state statutes governing professional services contracts of this type, including '
  'registration and tax withholding obligations. |\n'
  '| Local (Delaware, Dubuque, Jackson County) ordinances | zö agency will comply with any '
  'applicable county or municipal ordinances governing vendor conduct, background checks, and '
  'public records during performance of this contract. |\n'
  '| Drug-Free Workplace | zö agency maintains a drug-free workplace policy applicable to all '
  'staff assigned to this engagement. |\n'
  '| Energy Policy and Conservation Act | zö agency will comply with mandatory standards and '
  'policies relating to energy efficiency where applicable to this scope of work. |\n'
  '| Access to Records | zö agency will provide RTA, the U.S. DOT Secretary, and the Comptroller '
  'General access to any books, documents, papers, and records related to this contract for the '
  'purpose of audit and examination. |\n'
  '| Federal Changes clause | zö agency will comply with all applicable FTA regulations, policies, '
  'and directives, including future amendments, without requiring separate amendment of this '
  'contract, unless the amendment affects contract cost or scope. |\n'
  '\n'
  '[VERIFY: confirm any additional Attachment A clauses specific to this RTA solicitation not '
  'listed in the excerpt provided]'),
 ('procurement',
  'Attachment B – Lobbying Certification',
  '**Certification Regarding Lobbying**\n'
  '\n'
  'zö agency certifies, to the best of its knowledge and belief, the following, as required for '
  'this federally assisted contract:\n'
  '\n'
  '| Certification Item | Vendor Response |\n'
  '|---|---|\n'
  '| No federal appropriated funds have been paid or will be paid by or on behalf of the Vendor to '
  'any person for influencing or attempting to influence an officer or employee of any federal '
  'agency, a Member of Congress, an officer or employee of Congress, or an employee of a Member of '
  'Congress in connection with the awarding of this federal contract, the making of any federal '
  'grant, the making of any federal loan, the entering into of any cooperative agreement, or the '
  'extension, continuation, renewal, amendment, or modification of any federal contract, grant, '
  'loan, or cooperative agreement. | Certified |\n'
  '| If any funds other than federal appropriated funds have been paid or will be paid to any '
  'person for influencing or attempting to influence an officer or employee of any federal agency, '
  'a Member of Congress, an officer or employee of Congress, or an employee of a Member of '
  'Congress in connection with this federal contract, grant, loan, or cooperative agreement, the '
  'Vendor shall complete and submit Standard Form-LLL, "Disclosure Form to Report Lobbying," in '
  'accordance with its instructions. | Not applicable; no such payments have been made |\n'
  '| This certification is a material representation of fact upon which reliance was placed when '
  'this transaction was made or entered into. Submission of this certification is a prerequisite '
  'for making or entering into this transaction imposed by 31 U.S.C. § 1352. Any person who fails '
  'to file the required certification shall be subject to a civil penalty of not less than $10,000 '
  'and not more than $100,000 for each such failure. | Acknowledged |\n'
  '\n'
  'The Vendor certifies that it will require that this certification be included in the award '
  'documents for all subawards at all tiers (including subcontracts, subgrants, and contracts '
  'under grants, loans, and cooperative agreements) and that all subrecipients shall certify and '
  'disclose accordingly.\n'
  '\n'
  '| Field | Response |\n'
  '|---|---|\n'
  '| Vendor Name | zö agency |\n'
  '| Authorized Signatory | [VERIFY: signatory name and title for execution] |\n'
  '| Signature | [DESIGNER NOTE: attach signed PDF] |\n'
  '| Date | [VERIFY: execution date] |\n'
  '\n'
  'zö agency has no lobbying activity to disclose in connection with this procurement and has not '
  'engaged, and does not intend to engage, any registered lobbyist in connection with this '
  'contract.'),
 ('procurement',
  'Attachment C – Debarment and Suspension Certification',
  '**Attachment C - Certification Regarding Debarment, Suspension, and Other Responsibility '
  'Matters (Nonprocurement)**\n'
  '\n'
  'zö agency certifies, to the best of its knowledge and belief, that it and its principals meet '
  'the certification standard required for this attachment:\n'
  '\n'
  '| Certification Item | Vendor Response |\n'
  '|---|---|\n'
  '| Debarment or suspension | zö agency and its principals are not presently debarred, suspended, '
  'proposed for debarment, declared ineligible, or voluntarily excluded from covered transactions '
  'by any federal department or agency. |\n'
  '| Prior three-year history | Within the preceding three years, zö agency and its principals '
  'have not been convicted of or had a civil judgment rendered against them for commission of '
  'fraud, a criminal offense in connection with obtaining, attempting to obtain, or performing a '
  'public (federal, state, or local) transaction or contract; violation of federal or state '
  'antitrust statutes; or commission of embezzlement, theft, forgery, bribery, falsification or '
  'destruction of records, making false statements, or receiving stolen property. |\n'
  '| Pending proceedings | zö agency and its principals are not presently indicted for, or '
  'otherwise criminally or civilly charged by a governmental entity with, commission of any of the '
  'offenses listed above. |\n'
  '| Prior terminations | zö agency and its principals have not had one or more public '
  'transactions (federal, state, or local) terminated for cause or default within the preceding '
  'three years. |\n'
  '| Subcontractor flow-down | zö agency will require any subcontractor engaged on this contract '
  'to certify to the same standard before performing work funded under this agreement. |\n'
  '\n'
  'zö agency understands this certification is a material representation of fact relied upon by '
  'RTA in making this award, and that a false certification is a criminal offense subject to '
  'remedies including suspension, debarment, and other appropriate action. zö agency will disclose '
  'promptly to RTA if it learns, at any time during the performance of this contract, that this '
  'certification was erroneous when submitted or has become erroneous due to changed '
  'circumstances.\n'
  '\n'
  'zö agency further certifies that where it is unable to certify to any of the statements above, '
  'it will attach an explanation to this proposal. No such explanation is required at this time.\n'
  '\n'
  '**Signature Block**\n'
  '\n'
  '| Field | Response |\n'
  '|---|---|\n'
  '| Authorized Signature | [DESIGNER NOTE: attach signed PDF] |\n'
  '| Printed Name | [MANUAL FILL: Sonja, signatory name] |\n'
  '| Title | [MANUAL FILL: Sonja, signatory title] |\n'
  '| Date | [MANUAL FILL: Sonja, signature date] |'),
 ('procurement',
  'Attachment D – Federal Clauses Acknowledgement',
  '**Attachment D - Federal Clauses Acknowledgement**\n'
  '\n'
  'zö agency acknowledges and agrees to comply with the federal clauses applicable to this '
  'contract as a condition of award and performance.\n'
  '\n'
  '| Federal Clause | Vendor Acknowledgement |\n'
  '|---|---|\n'
  '| Civil Rights Act of 1964, Title VI | zö agency will comply with Title VI and will not '
  'discriminate on the basis of race, color, or national origin in the performance of this '
  'contract. |\n'
  '| Americans with Disabilities Act (ADA) / Section 504 | zö agency will comply with ADA and '
  'Section 504 requirements in all deliverables and services provided under this contract. |\n'
  "| Program | zö agency acknowledges RTA's program requirements and will comply with applicable "
  'reporting and non-discrimination provisions. |\n'
  '| Debarment and Suspension | zö agency certifies it is not presently debarred, suspended, '
  'proposed for debarment, or declared ineligible from participating in federally assisted '
  'contracts by any federal department or agency. |\n'
  "| Byrd Anti-Lobbying Amendment | zö agency's lobbying certification is addressed in Attachment "
  'B of this proposal. |\n'
  '| Access to Records and Reports | zö agency will provide RTA, the FTA Administrator, the U.S. '
  'DOT Inspector General, and the Comptroller General of the United States, or any of their '
  'authorized representatives, access to any books, documents, papers, and records that are '
  'directly pertinent to this contract for the purposes of making audits, examinations, excerpts, '
  'and transcriptions. |\n'
  '| Federal Changes | zö agency will comply with all applicable FTA regulations, policies, '
  'procedures, and directives, including any amendments made during the term of this contract, to '
  'the extent they are applicable. |\n'
  '| Energy Conservation Requirements | zö agency will comply with mandatory standards and '
  'policies relating to energy efficiency in the applicable state energy conservation plan issued '
  'in compliance with the Energy Policy and Conservation Act. |\n'
  '| Clean Air Act / Clean Water Act | zö agency will comply with all applicable standards, '
  'orders, and regulations issued pursuant to the Clean Air Act and the Federal Water Pollution '
  'Control Act. |\n'
  '| No Federal Government Obligation to Third Parties | RTA and zö agency acknowledge that the '
  'federal government is not a party to this contract and is not subject to any obligations or '
  'liabilities to any third party in connection with this contract. |\n'
  '| Program Fraud and False or Fraudulent Statements | zö agency acknowledges that it is subject '
  'to the Program Fraud Civil Remedies Act of 1986 and related federal civil fraud, statute, and '
  'civil penalty provisions applicable to its actions in connection with this contract. |\n'
  "| Termination | zö agency acknowledges RTA's right to terminate this contract in accordance "
  'with the termination provisions required in FTA-assisted third-party contracts. |\n'
  '| Government-wide Debarment and Suspension (Nonprocurement) | zö agency certifies that neither '
  'it nor its principals are presently debarred, suspended, proposed for debarment, declared '
  'ineligible, or voluntarily excluded from participation in this transaction by any federal '
  'department or agency. |\n'
  '\n'
  'zö agency will execute Attachment D as a signed certification at the time of contract '
  'execution, consistent with the certifications provided in Attachment B of this proposal. '
  "[DESIGNER NOTE: format as a signature-ready acknowledgement page matching Attachment D's "
  'official layout, with signature/date/title fields at the bottom.]'),
 ('narrative',
  'Conflict of Interest Disclosure Statement',
  '**Conflict of Interest Disclosure**\n'
  '\n'
  "zö agency, its principals, and the staff we're assigning to this engagement, Ella Lindau and "
  'Sonja Anderson, Agency Director, business relationship, family relationship, or other '
  'affiliation with RTA, its board members, or its staff that would create an actual or apparent '
  'conflict of interest in performing this contract.\n'
  '\n'
  '**Disclosure statement**\n'
  '\n'
  '| Item | Response |\n'
  '|---|---|\n'
  '| Financial interest in RTA or its board/staff | None to disclose |\n'
  '| Family or business relationship with RTA decision-makers | None to disclose |\n'
  '| Prior or current contracts with RTA | None to disclose |\n'
  '| Subcontractor or partner conflicts | None to disclose |\n'
  "| Ongoing obligation | We'll disclose in writing, within five business days, any conflict that "
  'arises during the two-year term. |\n'
  '\n'
  "We'll sign RTA's own conflict-of-interest disclosure form as part of contract execution, using "
  "the identical facts stated above. If RTA's procurement office identifies a relationship we "
  "haven't captured here, we'll respond in writing within five business days of notice.\n"
  '\n'
  '[VERIFY: Sonja, confirm no conflicts before signature, including any prior zö work for RTA '
  'board members or Delaware/Dubuque/Jackson County officials]'),
 ('narrative',
  'F.2 — Responsiveness to Project Scope',
  "We answer each Scope of Services item below with what we'll build and how it works for RTA "
  'riders, drivers, and staff.\n'
  '\n'
  '**II.B.1, Outreach and Communications Strategy Development**\n'
  "We'll build the strategy from stakeholder interviews across RTA staff, riders, and community "
  'partners, mapping each audience to the barriers that keep them from riding or referring others. '
  'The strategy document names sequencing, channels, and message hierarchy by county, and it goes '
  'to RTA for written approval before any material gets drafted.\n'
  '\n'
  "We'll separate paid-driver and volunteer-driver funnels, since the barriers differ. Paid "
  'recruitment gets job-board-ready messaging and a referral incentive framework; volunteer '
  'recruitment gets community-partner-channel messaging built for senior centers, faith groups, '
  'and civic organizations. Both funnels get a shared intake and follow-up sequence so RTA can '
  'track inquiries through to applications.\n'
  '\n'
  '**II.B.3, Messaging, Branding, and Educational Materials**\n'
  "We'll produce plain-language materials in print, digital, and social formats, built as editable "
  'templates RTA staff can update after our contract ends. Every asset carries consistent visual '
  'identity across the three counties so a rider sees the same brand on a printed schedule, a '
  'driver flyer, and a website page.\n'
  '\n'
  '**II.B.4, Website and Passenger Information Review**\n'
  "We'll audit the current site and passenger-facing content for navigation gaps and outdated "
  'information, then rewrite the content in plain language and restructure information '
  'architecture so fare, schedule, and service-area details answer the questions RTA staff field '
  'most often by phone.\n'
  '\n'
  '**II.B.5, Community Outreach and Partner Engagement**\n'
  "We'll coordinate joint outreach activities, presentations, and demonstrations with priority "
  "referral partners, and log every session's feedback so recurring questions and access barriers "
  'surface in writing.\n'
  '\n'
  '**II.B.6, Performance Measurement Framework**\n'
  'Our framework tracks materials developed and distributed, website and passenger-information '
  'improvements completed, outreach activities and demonstrations completed, partner organizations '
  'engaged, and feedback received from riders, partners, and staff. It also tracks ridership by '
  'location, service area, trip purpose, and user type, and changes over time in rider inquiries, '
  'first-time rider participation, partner referrals, and driver applicant activity, benchmarked '
  'against the discovery-phase baseline.\n'
  '\n'
  '**II.B.7, Photography and Video Production**\n'
  "We'll produce photography and short video assets depicting real riders, drivers, and routes for "
  'use across print, web, and social materials, licensed to RTA for ongoing use beyond the '
  'contract term.\n'
  '\n'
  '**II.B.8, ADA/Section 504 Accessibility Review**\n'
  'Every material clears an accessibility QA gate covering alt text, captions, contrast, and '
  'plain-language readability before it moves to print or publish, and the website content review '
  'applies the same standard to digital passenger information.\n'
  '\n'
  "Accessibility and measurement aren't a closing step in this scope; both run through every item "
  'above so what we build in month one still reports clean data in month twenty-four.'),
 ('narrative',
  'Evaluation Schedule',
  '**F.3 Schedule**\n'
  '\n'
  '**Milestone timeline**\n'
  '\n'
  '| Milestone | Timing from contract start |\n'
  '|---|---|\n'
  '| Contract execution | Week 0 |\n'
  '| Assessment findings sign-off | Week 6 |\n'
  '| Strategy and measurement framework approval | Week 12 |\n'
  '| Materials draft review | Week 20 |\n'
  '| Materials and website finalization | Week 26 |\n'
  '| Community outreach launch | Week 28 |\n'
  '| Substantial implementation | Month 8 (July 2027) |\n'
  '| Year 2 refinement and final delivery | Month 24 (December 2028) |\n'
  '\n'
  "We built this sequence backward from July 2027. That date carries RTA's grant-related reporting "
  'obligations, so our materials and outreach launch land by week 28, giving RTA a full reporting '
  "cycle before the deadline. If a stakeholder round runs long during discovery, we'll compress "
  'the materials draft review window.\n'
  '\n'
  "We'll run bi-weekly working sessions with RTA staff through every active phase, so schedule "
  'risk gets caught at the two-week mark. Monthly status reporting keeps the record current '
  "between sessions, and we'll add a quarterly performance review once implementation starts, "
  'checking outreach and recruitment metrics against the benchmarks set in the strategy phase.\n'
  '\n'
  "The eighteen months between implementation and contract close aren't idle time. We'll spend "
  'them refining materials and outreach tactics against real ridership and recruitment data, then '
  'hand RTA a finished, editable materials package and a trained staff team well before the '
  'December 2028 end date, with no scramble at close-out.\n'
  '\n'
  "We understand schedule is one factor among several in this decision, and we've built ours to be "
  "conservative on the date that matters most to RTA's grant reporting while staying flexible on "
  'everything upstream of it.'),
 ('narrative',
  'Negotiated Contract Evaluation Requirements',
  'F.5 Other\n'
  '\n'
  "We'll make ourselves available for a presentation if RTA invites us to one after preliminary "
  'scoring. Ella Lindau, Operations Director who will run this engagement day to day, and Sonja '
  "Anderson, Agency Director, will both attend, and we'll bring the working team members RTA wants "
  'in the room to answer questions on materials, accessibility, or measurement.\n'
  '\n'
  "One piece of value we haven't placed anywhere else in this response: we treat the six "
  'phase-gate sign-offs in our schedule as open checkpoints for RTA to redirect scope. If a '
  "partner conversation during Community Outreach surfaces a barrier we didn't anticipate in "
  "Discovery, we'll bring it back to the strategy document and adjust before materials get "
  'locked,.\n'
  '\n'
  "We're a WBENC- and WOSB-certified firm, a status we haven't leaned on elsewhere in this "
  "proposal because It's simply true, and RTA may weigh it as it sees fit.")]
