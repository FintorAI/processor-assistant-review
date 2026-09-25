"""Tests for the doc_fields <-> bucket-config bridge (shared/tasktile_docfields.py)."""
from shared import tasktile_docfields as dfx


def test_category_for_doc_type_exact_and_alias():
    assert dfx.category_for_doc_type("Driver's License") == 323
    assert dfx.category_for_doc_type("Drivers License") == 323  # apostrophe alias
    assert dfx.category_for_doc_type("ALTA Settlement Statement") == 2168
    assert dfx.category_for_doc_type("Closing Protection Letter (CPL)") == 167


def test_category_for_doc_type_fuzzy():
    # normalization ignores punctuation/case/spacing
    assert dfx.category_for_doc_type("drivers  license") == 323
    assert dfx.category_for_doc_type("Unknown Doc Type") is None


def test_category_for_doc_type_income_docs():
    # AWM category ids verified via GET /api/category-sets/awm
    assert dfx.category_for_doc_type("Paystubs") == 16
    assert dfx.category_for_doc_type("Paystub") == 16
    assert dfx.category_for_doc_type("W-2") == 25
    assert dfx.category_for_doc_type("W2") == 25
    assert dfx.category_for_doc_type("Bank Statement") == 502


def test_resolve_and_fill_paystub_and_w2():
    """Paystub(16)+W2(25) manifest leaves -> processor doc_fields field_keys."""
    manifest = {
        "_processor": {"job_id": "T"},
        "documents": [
            {"root_attachment_id": "a1", "category_id": None, "metadata": {
                "group_name": "Paystubs", "total_pages": 2,
                "YTD": "45000", "current": "3750",
                "payStubPeriodFrom": "2026-01-01", "payStubPeriodTo": "2026-01-15",
                "hourlyRate": "45", "hoursWorked": "80",
                "employer": {"name": "Acme Corp", "EIN": "12-3456789"},
            }},
            {"root_attachment_id": "a2", "category_id": None, "metadata": {
                "group_name": "W-2", "year": "2025", "box5": "90000",
                "wagesTipsOtherCompensation": "88000",
                "employer": {"name": "Acme Corp", "EIN": "12-3456789"},
            }},
        ],
    }
    att_to_doctype = {"a1": "Paystubs", "a2": "W-2"}
    doc_fields = {}
    proposals = dfx.resolve_and_fill(doc_fields, manifest, att_to_doctype, apply=True)

    filled = {p["field_key"]: p["value"] for p in proposals}
    # Paystub fields
    assert filled["employer_name"] == "Acme Corp"
    assert filled["ytd_gross_pay"] == "45000"
    assert filled["gross_pay_this_period"] == "3750"
    assert filled["pay_period_start"] == "2026-01-01"
    # W2 fields
    assert filled["employer_ein"] == "12-3456789"
    assert filled["tax_year"] == "2025"
    assert filled["wages_tips_other_compensation"] == "88000"
    assert filled["medicare_wages"] == "90000"
    # every proposal came from the manifest and was applied into doc_fields
    assert all(p["source"] == "tasktile_ai_only:manifest" and p["applied"] for p in proposals)
    assert doc_fields["employer_name"]["value"] == "Acme Corp"


def test_resolve_and_fill_voe():
    manifest = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "v", "category_id": None, "metadata": {
            "group_name": "Verification of Employment",
            "VOEDate": "2026-02-01", "rateOfPay": "45.00", "frequencyOfPay": "Hourly",
            "averageHoursPerPayPeriod": "80", "dateOfEmployment": "2020-05-01",
            "employer": {"name": "Acme Corp"},
            "employerAddress": {"address1": "1 Main St", "city": "SF", "state": "CA", "zipCode": "94105"},
        }},
    ]}
    doc_fields = {}
    proposals = dfx.resolve_and_fill(doc_fields, manifest, {"v": "Verification of Employment"}, apply=True)
    f = {p["field_key"]: p["value"] for p in proposals}
    assert f["current_employer_name"] == "Acme Corp"
    assert f["current_rate_of_pay"] == "45.00"
    assert f["current_employer_city"] == "SF"
    assert f["verification_date"] == "2026-02-01"


