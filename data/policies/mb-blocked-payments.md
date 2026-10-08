---
policy_id: mb-blocked-payments
title: Payment blocks and duplicate invoices
audience: all
facts_used:
  duplicate_invoice_window_days: 90
  duplicate_invoice_value_tolerance_eur: 1
---

# Payment blocks and duplicate invoices

A payment block holds an invoice until the reason for the block is resolved. A
block is removed only once the underlying reason is gone -- the goods receipt
arrived, the credit note was issued, or a controller approved the difference.

Removing a block to make a payment go through, while the reason for the block
still stands, is a control breach. The correct action in that situation is to
escalate to a controller.

A second invoice from the same vendor, for the same value, within
**90 days** of an existing one is treated as a suspected
duplicate and is blocked pending review.

Meridian Industrial Supply refuses all requests to change vendor bank details through this channel.
Such requests are escalated without exception.
