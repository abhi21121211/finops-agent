# ADR 0002: What reconciliation blocks, and how payments are matched

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

The spec's routing rule sends an invoice to a human whenever
`match_result.status != "matched"`, where the status is one of `matched`, `partial`,
`mismatch`, `no_po` or `unpaid`.

In accounts payable an invoice is approved *before* it is paid. At approval time almost
every invoice is `unpaid`. Applied literally, the rule would send every invoice to a human
and auto-approval would never happen.

## Decision

1. **Only purchase-order problems block auto-approval.** `no_po` and `mismatch` route to
   human review. `unpaid` and `partial` describe payment state and are recorded and shown,
   not used for routing.
2. **The overall status combines both sides.** PO problems take precedence. Otherwise the
   payment state decides: paid in full (including a recognised TDS shortfall) → `matched`,
   part-paid → `partial`, nothing paid → `unpaid`.
3. **Payments are matched tenant-wide, not per invoice**, because one transfer can pay
   several invoices. Every bank statement upload, and the end of every workflow run,
   recomputes all payment matches with the same pure function. Results are stored in
   `matches.details`.
4. **Matching on amount alone is never allowed.** A transfer must quote an invoice number
   or name a single identifiable vendor. Rent or salary of the same amount must not
   "pay" an invoice.
5. **A part-delivery is a match.** Billing 500 of 1,000 ordered boxes is normal. Quantity
   is checked against what is *left* on the PO after other live invoices, which also
   catches over-billing spread across several invoices.
6. **The LLM's role is limited.** It only judges whether two differently worded line
   descriptions are the same item (normal tier). Amounts, quantities, prices, TDS and
   allocation are plain code.

## Consequences

- Auto-approval stays possible. The reconciliation page shows payment state separately,
  so unpaid and part-paid invoices remain visible.
- A full recompute per upload is O(invoices × transactions) per tenant. That is fine for
  a small business. Larger tenants would need incremental allocation.
- Combined-payment search is capped (4 invoices, 12 candidates per vendor) to bound the
  subset search.