def test_resolve_and_fill_purchase_agreement():
    manifest = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "p", "category_id": None, "metadata": {
            "group_name": "Purchase Agreement",
            "purchasePrice": "1160000", "closingDate": "2026-08-28",
            "earnestMoney": "17400", "datePrepared": "2026-08-07",
            "propertyAddress": {"fullAddress": "14940 Janine Drive, Whittier, CA"},
            "buyersAgent": {"company": "KW", "name": "J Mora", "phone": "111", "email": "j@kw.com", "licenseId": "02324709"},
            "sellersAgent": {"company": "TNG", "name": "L D", "email": "l@x.com"},
        }},
    ]}
    proposals = dfx.resolve_and_fill({}, manifest, {"p": "Purchase Agreement"}, apply=True)
    f = {p["field_key"]: p["value"] for p in proposals}
    assert f["pa_purchase_price"] == "1160000"
    assert f["pa_closing_date"] == "2026-08-28"
    assert f["buyer_agent_name"] == "J Mora"
    assert f["seller_agent_company"] == "TNG"


def test_resolve_and_fill_du_findings_and_mi():
    manifest = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "d", "category_id": None, "metadata": {
            "group_name": "DU Findings",
            "loanType": "Conventional", "noteRate": "7.250%", "loanPurpose": "Purchase",
            "DTIPercentage": "44.12%", "recommendation": "Approve/Eligible", "ltv": "95",
            "loanAmount": "266000", "appraisedValue": "280000",
            "propertyAddress": {"address1": "132 N Linwood Ave"},
        }},
        {"root_attachment_id": "m", "category_id": None, "metadata": {
            "group_name": "MI Certificate",
            "certificateNumber": "C1", "miFileNumber": "F1", "premiumType": "Monthly",
            "monthlyPremiumAmount": "166.73", "company": {"name": "Radian", "address": "PA"},
            "renewal": {"firstPercent": "0.18", "firstMonths": "10"}, "cancelAtPercent": "0",
        }},
    ]}
    proposals = dfx.resolve_and_fill({}, manifest, {"d": "DU Findings", "m": "MI Certificate"}, apply=True)
    f = {p["field_key"]: p["value"] for p in proposals}
    assert f["du_recommendation"] == "Approve/Eligible"
    assert f["mortgage_type"] == "Conventional"
    assert f["ltv"] == "95"
    assert f["monthly_premium_amount"] == "166.73"
    assert f["mi_company_name"] == "Radian"
    assert f["first_renewal_percent"] == "0.18"


def test_resolve_and_fill_green_card_govt_id():
    manifest = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "gc", "category_id": None, "metadata": {
            "group_name": "Permanent Resident Card",
            "resident": {"idNumber": "219-909-413", "firstName": "MARIA",
                         "lastName": "ACUNA LOPEZ", "DOB": "1993-04-12"},
            "expirationDate": "2024-03-23"}},
    ]}
    f = {p["field_key"]: p["value"] for p in
         dfx.resolve_and_fill({}, manifest, {"gc": "Permanent Resident Card"}, apply=True)}
    assert f["dl_gov_id"] == "219-909-413"          # hyphenated USCIS/A-number now accepted
    assert f["borrower_first_name"] == "MARIA"
    assert f["borrower_dob"] == "1993-04-12"


def test_resolve_and_fill_coborrower_gov_id_stacks_copies():
    # Borrower DL + co-borrower Passport arrive as SEPARATE ID attachments.
    # Both gov IDs must be captured as multi-copy doc_fields so the review tool
    # can write field 5053 (borrower) AND 5054 (co-borrower).
    manifest = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "a", "category_id": None, "metadata": {
            "group_name": "Driver's License",
            "owner": {"idNumber": "D1234567", "firstName": "JOHN",
                      "lastName": "SMITH", "DOB": "1980-01-02"},
            "expirationDate": "2030-05-01"}},
        {"root_attachment_id": "b", "category_id": None, "metadata": {
            "group_name": "Passport",
            "owner": {"idNumber": "X9988776", "firstName": "JANE",
                      "lastName": "SMITH", "DOB": "1982-03-04"},
            "dateOfExpiration": "2029-06-01"}},
    ]}
    df = {}
    dfx.resolve_and_fill(df, manifest,
                         {"a": "Driver's License", "b": "Passport"}, apply=True)
    # top-level value stays the borrower's (backward-compat for _doc())
    assert df["dl_gov_id"]["value"] == "D1234567"
    copies = {c["copy_index"]: c["value"] for c in df["dl_gov_id"]["copies"]}
    assert copies == {0: "D1234567", 1: "X9988776"}
    # aligned name copies let the review tool match each ID to the right person
    names = {c["copy_index"]: c["value"] for c in df["dl_borrower_name"]["copies"]}
    assert names == {0: "JOHN SMITH", 1: "JANE SMITH"}
    # borrower single-slot identity fields are the first (borrower) doc's
    assert df["borrower_first_name"]["value"] == "JOHN"


