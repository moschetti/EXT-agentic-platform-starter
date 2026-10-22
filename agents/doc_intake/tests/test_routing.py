from intake.doc_types import DOC_TYPES, OTHER, classification_labels, route


def test_failed_check_goes_to_review_whatever_the_confidence():
    r, reasons = route(0.99, {"policy_found": True, "iban_valid": False}, [], "motor_claim", 0.9, 0.75)
    assert r == "human_review"
    assert "check failed: iban_valid" in reasons


def test_missing_field_goes_to_review():
    r, reasons = route(0.99, {"policy_found": True}, ["iban"], "motor_claim", 0.9, 0.75)
    assert r == "human_review"
    assert "missing required field: iban" in reasons


def test_unknown_type_goes_to_review():
    assert route(1.0, {}, [], OTHER, 0.9, 0.75)[0] == "human_review"


def test_confidence_bands():
    ok = {"policy_found": True}
    assert route(0.95, ok, [], "address_change", 0.9, 0.75)[0] == "auto"
    assert route(0.80, ok, [], "address_change", 0.9, 0.75)[0] == "auto_with_audit"
    assert route(0.50, ok, [], "address_change", 0.9, 0.75)[0] == "human_review"


def test_every_type_declares_consistent_fields():
    for name, t in DOC_TYPES.items():
        for f in t["required"] + [t["policy_field"]]:
            assert f in t["fields"], (name, f)
        for key in ("date_field", "amount_field"):
            assert t[key] is None or t[key] in t["fields"], (name, key)
    assert OTHER in classification_labels()
