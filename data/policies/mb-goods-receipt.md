---
policy_id: mb-goods-receipt
title: When a goods receipt is required
audience: all
facts_used:
  gr_required_flows: ['3-way match, invoice before GR', '3-way match, invoice after GR']
  gr_not_required_flows: ['2-way match', 'Consignment']
  gr_quantity_tolerance_pct: 5.0
---

# When a goods receipt is required

Whether receipt evidence is needed before an invoice can be cleared depends on
the item's matching flow, not on the kind of goods.

Receipt evidence **is** required for:

- 3-way match, invoice before GR
- 3-way match, invoice after GR

Receipt evidence is **not** required for:

- 2-way match
- Consignment

For a service purchase, a service entry sheet is receipt evidence and is treated
exactly as a goods receipt.

A received quantity may exceed the ordered quantity by at most
**5.0%**. Beyond that, the purchase order must be amended
before the receipt is recorded.

Clearing an invoice on a flow that requires receipt evidence, before that
evidence exists, is a control breach. Check the flow before you check anything
else: the same invoice is correct on one flow and a breach on another.