def test_resolve_and_fill_id_copy_dedupes_same_person():
    # The same borrower's ID re-uploaded (same number) must NOT create a phantom
    # second copy (which would be misread as a co-borrower).
    dl = {"root_attachment_id": "a", "category_id": None, "metadata": {
        "group_name": "Driver's License",
        "owner": {"idNumber": "D1234567", "firstName": "JOHN", "lastName": "SMITH"}}}
    manifest = {"_processor": {"job_id": "T"}, "documents": [dl, dict(dl, root_attachment_id="a2")]}
    df = {}
    dfx.resolve_and_fill(df, manifest,
                         {"a": "Driver's License", "a2": "Driver's License"}, apply=True)
    assert len(df["dl_gov_id"]["copies"]) == 1


def test_resolve_and_fill_id_copy_yields_to_primary_extraction():
    # A primary (non-ai-only) extraction on dl_gov_id is never touched.
    df = {"dl_gov_id": {"value": "PRIMARY-ID", "source_document": "eFolder"}}
    manifest = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "a", "category_id": None, "metadata": {
            "group_name": "Driver's License",
            "owner": {"idNumber": "D1234567", "firstName": "JOHN", "lastName": "SMITH"}}}]}
    dfx.resolve_and_fill(df, manifest, {"a": "Driver's License"}, apply=True)
    assert df["dl_gov_id"]["value"] == "PRIMARY-ID"
    assert "copies" not in df["dl_gov_id"]


def test_resolve_and_fill_bucketb_leftovers():
    # LE cost fields
    le = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "le", "category_id": None, "metadata": {
            "group_name": "Loan Estimate", "loanEstimate": {
                "estimatedClosingCosts": 27390,
                "loanCosts": {"originationCharges": 2190}}}}]}
    f = {p["field_key"]: p["value"] for p in
         dfx.resolve_and_fill({}, le, {"le": "Loan Estimate"}, apply=True)}
    assert f["le_origination_charges"] == 2190
    assert f["le_estimated_closing_costs"] == 27390
    # MI upfront
    mi = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "m", "category_id": None, "metadata": {
            "group_name": "MI Certificate", "upfrontPremiumAmount": "8002.8"}}]}
    f = {p["field_key"]: p["value"] for p in
         dfx.resolve_and_fill({}, mi, {"m": "MI Certificate"}, apply=True)}
    assert f["upfront_premium_amount"] == "8002.8"
    # Credit dob + score (score via array first element)
    cr = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "c", "category_id": None, "metadata": {
            "group_name": "Credit Report",
            "borrower": {"dob": "04/19/1989", "creditScore": [{"score": "802"}]}}}]}
    f = {p["field_key"]: p["value"] for p in
         dfx.resolve_and_fill({}, cr, {"c": "Credit Report"}, apply=True)}
    assert f["borrower_dob"] == "04/19/1989"
    assert f["credit_score"] == "802"


def test_resolve_and_fill_fraud_report():
    manifest = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "fr", "category_id": None, "metadata": {
            "group_name": "Fraud Report", "date": "2026-01-01",
            "fraudAlertStatus": "High", "fraudScore": 762,
            "addressHistory": [{"address1": "1 Main", "city": "SF"}],
        }},
    ]}
    proposals = dfx.resolve_and_fill({}, manifest, {"fr": "Fraud Report"}, apply=True)
    f = {p["field_key"]: p["value"] for p in proposals}
    assert f["fraud_alert_status"] == "High"
    assert f["fraud_score"] == 762


