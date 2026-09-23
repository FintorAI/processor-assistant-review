# TaskTile AWM categories that are **classification-only**

These AWM category schemas return **only routing metadata** — borrower/veteran names, property
address, signature/date, and (in a couple of cases) a company name or a single date. **None expose
a data field the agent writes to Encompass**, so for these doc types **LandingAI stays primary**;
TaskTile is useful only to *classify/route* the document, not to extract from it.

Verified against the **AWM category set** (`tasktile.staging.cybersoftbpo.ai/api/category-sets/awm/{id}`),
2026-09-23. 
---

## Classification-only categories

| SBIQ id | Doc type | Fields TaskTile returns (AWM) | Fields still MISSING from TaskTile |
|---|---|---|---|
| **294** | VA Loan Summary | `veterans[]{firstName,middleName,lastName,suffix,signed,dateSigned}` | sales_price, appraised_value, loan_amount, interest_rate, PITI breakdown, proposed escrows |
| **447** | VA Certificate of Eligibility | `veteran{firstName,middleName,lastName,suffix}`, `dateIssued` | va_case_number, entitlement_code, basic/additional_entitlement, funding_fee_exempt |
| **1118** | Fraud Report | `borrowers[]{names}`, `date` | fraud_alert_status, fraud_score, aka (borrower+coborrower), address_history, ssn |
| **1800** | Flood Certificate | `borrowers[]{names,signed,dateSigned}` | flood_zone, in_sfha, community_number, map_panel, map_date, determination_date, flood_determination_number, order_number |
| **538** | Flood Certification (alt) | `borrowers[]{names}`, `date`, `lender` | same flood set as 1800 |
| **397** | Payoff Statement | `payoffStatementDate` | creditor_name, account_number, payoff_amount, per_diem, good_through_date, payee_address |
| **2087** | Home Inspection | `borrowers[]{names,signed,dateSigned}` | inspection_date, inspector_name, inspector_company, result, major_issues |
| **578** | Pest Inspection | `company.name`, `date` | pest_found, treatment_required, result |
| **33** | Appraisal Invoice | `appraisalCompany.name`, `loanNumber`, `propertyAddress{…}` | appraisal_fee, total_invoice_amount, invoice_number, invoice_date, reinspection_fee, balance_due |

---

## Excluded from this list — they look similar but DO carry data (PARTIAL, not classification-only)

Double-checking the AWM set moved these **out** of "classification-only": each exposes at least one
data field the agent uses, so they are **partially** served by TaskTile (map what's there,
LandingAI backfills the rest).

| SBIQ id | Doc type | Data field(s) present on AWM | Fields still MISSING from TaskTile |
|---|---|---|---|
| **522** | Title Report / Commitment | `vestedIn`, `vestedPersons[]`, `loanAmount`, `address{…}` | title_company, settlement_agent, escrow_company, commitment_number, effective_date, legal_description, parcel_number |
| **1561** | Evidence of Hazard Insurance | `company.name`, `policyType`, `totalPremium`, `expirationDate`, `owner{…}` | policy_number, effective_date, coverage{dwelling,deductible,wind_hail}, agent{…}, mortgagee_clause |
| **162** | Appraisal Report | `value` (appraised value), `appraisalDate`, prior-sale date/price | property_type, year_built, parcel_number, flood_zone, appraiser{…}, condition flags |
| **333** | Approval Form | `underwritingStatus`, `decisionDate` | prior_to_docs_conditions, prior_to_funding_conditions |
| **167** | Closing Protection Letter | `issuingAgent`, `CPLDate` | lender_name, cpl_title_underwriter |

---

## Not in scope here

- **Partially updated (have new data fields):** 819 Closing Disclosure (`closingDate`,
  `disbursementDate`), 984 Loan Estimate (5 fee/escrow amounts) — TaskTile-primary for those keys.
- **Already covered (rich schemas, no ask):** 349 URLA, 1 Business Tax, 843 SSN Card, 1481 Property Tax.
- **No fields consumed (no ask):** 820 VA Funding Fee Worksheet, 296 VA Nearest Living Relative.
