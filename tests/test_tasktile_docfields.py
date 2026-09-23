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


def test_classify_gap_bucket1_is_investigate():
    # 200 (Purchase Contract) is a tested bucket-1 category
    g = dfx.classify_gap("purchase_price", ["Purchase Contract"])
    assert g["category_id"] == 200
    assert g["bucket"] == 1
    assert g["action"] == "investigate_bucket1_gap"
    assert g["tested"] is True


def test_classify_gap_bucket3_default_fallback():
    g = dfx.classify_gap("some_field", ["Property Tax"])  # 1481 untested -> bucket 3
    assert g["category_id"] == 1481
    assert g["bucket"] == 3
    assert g["action"] == "fallback_landingai"


def test_classify_gap_unknown_doc_type():
    g = dfx.classify_gap("mystery", ["Totally Unknown"])
    assert g["category_id"] is None
    assert g["action"] == "fallback_landingai"


def test_classify_gap_cross_doc_when_available():
    # 2168 ALTA has cross_doc_source (-> 167 CPL); with field_overrides it is bucket 2 material
    g = dfx.classify_gap("escrow_company", ["ALTA Settlement Statement"])
    assert g["category_id"] == 2168
    assert g["cross_doc_source"] == 167
    assert g["bucket"] == 2          # escrowCompany override pins it
    assert g["action"] == "cross_doc"


def test_classify_gap_structural_id_gap_not_flagged_as_regression():
    # DL category is bucket 1, but owner.idNumber is pinned to bucket 2. A missing
    # id number must NOT be flagged as a bucket-1 regression.
    g = dfx.classify_gap("dl_id_number", ["Driver's License"])
    assert g["category_id"] == 323
    assert g["bucket"] == 2
    assert g["action"] == "known_missing_fallback"  # 323 has no cross_doc source
    assert g["cross_doc_source"] is None


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
