---
policy_id: mb-clearing-authority
title: Who may clear an invoice, and up to what value
audience: all
facts_used:
  clearing_auto_approve_limit_eur: 10000
  approval_limits_eur: {'requisitioner': 0, 'buyer': 25000, 'ap_clerk': 0, 'controller': 250000}
  controller_sla_hours: 24
extra_numbers: [1.0]
---

# Who may clear an invoice, and up to what value

Accounts-payable clerks clear invoices. Buyers do not clear invoices, and
clerks do not record goods receipts: those two duties are separated.

A clearing of **10,000 EUR or less** executes once every control
check passes. A clearing **above 10,000 EUR** is queued for a
controller, who responds within **24 hours**.

A queued clearing has not been paid. It must be described as queued for
approval, never as paid, settled or done.

Release authority on a purchase order is separate: a buyer may release up to
**25,000 EUR**, a controller up to **250,000 EUR**.

No single person may perform both halves of a separated duty on the same
purchase-order item, regardless of their role or their limit. If you have
already acted on one side of a pair for an item, the other side must be done by
someone else -- that is 1 rule with no exceptions.
