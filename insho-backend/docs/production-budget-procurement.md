# Phase 10 Part 4 — Budgets, suppliers, quotes, and procurement

Part 4 uses Part 3 BOM revisions, cost bases, currency, and stale-source checks. Each requirement retains the Part 1 production item, Part 2 quantity and technical specification, and Phase 9 source references captured by its BOM line. Quotation attachments use existing project assets. Project and event authorization applies throughout.

## Budgets and variance

A budget is an immutable revision for a project or one of its events. Supply the amount explicitly or use the project's or event's existing planning amount by omitting `amount`. The latter captures the legacy amount and source version. Allocate amounts to the existing production cost categories. Allocations may be less than the budget, with the difference reported as unallocated. A revision requires the current `expected_version`. Use 0 for the first revision. Approval stamps the latest revision and keeps earlier approvals readable.

Select one current BOM per scene. All selected BOMs use the budget currency and decimal precision. Estimated, quoted, and actual amounts are reported separately per category and for the budget. Select approved quote IDs to use supplier quotes in place of the Part 3 quoted basis for their BOMs. A BOM line may receive one selected supplier quote. Missing coverage or unallocated delivery charges leave that portion of the quoted total incomplete. Quote lines without a mapped requirement appear under `other`. Actual costs come from explicit Part 3 actual lines.

A basis with unknown values returns a null total. When part of a basis is priced, its priced subtotal remains available. The summary reports unpriced lines and unrecorded categories. For an approved amount and complete actuals, remaining budget equals approved amount minus actual cost, and variance equals actual cost minus approved amount. Estimated and quoted variances compare their complete totals with the approved amount. Stale reads retain the captured figures and report changed BOM, quote, or legacy planning sources. Create a new budget revision to update the figures.

## Suppliers and quotations

The supplier directory belongs to an organization. Suppliers have versioned identities, business and contact details, categories, capabilities, location, delivery coverage, and authored terms. Project links keep a dated sourcing history independent of the directory. An inactive link blocks new sourcing decisions for that project. Directory and offering edits use optimistic versions. Quotations and sourcing history retain the supplier and offering snapshots used for the decision.

An offering is a material, component, or service. It can link to an existing material definition or a reusable Object Library definition where applicable. Its terms, unit, minimum order quantity, and lead time are supplied by the user. Multiple suppliers can offer the same requirement.

A quote is recorded against a current BOM revision and retains the supplier reference, submitted date, validity, lines, terms, and optional asset reference. Revisions use the supplier, project or event, and reference as their series. Documents can be existing project documents or scanned images. A quoted attachment's identity and fingerprint are retained in history, and the existing asset cleanup excludes linked files.

Supplier-submitted quantities, rates, tax, discount, and any stated totals remain visible. The existing Part 3 Decimal formula computes line totals. The calculated quotation total is the sum of complete line totals plus explicitly supplied delivery. Enter zero when delivery carries no separate charge. A stated total that differs from the calculated total, missing pricing or validity, and an expired quote block commercial approval. Validity is inclusive through its stated UTC date. Lead time, payment terms, and attachment gaps remain visible.

Comparison uses the same BOM line IDs, verifies equivalent quantities with Decimal unit conversions before display rounding, and checks literal specification and finish text and declared catalogue substitutions. A comparable line shows the quoted line cost minus the estimated BOM line cost before freight. Mismatches identify the different terms. Quote approval records a commercial decision. A specification change still requires a procurement substitution review.

## Procurement and value engineering

Generate purchasing requirements from the latest current approved BOM. Generation is idempotent for each approved estimated BOM line. A request retains the original requirement, quantity, unit, specification, source, estimated cost, and delivery and storage instructions. Keep preferred, alternative, and selected suppliers and the selected quotation on the request. The supplier quote must cover the requirement in a compatible unit and meet any recorded minimum. Explicit acceptance is required for surplus quantity. Procurement approval requires a current approved quote and an allocated delivery charge. A single-line quote can use its whole delivery charge. For a multi-line quote with positive delivery charges, supply the allocation per line before procurement approval.

The lifecycle is `requested → sourcing → approved → ordered → delivered`, with cancellation available before delivery. Owner or admin authorization applies to approval and post-approval transitions. Recording an existing order or delivery requires its external reference and date. Historical updates retain the approval snapshot and show any source changes. Actual purchasing and payment are external to this workflow.

Propose a substitution separately using a quoted material or component alternative. The proposal captures the original requirement, quoted alternative, cost and quantity differences, rationale, specification comparison, visual, technical, production and lead-time impacts, and risks. It can reference an existing saved design artifact. Explicit review evidence is required for approval. Approval and application are separate owner or admin actions. Applying the proposal updates the procurement selection and retains the original requirement, technical source, and BOM for review. The application records the human review without certifying equivalence.

