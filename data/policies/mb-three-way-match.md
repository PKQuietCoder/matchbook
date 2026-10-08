---
policy_id: mb-three-way-match
title: Three-way match and invoice tolerance
audience: all
facts_used:
  tolerance_abs_eur: 50
  tolerance_pct: 2.0
  tolerance_rule: looser_of_abs_or_pct
---

# Three-way match and invoice tolerance

An invoice is matched against the value of the goods receipt and the value on
the purchase-order item. The invoice may exceed the matched value by the
**looser** of two allowances: **50 EUR**, or **2.0%**
of the matched value.

The looser-of rule is deliberate. A percentage alone would reject trivial
rounding differences on small items; an absolute allowance alone would wave
through a material difference on a large one.

A variance above the allowance is a **tolerance breach**. A tolerance breach is
resolved by the vendor issuing a credit note, or by a controller approving the
difference. It is never resolved by removing the payment block.

An invoice *below* the matched value is not a breach; the remaining purchase-order
value stays open.
