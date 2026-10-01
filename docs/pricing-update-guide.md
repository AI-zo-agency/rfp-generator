# How to update pricing in the RFP Writer

For the pricing team. No developer needed for a normal update.

The RFP Writer prices every budget from three pricing docs in the Knowledge Base. Change a price, add a catalog item or edit the wording by editing those docs, then upload them.

## The three docs

| Doc | What it holds | Who can see it |
|---|---|---|
| **Pricing Book** | Client prices for every catalog item, the hourly rates and fees, digital media fees, billing terms | Every agent (safe to quote) |
| **Rules and Wording** | How zö prices, standing rules, the approved wording blocks, the budget page guide | Every agent |
| **Pricing Internal** | Settings (53% floor, 3.7 target, $275 rate), people's loaded rates, vendors, the cost behind every catalog price, the task library, the pricing rules for the budget AI | Only the pricing engine. It is hidden from every other agent. |

The three docs are a set. They must all carry the same **Version**.

## To update prices

1. Open the three current docs (`.md` files). Edit them in any text editor.
2. Keep every heading and every table column name exactly as written. The app reads prices from those names. Add or change rows freely.
3. Change the **Version** line in the table at the top of **all three** docs (for example `v2` to `v3`). Update **Effective** and **Valid through** too.
4. Upload each doc: Knowledge Base, **Add document**, **Document type = Pricing**, choose the file, **Upload**. Upload one at a time. Do not type a title or a note; the app fills in the title.
5. Read the message after each upload:
   - **Pricing v3 is live** means all three are in and every budget from now on uses v3.
   - **Still needed for v3: …** means upload the ones it names. Budgets keep using the current version until then.
   - **Not uploaded** followed by numbered reasons means a heading, column or number is wrong. Fix exactly what it names and upload again. Nothing was saved.
6. When v3 is live, delete the older docs (v2) from the Knowledge Base page so the agents see one Pricing Book. The message tells you when.

## Rules the app checks

- The Settings table needs: Margin floor, Target multiplier, Blended rate, Minimum price per in-house hour, Nonprofit discount, Traditional media commission. Percentages need a `%` sign.
- Every catalog code in the Pricing Book needs a cost row in Pricing Internal (Catalog costs), and the other way round.
- Role keys in Catalog costs (DS, PD, WD, DG, AM, PM, LD) must match the Roles table.
- The last row of Digital media fees has `above` in the first column.
- Billing terms needs the five named rows (Standard 50/25/25, Production 100, Government Monthly, Retainer Monthly, Lump Sum at Completion).
- Rules and Wording needs the approved wording headings (Monthly Retainer, Fixed Quote, private client, Government contract, Time & Materials, Time & Deliverables, Not to Exceed, Production, Outside the price, Travel, Nonprofit discount, Change orders, Rates).

## Things to know

- **Do not put prices in notes.** The upload box hides the Notes field for Pricing on purpose. A note is added to every AI call and could contradict the docs.
- **Budgets that were already made keep the version they were priced with.** To re-price one with a new version, regenerate its budget.
- **After Valid through passes**, the app warns on every new budget until a new price list is uploaded.
- **If you rename a heading or column**, the upload is refused and tells you which one. The names are in this guide and in the docs themselves.
- **A new pricing model** (not a price change, but new rules like a different way to compute the price) needs a developer.