def test_resolve_and_fill_alta_candidate_leaves():
    # run C shape: top-level settlementAgent.* / escrowCompany / titleCompany
    run_c = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "a", "category_id": None, "metadata": {
            "group_name": "ALTA Settlement Statement",
            "escrowCompany": "Escrow Inc", "titleCompany": "Fidelity",
            "settlementAgent": {"name": "Shane Magness", "address": "3 Pointe Dr", "phone": "714-854-9344"},
        }},
    ]}
    f = {p["field_key"]: p["value"] for p in
         dfx.resolve_and_fill({}, run_c, {"a": "Estimated Settlement Statement"}, apply=True)}
    assert f["escrow_company"] == "Escrow Inc"
    assert f["title_company"] == "Fidelity"
    assert f["contact_settlement_agent_name"] == "Shane Magness"

    # run B shape: nested escrow.* -> candidate fallback must still resolve the agent name
    run_b = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "a", "category_id": None, "metadata": {
            "group_name": "ALTA Settlement Statement",
            "escrow": {"escrowOfficer": "Shane Magness",
                       "settlementLocation": {"street": "3 Pointe Dr"},
                       "approximateAmountDueEscrow": "31041.19"},
        }},
    ]}
    f = {p["field_key"]: p["value"] for p in
         dfx.resolve_and_fill({}, run_b, {"a": "Estimated Settlement Statement"}, apply=True)}
    assert f["contact_settlement_agent_name"] == "Shane Magness"   # from escrow.escrowOfficer
    assert f["contact_settlement_agent_address"] == "3 Pointe Dr"  # from escrow.settlementLocation.street
    assert f["ess_cash_to_close"] == "31041.19"


def test_resolve_and_fill_alta_file_number_feeds_escrow_case():
    # ALTA fileNumber -> contact_settlement_agent_file_number -> Encompass field 186
    run_c = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "a", "category_id": None, "metadata": {
            "group_name": "ALTA Settlement Statement", "fileNumber": "14576 - SM"}},
    ]}
    f = {p["field_key"]: p["value"] for p in
         dfx.resolve_and_fill({}, run_c, {"a": "Estimated Settlement Statement"}, apply=True)}
    assert f["contact_settlement_agent_file_number"] == "14576 - SM"
    # nested-escrow shape falls back to escrow.escrowNumber
    run_b = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "a", "category_id": None, "metadata": {
            "group_name": "ALTA Settlement Statement", "escrow": {"escrowNumber": "14576 - SM"}}},
    ]}
    f = {p["field_key"]: p["value"] for p in
         dfx.resolve_and_fill({}, run_b, {"a": "Estimated Settlement Statement"}, apply=True)}
    assert f["contact_settlement_agent_file_number"] == "14576 - SM"


def test_resolve_and_fill_appraisal():
    # ai-only returns rich AWM shape but only property_type + parcel_number have targets
    manifest = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "ap", "category_id": None, "metadata": {
            "group_name": "Appraisal", "value": 831000, "appraisalDate": "2026-04-18",
            "propertyType": "Twin", "yearBuilt": 1900, "parcelNumber": "335-08.07-141.01",
            "floodZone": "X", "appraiser": {"name": "Harold Lankenau"}, "condition": "C3",
        }},
    ]}
    f = {p["field_key"]: p["value"] for p in
         dfx.resolve_and_fill({}, manifest, {"ap": "Appraisal (URAR / 1004)"}, apply=True)}
    assert f["property_type"] == "Twin"
    assert f["parcel_number"] == "335-08.07-141.01"
    # cross-wired global keys: appraisal is the authoritative source
    assert f["appraised_value"] == 831000
    assert f["flood_zone"] == "X"
    # Bucket A registry gap: appraiser/condition/yearBuilt have no processor field
    assert "appraiser_name" not in f and "year_built" not in f


def test_resolve_and_fill_single_leaf_still_works():
    # backward-compat: a plain string leaf must behave exactly as before
    manifest = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "p", "category_id": None, "metadata": {
            "group_name": "Paystubs", "employer": {"name": "Acme"}}},
    ]}
    f = {p["field_key"]: p["value"] for p in
         dfx.resolve_and_fill({}, manifest, {"p": "Paystubs"}, apply=True)}
    assert f["employer_name"] == "Acme"


