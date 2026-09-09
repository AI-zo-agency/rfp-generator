# Tseng College (CSUN, RFP No. 2601)
Working Draft vs. Final Designed Version — Comparison Report (v5)

Prepared by: Ralph QC Review  
Date: September 2026  
Source: RFP No. 2601 — verified directly against the RFP

---

## Bottom line

Four required items did not survive the handoff from Ralph’s working draft to the final designed file:

1. **Section 9 — Accessibility is missing.** Explicitly disqualifying.
2. **Exhibit D — Bidder Declaration is blank.** Correct content was in the working draft.
3. **Exhibit J — Proposal Certification Form is blank.** Correct content was in the working draft.
4. **Exhibit L — Pricing has a new $1,500 error.** Working draft was $213,500 and reconciled. Designed file: line items $211,000, stated total $209,500.

Also fix:

- Exhibit K heading (references exist, but the heading is used for Experience 4.C)
- One contact email (`connect@zo.agency` vs `sonja@zo.agency`)
- Audience & Data Strategy line item currently describes PR / media relations

This is a **design handoff** problem, not a drafting problem. Nothing in the product checks that the designed file still matches the last approved draft. That check only happens when someone runs a comparison like this.

**Before submit:** restore Section 9; sign and attach D and J; put Exhibit L back on $213,500; fix the K heading, the line-item copy, and the email.

---

## 1. Disqualifying gaps

Confirmed against the RFP Table of Contents and Exhibits list.

| RFP item | RFP status | Working draft | Final design | Verdict |
|---|---|---|---|---|
| Section 9 — Vendor Accessibility (VPAT, Assessment, Roadmap) | Mandatory. Non-compliance disqualifies. | Fully built | Missing | Highest priority |
| Exhibit D — Bidder Declaration | Required | Present | Blank page | Required exhibit blank |
| Exhibit J — Proposal Certification | Required | Present | Blank page | Draft content not carried forward |
| Exhibit K — References Form | Required | Own section | Heading used for Experience 4.C | References exist, wrong heading |
| Exhibit L — Pricing | Required | $213,500, math correct | $1,500 discrepancy | New error in design |

What design *did* improve: resumes, Innovation Process (once, not twice), Idaho / Benedictine case studies, Cover Letter, Exceptions.

---

## 2. Ralph vs Claude — three layers, not two

Treating this as “Ralph vs Claude” makes QC look missing. A QC stack already runs inside Generate and Complete Scan. It never sees InDesign.

| Layer | Status | What it does | What it does not do |
|---|---|---|---|
| 1. Ralph drafting | Shipped | Writes the working draft. Flags gaps instead of inventing. First-pass budget math usually adds up. Fits the page limit. | Does not produce the designed PDF. |
| 2. In-product Claude QC | Shipped | Runs on Generate, Complete Scan, and chat save. Fact-check, fabrication gates, forms, insurance, personnel, budget-to-ledger, presubmit. | Does not see the designed PDF. |
| 3. This review | Manual | Independent fact re-check. Draft vs designed comparison. Team write-ups. | Only runs when someone asks. |

The Accessibility copy, Exhibit J language, and $213,500 budget existed in Layer 1 and were checked by Layer 2. They died in design. Layer 3 is the only comparison, and it is on-request only.

---

## 3. Automation status (no module list)

| Status | Count | Meaning |
|---|---|---|
| Shipped | 28 | Core draft + in-product QC is live |
| In progress | 9 | Being tightened now (Go/No-Go fairness, empty tabs, verify-after-edit, outline, budget speed) |
| Not built | 5 | All after drafting: design-to-draft check, comparison reports, submission log, VPAT package, Scan quality gate re-attach |

**67% shipped. The five missing pieces are all handoff / after-draft.**

What is still open in product: confirm a chat/scan fix actually landed; score each section against that RFP’s criteria; stop treating rubric labels as extra tabs; keep empty form pages from collapsing; keep designed budgets from drifting off the ledger.

What is not built: designed-file vs approved-draft check; win/loss log (Sonja); a VPAT package that cannot be dropped in layout.

---

## 4. Team requests