A selected quote with an uncovered quantity or incompatible unit leaves the quoted budget line incomplete. Specification, finish, and catalogue mismatches also leave it incomplete until an approved substitution is applied to that requirement. The budget records the procurement and proposal revision it used. Applying, replacing, or invalidating that substitution flags affected budget snapshots as `quotation_requirements_changed`. Quote expiry and new quote revisions also flag their dependent snapshots. Create a new budget revision to use the updated basis.

If a draft procurement's applied proposal loses its design evidence or needs a revised quote, approve a new proposal and apply it with `replaces_proposal_id` set to the current proposal ID. The old proposal and original requirement remain in history. Once procurement is approved, the approval snapshot is retained. Order and delivery transitions record external events against that approval and surface later stale-source warnings.

## API examples

Project routes below use `/api/v1/production/projects/{project_id}` as their prefix. Supplier directory routes use `/api/v1/production/organizations/{organization_id}/suppliers`.

| Endpoint | Purpose |
| --- | --- |
| `GET /budgets`, `POST /budgets`, `GET /budgets/{id}`, `POST /budgets/{id}/approve` | Budget revisions, allocated costs, variance, and approval. List or write an event budget with `event_id`. |
| `GET` or `POST` at the supplier directory, `GET` or `PUT /{supplier_id}`, `GET` or `POST /{supplier_id}/offerings`, `PUT /{supplier_id}/offerings/{offering_id}` | Supplier identity and offerings. |
| `GET /suppliers`, `PUT /suppliers/{supplier_id}` | List or update project supplier links. |
| `GET /quotes`, `POST /quotes`, `GET /quotes/{id}`, `POST /quotes/{id}/decision` | Record, read, revise, and review supplier quotes. Lists accept `event_id`, `status`, and `history=true` for prior revisions. |
| `GET /boms/{bom_id}/quote-comparison` | Compare current quote revisions against one BOM. |
| `POST /procurements/generate`, `GET /procurements`, `GET /procurements/{id}` | Generate and retrieve requirements. |
| `PUT /procurements/{id}/selection`, `POST /procurements/{id}/status` | Manage supplier selection, approval, order, delivery, or cancellation. |
| `GET` or `POST /procurements/{id}/proposals`, `GET /procurements/{id}/proposals/{proposal_id}`, `POST /procurements/{id}/proposals/{proposal_id}/decision` | Record proposals and explicitly approve, reject, or apply them. |

A first budget revision for an existing BOM.

```json
{
  "expected_version": 0,
  "currency": "INR",
  "amount": "100.00",
  "allocations": {"material": "90.00"},
  "bom_ids": ["9f280300-c675-4056-81e4-d87ca89e7313"]
}
```

After approval, with an 80 INR estimate and 72 INR actual cost, the budget response includes these fields.

```json
{
  "version": 1,
  "unallocated": "10.00",
  "summary": {
    "approved_amount": "100.00",
    "remaining": "28.00",
    "actual_variance": "-28.00"
  },
  "stale": false
}
```

A first supplier quote for a requirement from that BOM.

```json
{
  "expected_version": 0,
  "bom_id": "9f280300-c675-4056-81e4-d87ca89e7313",
  "supplier_id": "2df8827d-5783-4619-9ffe-b32a9c0f74fd",
  "reference": "Q-120",
  "quoted_on": "2026-11-24",
  "valid_until": "2026-12-04",
  "currency": "INR",
  "delivery_charge": "4.00",
  "lines": [{
    "description": "Cut panels",
    "bom_line_id": "9ed640c5-dad7-4c96-b1cd-130052119d21",
    "quantity": "32",
    "unit": "m2",
    "unit_rate": "2.00",
    "specification": "12 mm plywood",
    "finish": "Matte"
  }]
}
```

The quote response provides `details.total: "68.00"`, `status: "received"`, comparison results, and the completeness and expiry fields. Review with `{"expected_version": 1, "decision": "approved"}`. For procurement, send that BOM ID to `/procurements/generate`, then confirm a quote and supplier with the request's current `expected_version`. Both proposal decisions and their application also verify `expected_procurement_version`.

## Migration and Phase 12

Migration `8d31b6e2f4a7` follows Part 3 and adds seven tables. A downgrade to Part 3 removes these sourcing and budget records. Earlier production tables and values are preserved.

Phase 12 can use budget history and variance, supplier offerings and project links, quote comparison and existing asset downloads, procurement status and audit, and proposal evidence and decisions. Display incomplete, stale, and expired states separately from approval. Supplier portals, external purchase orders, payments, inventory reconciliation between BOM versions, tax determination, exchange rates, and automated technical certification remain outside the contract.

Phase 11 Part 4 reconciles verified spend against the estimate captured in an exact event design version. A budget total and a BOM line are separate comparison scopes. Supplier outcomes use the existing project supplier link and verified event observations. Learning calculations keep all values in the recorded currency. [Learning Engine and Actuals Capture](../README.md#phase-11-part-4--learning-engine-and-actuals-capture)
