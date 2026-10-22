"""Document types the intake process handles: what to extract and which checks must pass.

Used by the SQL pipeline (notebook 04) and by the agent, so both extract the same fields and apply the
same checks. Add a document type here and both pick it up.

Field types follow the ``ai_extract`` schema format: string, number, integer, boolean, enum.
"""

DOC_TYPES = {
    "motor_claim": {
        "description": "A claim form for damage to a vehicle covered by a motor policy.",
        "fields": {
            "policy_number": {"type": "string", "description": "Policy number, starts with P"},
            "claimant_name": {"type": "string", "description": "Name of the policyholder making the claim"},
            "incident_date": {"type": "string", "description": "Date of the incident, as YYYY-MM-DD"},
            "incident_location": {"type": "string", "description": "Where the incident happened"},
            "vehicle_registration": {"type": "string", "description": "Registration plate of the insured vehicle"},
            "third_party_involved": {"type": "boolean", "description": "True if another vehicle or person was involved"},
            "estimated_amount_eur": {"type": "number", "description": "Estimated repair cost in EUR"},
            "iban": {"type": "string", "description": "Bank account for the payment, as an IBAN"},
        },
        "required": ["policy_number", "claimant_name", "incident_date", "estimated_amount_eur", "iban"],
        "policy_field": "policy_number",
        "date_field": "incident_date",
        "amount_field": "estimated_amount_eur",
        "checks": ["policy_found", "policy_active_on_date", "within_coverage", "iban_valid"],
    },
    "property_claim": {
        "description": "A claim form for damage to a home or its contents covered by a home policy.",
        "fields": {
            "policy_number": {"type": "string", "description": "Policy number, starts with P"},
            "claimant_name": {"type": "string", "description": "Name of the policyholder making the claim"},
            "incident_date": {"type": "string", "description": "Date the damage happened, as YYYY-MM-DD"},
            "damage_type": {"type": "enum", "labels": ["water", "fire", "theft", "storm", "other"],
                            "description": "Main cause of the damage"},
            "property_address": {"type": "string", "description": "Address of the insured property"},
            "estimated_amount_eur": {"type": "number", "description": "Estimated cost of the damage in EUR"},
            "iban": {"type": "string", "description": "Bank account for the payment, as an IBAN"},
        },
        "required": ["policy_number", "claimant_name", "incident_date", "damage_type", "estimated_amount_eur", "iban"],
        "policy_field": "policy_number",
        "date_field": "incident_date",
        "amount_field": "estimated_amount_eur",
        "checks": ["policy_found", "policy_active_on_date", "within_coverage", "iban_valid"],
    },
    "repair_invoice": {
        "description": "An invoice from a repair shop or contractor for work related to a claim.",
        "fields": {
            "invoice_number": {"type": "string", "description": "Invoice number"},
            "vendor_name": {"type": "string", "description": "Company that issued the invoice"},
            "vendor_vat_id": {"type": "string", "description": "VAT identification number of the vendor"},
            "invoice_date": {"type": "string", "description": "Invoice date, as YYYY-MM-DD"},
            "policy_or_claim_reference": {"type": "string", "description": "Insurance policy or claim number the invoice refers to"},
            "total_amount_eur": {"type": "number", "description": "Total amount including VAT, in EUR"},
            "vat_amount_eur": {"type": "number", "description": "VAT amount in EUR"},
            "iban": {"type": "string", "description": "Vendor bank account for the payment, as an IBAN"},
        },
        "required": ["invoice_number", "vendor_name", "vendor_vat_id", "invoice_date", "policy_or_claim_reference",
                     "total_amount_eur", "iban"],
        "policy_field": "policy_or_claim_reference",
        "date_field": "invoice_date",
        "amount_field": "total_amount_eur",
        "checks": ["policy_found", "within_coverage", "iban_valid"],
    },
    "address_change": {
        "description": "A request from a customer to change their address.",
        "fields": {
            "customer_name": {"type": "string", "description": "Name of the customer"},
            "policy_number": {"type": "string", "description": "Policy number, starts with P"},
            "old_address": {"type": "string", "description": "Current address"},
            "new_address": {"type": "string", "description": "New address"},
            "effective_date": {"type": "string", "description": "Date the new address applies from, as YYYY-MM-DD"},
        },
        "required": ["customer_name", "policy_number", "new_address", "effective_date"],
        "policy_field": "policy_number",
        "date_field": None,
        "amount_field": None,
        "checks": ["policy_found"],
    },
}

# Documents that match none of the types above. They always go to human review.
OTHER = "other"
OTHER_DESCRIPTION = "Anything else: marketing, newsletters, invitations, or documents we do not process."

ROUTES = ("auto", "auto_with_audit", "human_review")


def classification_labels() -> dict[str, str]:
    """Label -> description, in the format ai_classify accepts."""
    return {**{name: t["description"] for name, t in DOC_TYPES.items()}, OTHER: OTHER_DESCRIPTION}


def extraction_schema(doc_type: str) -> dict:
    """The ai_extract schema for one document type."""
    return DOC_TYPES[doc_type]["fields"]


def route(confidence: float, checks: dict[str, bool], missing_required: list[str], doc_type: str,
          auto_threshold: float, audit_threshold: float) -> tuple[str, list[str]]:
    """Decide the route for one document and return it with the reasons.

    Failed checks and missing required fields always send a document to human review, whatever the
    confidence. Only clean documents are routed on confidence.
    """
    reasons = []
    if doc_type not in DOC_TYPES:
        reasons.append("document type is not one we process")
    reasons += [f"missing required field: {f}" for f in missing_required]
    reasons += [f"check failed: {name}" for name, ok in checks.items() if not ok]
    if reasons:
        return "human_review", reasons
    if confidence >= auto_threshold:
        return "auto", [f"confidence {confidence:.2f} >= {auto_threshold}"]
    if confidence >= audit_threshold:
        return "auto_with_audit", [f"confidence {confidence:.2f} between {audit_threshold} and {auto_threshold}"]
    return "human_review", [f"confidence {confidence:.2f} < {audit_threshold}"]
