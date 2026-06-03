2605968608

thread id: 019e89e6-7fbe-74a2-babb-e4aef1ac7fc1


- Bank statements months is 0? but present in efolder?

Credit Score Missing

Review Borrower Summary - Origination

Middle credit score (field 1168) is blank.

Suggestion: Ensure credit has been pulled and scores are populated in Encompass.

- 1168 is wrong field id? points to another field


Section 2c Empty — 'Does Not Apply' Not Checked (Borrower)

Employment Verification (2b VOE)

Borrower additional employment (Section 2c on 1003 URLA Part 2) is blank and 'Does Not Apply' is not checked.

Suggestion: Enter self/additional employment details or check the 'Does Not Apply' box for Section 2c.


Section 2d Empty — 'Does Not Apply' Not Checked (Borrower)

Employment Verification (2b VOE)

Borrower previous employment (Section 2d on 1003 URLA Part 2) is blank and 'Does Not Apply' is not checked.

Suggestion: Enter previous employment details or check the 'Does Not Apply' box for Section 2d.

- Do we not write this? are these just info flags

Monthly Base Pay Mismatch — Current (VOE vs 1003)

Employment Verification (2b VOE)

LOS: $14,438.67 | VOE: $15,341.08

Suggestion: Reconcile the monthly base pay figure with the VOE

- where did 15k come from?

Estate Verified — Fee Simple

Update Borrower Vesting

Resolved
Estate Will Be Held In (field 1066) is 'FeeSimple'.

Suggestion: No action needed.

Documents: Title Report

- why is this in vesting, should be in lender

Appraisal Form Number Set — 1004

Update Transmittal Summary

Resolved
Wrote: 1542='1004', TSUM.PropertyFormType='Uniform Residential Appraisal Report'. Derived from property type ('Detached'). ⚠️ Field ID 1542 unverified — confirm with field_rw.py before relying on this write.

Suggestion: Verify field 1542 is correct in Encompass after this run.

- dont need this flag

Field Write Error

Processor Workflow Update

Could not write field(s) ['CX.PRODUCTTYPE', 'CX.DOCUMENTATIONTYPE', 'CX.NONDEL.INV.APPROVAL']: Field write failed (status 400): {"summary":"Bad Request","details":"Request Payload has errors","errors":[{"summary":"changes[4].id","details":"Invalid custom field 'CX.NONDEL.INV.APPROVAL'.","additionalInfo":{"errorType":"Serialization"}}]}

Suggestion: Check if the loan is locked or the field ID is correct.

Field Write Error

Processor Closing Update

Could not write field(s) ['CUST50FV', 'CX.WIREDATELO']: Field write failed (status 400): {"summary":"Bad Request","details":"Request Payload has errors","errors":[{"summary":"changes[0].value.loan.customFields[(fieldName == 'CUST50FV')].value","details":"Invalid value for custom field 'CUST50FV'. Value should be a valid UTC Date in ISO format 'yyyy-MM-dd'. Timezone offset is not allowed.","additionalInfo":{"errorType":"Serialization"}},{"summary":"changes[2].value.loan.customFields[(fieldName == 'CX.WIREDATELO')].value","details":"Invalid value for custom field 'CX.WIREDATELO'. Value should be a valid UTC Date in ISO format 'yyyy-MM-dd'. Timezone offset is not allowed.","additionalInfo":{"errorType":"Serialization"}}]}

Suggestion: Check if the loan is locked or the field ID is correct.

- check above