def test_category_for_doc_type_tranche():
    assert dfx.category_for_doc_type("Purchase Agreement") == 200
    assert dfx.category_for_doc_type("Fraud Report") == 1118
    assert dfx.category_for_doc_type("Estimated Settlement Statement") == 2168
    assert dfx.category_for_doc_type("Title Report") == 522
    assert dfx.category_for_doc_type("Transmittal Summary") == 351
    assert dfx.category_for_doc_type("MI Certificate") == 141
    assert dfx.category_for_doc_type("Evidence of Insurance") == 1561
    assert dfx.category_for_doc_type("Closing Protection Letter") == 167
    assert dfx.category_for_doc_type("DU Findings / AUS Certificate") == 324


def test_resolve_and_fill_bank_statement_array_first_element():
    # account-level leaves are arrays -> flatten() takes the first account (obj[0])
    manifest = {"_processor": {"job_id": "T"}, "documents": [
        {"root_attachment_id": "b", "category_id": None, "metadata": {
            "group_name": "Bank Statement",
            "bank": {"name": "Chase"},
            "statementPeriodFrom": "2026-01-01", "statementPeriodTo": "2026-01-31",
            "accounts": [
                {"accountType": "Checking", "accountNumber": "1234", "endingBalance": "5000",
                 "hasLargeDepositWithdrawal": True},
                {"accountType": "Savings", "accountNumber": "5678", "endingBalance": "9000"},
            ],
        }},
    ]}
    doc_fields = {}
    proposals = dfx.resolve_and_fill(doc_fields, manifest, {"b": "Bank Statement"}, apply=True)
    f = {p["field_key"]: p["value"] for p in proposals}
    assert f["institution_name"] == "Chase"
    assert f["account_type"] == "Checking"      # first account
    assert f["account_number"] == "1234"
    assert f["ending_balance"] == "5000"
    assert f["statement_period_start"] == "2026-01-01"


def test_classify_gap_bucket1_is_investigate():
    # 200 (Purchase Contract) is a tested bucket-1 category
    g = dfx.classify_gap("purchase_price", ["Purchase Contract"])
    assert g["category_id"] == 200
    assert g["bucket"] == 1
    assert g["action"] == "investigate_bucket1_gap"
    assert g["tested"] is True


def test_classify_gap_bucket3_default_fallback():
    g = dfx.classify_gap("some_field", ["Passport"])  # 845 untested -> bucket 3
    assert g["category_id"] == 845
    assert g["bucket"] == 3
    assert g["action"] == "fallback_landingai"


def test_classify_gap_unknown_doc_type():
    g = dfx.classify_gap("mystery", ["Totally Unknown"])
    assert g["category_id"] is None
    assert g["action"] == "fallback_landingai"


def test_classify_gap_field_override_pins_bucket():
    # 2168 ALTA: titleCharges[].paidTo is still a pinned bucket-2 gap post-fix.
    g = dfx.classify_gap("title_charges_paid_to", ["ALTA Settlement Statement"])
    assert g["category_id"] == 2168
    assert g["bucket"] == 2


def test_classify_gap_id_number_fixed_now_bucket1():
    # Issue A fixed (job a2643fae): DL owner.idNumber now extracts -> bucket 1.
    # A missing id number is therefore an investigate-worthy bucket-1 gap.
    g = dfx.classify_gap("dl_id_number", ["Driver's License"])
    assert g["category_id"] == 323
    assert g["bucket"] == 1
    assert g["action"] == "investigate_bucket1_gap"


def test_classify_gap_non_override_field_keeps_category_bucket():
    # a DL field that is NOT an override (e.g. name) stays bucket 1 => investigate
    g = dfx.classify_gap("borrower_name", ["Driver's License"])
    assert g["category_id"] == 323
    assert g["bucket"] == 1
    assert g["action"] == "investigate_bucket1_gap"


def test_plan_doc_gaps_inverts_and_filters_required():
    doc_field_map = {
        "Purchase Contract": ["purchase_price", "seller_name"],
        "Closing Disclosure": ["cd_total"],
        "Some Unrequired Doc": ["ignored_key"],
    }
    missing = {"purchase_price", "cd_total", "ignored_key", "orphan_key"}
    plan = dfx.plan_doc_gaps(
        missing, doc_field_map,
        required_doc_types={"Purchase Contract", "Closing Disclosure"},
    )
    keys = {g["field_key"] for g in plan}
    # ignored_key filtered (not required), orphan_key filtered (no owning doc)
    assert keys == {"purchase_price", "cd_total"}


