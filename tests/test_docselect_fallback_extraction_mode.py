"""Regression test for Issue A: fallback doc-selection must keep extraction_mode.

Before the fix, ``get_required_documents_for_loan`` returned ``{}`` for the
extraction_mode on the fallback branch, silently degrading every doc to
selectionMode=Best / is_multi_copy=False. That dropped the co-borrower's
Driver's License (and any 2nd copy of VOE/paystubs/etc.) from doc_fields.
"""
from output.tools.data_gathering import get_required_documents_for_loan


def test_fallback_returns_extraction_mode_multicopy():
    # An unmatched loan profile falls through to the fallback condition entry.
    doc_list, ext_modes = get_required_documents_for_loan(
        loan_type="__no_such_loan_type__",
        loan_purpose="__no_such_purpose__",
        borrower_count=2,
    )
    assert doc_list, "fallback must still return a document list"
    # The fallback entry marks Driver's License (+ VOE/Paystubs/W-2/...) as 'all'.
    assert ext_modes, "fallback must carry its extraction_mode map (Issue A regression)"
    assert str(ext_modes.get("Driver's License", "")).lower() == "all"
    # _comment helper key must be stripped
    assert "_comment" not in ext_modes


def test_fallback_multicopy_set_is_nonempty():
    _, ext_modes = get_required_documents_for_loan("x", "y", 2)
    multi = {dt for dt, mode in ext_modes.items() if str(mode).lower() == "all"}
    assert "Driver's License" in multi
    assert len(multi) >= 2