### Done
Rev 6 voice. Stop restating the RFP. One fee table (not a conflicting percentage mix). Pricing Guide verbatim terms. Case studies must match the KB. Full cover letters. Clean section titles. No invented people or “Compliant” insurance without evidence. Voice-align must not rewrite the Cost tab. Over-ceiling bids get a pricing flag. Flag gaps instead of fabricating.

### In progress
Verify-after-edit (Gilroy reported “done” while old text was still in the draft). Per-section eval scoring. Go/No-Go crediting real comparable work (Alameda). Clean titles on first generate. Empty form tabs. Budget-pass speed. No keyword/regex matching.

### Still pending — product
Design-to-draft check. Automated draft-vs-design reports. Submission log (Sonja). VPAT package. Ralph filling the Google Slides Master Template. Simpler one-action UI. Self-serve KB upload.

### Still pending — people
Rock the Locks / Deschutes Fair / Rogue X metrics (Sonja, Rachel). TEDx / Deschutes Brewery sponsorship studies (Sonja). Current COI in the KB. Signed Tseng Exhibits D and J. One contact email.

---

## 5. Where team feedback conflicts with the product

These are two valid asks that cannot both be followed literally.

**Budget look vs budget math.** Design compressed Tseng to $211,000 / $209,500. The approved draft was $213,500. Restore the designed file to the ledger. Do not ask Ralph to invent a total that matches the compressed layout.

**“I don’t trust budgets” vs “math is usually right.”** Both can be true. Addition is usually right on the *first draft*. Trust breaks on *why* the mix/hours, and on later chat/design changes. Do not say budgets are solved.

**Delete Investment Framing vs use it verbatim.** Delete the *percentage table*. Keep the *verbatim paragraphs* (Investment Framing, Scope Protection, Reimbursable Expenses).

**Cover everything under the cap vs never fake rates.** Cover the real scope when the Pricing Guide allows. If it cannot fit, flag Sonja. Do not reverse-engineer a number that fits.

**Make it cheaper to match design vs rate card.** Moving $213,500 toward $209,500 just to match InDesign is the same fake-rate problem. Change the designed file, unless Sonja changes scope.

**Fun / energy vs Rev 6.** Energy comes from specific ideas and real case studies, not hype or exclamation points.

**Go/No-Go should have scored higher vs don’t credit unverified work.** Credit what is in the KB. Do not count work that was never uploaded. Alameda retrieval is being fixed; more case studies and self-serve upload are still required.

---

## 6. What Ralph already does well

Across Gilroy, CNM, Orland Park, Pasadena, Tseng, North Miami Beach, Saint Paul College, Dane County:

- Flags MANUAL FILL instead of guessing
- Uses real KB facts in the large majority of cases
- First-pass budget tables usually add up (Tseng’s error appeared in design)
- Writes to the specific RFP (Pasadena interview prep, Tseng data framing, Gilroy “steward, don’t rebuild”)
- Names a weak case-study fit instead of stretching it

---

## 7. Blockers

| Blocker | Type | Owner |
|---|---|---|
| No design-to-draft check | Product | Engineering |
| Signed exhibits are human PDFs | Process | Sonja + Design |
| No VPAT package that survives layout | Product | Engineering |
| Current COI not in KB | Human | Sonja / Ops |
| Missing festival / sponsorship proof | Human | Sonja + Rachel |
| Eval scorecard not on Complete Scan | Product | Engineering |
| Designed budget can drift off the ledger | Product + process | Engineering + Design |
| No self-serve KB upload | Product | Engineering |
| Word → designer is still the path | Product | Engineering |

---

## How to read this

Ralph can write a proposal. In-product QC already runs. This report is not that QC.

The live questions:

1. Does the designed file still have every required section and exhibit from the approved draft?
2. Did anyone change the budget after the ledger was approved?
3. Are the remaining asks (design check, submission log, KB upload, Master Template, VPAT, verify-after-edit) scheduled?

Until (1) is automatic, every designed proposal needs a punch list like this before it goes out.

---

*RFP No. 2601; ZO-AGENCY as of 9 September 2026. Team-request status: Sonja / Rachel / QC this session (Gilroy, Utah Tech / Dane County 8 Sep, Alameda 9 Sep, Tseng).*