def test_gap_doc_types_distinct_order_preserved():
    plan = [
        {"doc_type": "Driver's License", "field_key": "a"},
        {"doc_type": "ALTA Settlement Statement", "field_key": "b"},
        {"doc_type": "Driver's License", "field_key": "c"},
        {"doc_type": None, "field_key": "d"},
    ]
    assert dfx.gap_doc_types(plan) == ["Driver's License", "ALTA Settlement Statement"]


def test_manifest_coverage_summarizes_leaves():
    manifest = {
        "_processor": {"job_id": "job-7"},
        "documents": [
            {"root_attachment_id": "att-1", "category_id": 323,
             "metadata": {"owner": {"idNumber": "D1", "firstName": "A"}, "source": "x"}},
            {"root_attachment_id": "att-2", "metadata": {}},
        ],
    }
    cov = dfx.manifest_coverage(manifest)
    assert cov["job_id"] == "job-7"
    assert len(cov["docs"]) == 2
    d0 = cov["docs"][0]
    assert d0["root_attachment_id"] == "att-1" and d0["category_id"] == 323
    assert set(d0["leaf_keys"]) == {"owner.idNumber", "owner.firstName"}  # 'source' skipped
    assert cov["docs"][1]["leaf_count"] == 0


def _dl_manifest(id_number="D4913348"):
    return {
        "_processor": {"job_id": "job-1"},
        "documents": [{
            "root_attachment_id": "att-dl",
            "metadata": {"owner": {"idNumber": id_number, "firstName": "GERARDO",
                                   "lastName": "TORRES"}, "expirationDate": "07/15/2028"},
        }],
    }


ATT2DT = {"att-dl": "Driver's License"}


def test_resolve_and_fill_shadow_does_not_mutate():
    doc_fields = {}
    proposals = dfx.resolve_and_fill(doc_fields, _dl_manifest(), ATT2DT, apply=False)
    keys = {p["field_key"] for p in proposals}
    assert "dl_gov_id" in keys
    assert all(p["applied"] is False for p in proposals)
    assert doc_fields == {}  # shadow: nothing written


def test_resolve_and_fill_apply_writes_doc_fields():
    doc_fields = {}
    proposals = dfx.resolve_and_fill(doc_fields, _dl_manifest(), ATT2DT, apply=True)
    assert doc_fields["dl_gov_id"]["value"] == "D4913348"
    assert doc_fields["dl_gov_id"]["source_document"] == "tasktile_ai_only"
    assert doc_fields["dl_gov_id"]["raw_key"] == "owner.idNumber"
    assert any(p["field_key"] == "dl_gov_id" and p["applied"] for p in proposals)


def test_resolve_and_fill_never_clobbers_existing():
    doc_fields = {"dl_gov_id": {"value": "EXISTING"}}
    dfx.resolve_and_fill(doc_fields, _dl_manifest(), ATT2DT, apply=True)
    assert doc_fields["dl_gov_id"]["value"] == "EXISTING"  # untouched


def test_resolve_and_fill_skips_invalid_id():
    # '!!' fails the id_number validator -> no fill for dl_gov_id
    doc_fields = {}
    proposals = dfx.resolve_and_fill(doc_fields, _dl_manifest(id_number="!!"), ATT2DT, apply=True)
    assert "dl_gov_id" not in doc_fields
    assert "dl_gov_id" not in {p["field_key"] for p in proposals}


def test_resolve_and_fill_unmapped_doctype_skipped():
    man = {"documents": [{"root_attachment_id": "x", "metadata": {"foo": "bar"}}]}
    doc_fields = {}
    assert dfx.resolve_and_fill(doc_fields, man, {"x": "Totally Unknown"}, apply=True) == []
    assert doc_fields == {}


def test_summarize_plan_counts_actions():
    plan = [
        {"action": "fallback_landingai"},
        {"action": "fallback_landingai"},
        {"action": "investigate_bucket1_gap"},
    ]
    assert dfx.summarize_plan(plan) == {
        "fallback_landingai": 2,
        "investigate_bucket1_gap": 1,
    }
