## Purpose

Review and populate the 1003 URLA Lender section. Sets how title will be held: Estate Will Be Held In (field 1066, normally FeeSimple) and Manner in Which Title Will Be Held (field 33 + URLA.X138, computed from property state, marital status, co-borrower/NBS presence, and borrower sex). This is the single owner of the manner-held value — Borrower Vesting (later step) reads field 33 and never recomputes it. Also surfaces Attachment Type and Property Type for verification against the listing.


**⚠️ This step may WRITE to Encompass.** Substeps that write are marked below.

**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `update_urla_lender` | Update 1003 URLA Lender |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 3.1 | Update 1003 URLA Lender | `update_urla_lender` |

## Tool Calls

```python
# Substep 3.1 - Update 1003 URLA Lender
# ⚠️ This substep WRITES to Encompass
update_urla_lender()
```

---

## Substeps

### Substep 3.1 - Update 1003 URLA Lender
**Tool**: `update_urla_lender`

Compute and write Manner in Which Title Will Be Held (field 33) + URLA.X138 from property state, marital status, co-borrower presence, NBS flag, and borrower sex (written when empty; NV forces As Joint Tenants for married couples). Auto-set Estate Will Be Held In (field 1066) to FeeSimple for standard residential loans. Surface Attachment Type (CX.ATTACHMENT.TYPE) and Property Type (field 1041) for verification against the listing.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Property State | `14` | `property_state` | State-specific manner-held rules (community property, joint tenants, tenancy by entirety) |
| Borrower Marital Status | `479` | `marital_status` | Married / Unmarried / Separated — drives manner held |
| Borrower Sex | `471` | `borrower_sex` | His/Her sole-and-separate and unmarried/married man/woman wording |
| Co-Borrower Sex | `478` | `coborrower_sex` | Used with borrower_sex for spouse vesting wording |
| Co-Borrower First Name | `4004` | `coborrower_first_name` | Presence determines joint vs sole manner held |
| Co-Borrower Last Name | `4006` | `coborrower_last_name` | Presence determines joint vs sole manner held |
| Non-Borrowing Spouse Flag | `CX.NBSFLAG` | `nbs_flag` | YES if NBS exists on title without being a co-borrower |
| Non-Borrowing Spouse Name | `CX.NBSINFO` | `nbs_info` | NBS presence affects manner held (both on title) |
| Manner in Which Title Will Be Held | `33` | `manner_of_title` | Computed and written here when empty (NV forces As Joint Tenants for married couples). URLA.X138 = the same data shown on the 1003 URLA Lender form — always written together with field 33. Borrower Vesting reads this. |
| Estate Will Be Held In | `1066` | `estate_held` | Auto-set to FeeSimple for standard residential loans (blank or Leasehold) |
| Property Type | `1041` | `property_type` | Surface for verification against the listing (Attachment/Property Type) |
| Attachment Type (Attached/Detached) | `CX.ATTACHMENT.TYPE` | `attachment_type` | Surface for verification against the listing |

**Business Rules:**
- **Manner Held Computed from State and Marital Status** (custom): Field 33 (Manner Held) is computed from property state, marital status, co-borrower presence, NBS flag, and borrower sex. Write only if empty. Community property states: AZ, CA, ID, LA, NV, NM, TX, WA, WI. NV married couples → As Joint Tenants (force overwrite). Flag mismatch for others. Always write URLA.X138 (Lender-form enum) together with field 33.

- **Estate Will Be Held In = Fee Simple** (value_set): Field 1066 (Estate Will Be Held In) is set to FeeSimple for standard residential loans whenever it is blank or Leasehold.

- **Attachment Type / Property Type vs Listing** (custom): Surface Attachment Type (CX.ATTACHMENT.TYPE) and Property Type (field 1041) for the processor to verify against the property listing. Automated validation is a TODO (not implemented).


**Flags — raise when conditions are met:**
- INFO: "Manner Held Auto-Set"
  - Condition: Field 33 was empty and a computed manner held was written
  - Remedy: Verify manner held matches title/vesting intent
- WARNING: "Manner Held Mismatch"
  - Condition: Field 33 does not match computed manner held for state/marital status
  - Remedy: Update Manner Held (field 33) or confirm with team lead
- INFO: "Estate Auto-Set to Fee Simple"
  - Condition: Field 1066 was blank or Leasehold and was set to FeeSimple
  - Remedy: Confirm Fee Simple is appropriate for this property

**⚠️ Field Updates (writes to Encompass):**
- Field `33` = `{computed_manner_held}` (when: empty)
- Field `URLA.X138` = `{computed_manner_urla_x138}` (when: empty)
- Field `1066` = `FeeSimple` (when: empty or Leasehold)

After completing this substep, call:
```
write_todo(step_id="STEP_03", substep_id="3.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_03", status="completed", ...)`
2. Call `write_todo(step_id="STEP_03", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_04", status="in_progress")` to start STEP_04 (1003 URLA Page 1)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
