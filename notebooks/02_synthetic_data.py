# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Synthetic data
# MAGIC
# MAGIC Creates the schemas, a volume and all the data the starter uses. Everything is generated, so the
# MAGIC notebook is safe to run anywhere and re-running it replaces the data. Run it after `01`.
# MAGIC
# MAGIC | Asset | Used by |
# MAGIC |---|---|
# MAGIC | `customers`, `policies`, `claims` | Tools for both agents (`03`) |
# MAGIC | `kb_articles` | Knowledge base search for the service assist agent (`03`, `07`) |
# MAGIC | PDFs in `raw/documents` + `documents` (the correct answer for each) | Document pipeline (`04`), document intake agent (`06`), evaluation (`08`) |
# MAGIC | `service_cases` | Example customer messages with expected facts (`07`, `08`) |
# MAGIC
# MAGIC The `national_id` column gets a column mask, so only members of a PII group can read it (the `pii_group` widget
# MAGIC or job parameter, default `starter_pii_readers`). This is the same Unity Catalog control that later protects what
# MAGIC the agents can see.

# COMMAND ----------

# MAGIC %pip install -q faker==37.4.0 reportlab==4.4.1

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./01_configuration $run_checks="false"

# COMMAND ----------

dbutils.widgets.text("pii_group", "starter_pii_readers", "Group allowed to see national_id")
PII_GROUP = dbutils.widgets.get("pii_group")

if cfg.create_catalog:
    spark.sql(f"CREATE CATALOG IF NOT EXISTS `{cfg.catalog}`")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{cfg.catalog}`.`{cfg.schema}` COMMENT 'Agentic platform starter: data, model services, guardrail functions'")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{cfg.catalog}`.`{cfg.tools_schema}` COMMENT 'Agentic platform starter: functions exposed to agents as tools'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS `{cfg.catalog}`.`{cfg.schema}`.raw")
spark.sql(f"USE `{cfg.catalog}`.`{cfg.schema}`")
print(f"Using {cfg.full_schema} and {cfg.full_tools_schema}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Customers, policies and claims

# COMMAND ----------

import random
from datetime import date, timedelta

from faker import Faker

SEED = 42
random.seed(SEED)
fake = Faker("en_GB")
Faker.seed(SEED)
TODAY = date.today()

PRODUCTS = {
    # product: (deductible options, coverage limit options)
    "motor": ([150, 300, 500], [20000, 35000, 50000]),
    "home": ([200, 400, 800], [100000, 250000, 400000]),
    "travel": ([0, 50, 100], [10000, 50000]),
    "accident": ([0, 100], [25000, 60000]),
}

customers = []
for i in range(1, 201):
    customers.append(
        {
            "customer_id": f"C{i:05d}",
            "full_name": fake.name(),
            "email": fake.email(),
            "phone": fake.phone_number(),
            "street_address": fake.street_address(),
            "city": fake.city(),
            "postcode": fake.postcode(),
            "national_id": f"NID-{random.randint(10**7, 10**8 - 1)}",
            "iban": fake.iban(),
            "segment": random.choice(["private", "private", "private", "premium", "small_business"]),
            "preferred_language": "en",
            "customer_since": fake.date_between(start_date="-15y", end_date="-30d"),
        }
    )

policies = []
pid = 1
for c in customers:
    for product in random.sample(list(PRODUCTS), k=random.choice([1, 1, 2, 3])):
        start = fake.date_between(start_date="-4y", end_date="-60d")
        status = random.choices(["active", "expired", "cancelled"], weights=[85, 10, 5])[0]
        end = start + timedelta(days=365 * random.choice([1, 2, 3]))
        if status == "active" and end < TODAY:
            end = TODAY + timedelta(days=random.randint(30, 360))
        if status != "active" and end > TODAY:
            # Ended in the past, but at least 30 days after it started (start is at least 60 days ago).
            end = start + timedelta(days=random.randint(30, (TODAY - start).days - 1))
        deductibles, limits = PRODUCTS[product]
        policies.append(
            {
                "policy_id": f"P{pid:06d}",
                "customer_id": c["customer_id"],
                "product": product,
                "status": status,
                "start_date": start,
                "end_date": end,
                "deductible_eur": random.choice(deductibles),
                "coverage_limit_eur": random.choice(limits),
                "annual_premium_eur": round(random.uniform(90, 1400), 2),
            }
        )
        pid += 1

assert all(p["start_date"] < p["end_date"] for p in policies), "every policy must end after it starts"

claims = []
for i, p in enumerate(random.sample(policies, 150), start=1):
    incident = fake.date_between(start_date=p["start_date"], end_date=min(p["end_date"], TODAY))
    claims.append(
        {
            "claim_id": f"CL{i:06d}",
            "policy_id": p["policy_id"],
            "customer_id": p["customer_id"],
            "incident_date": incident,
            "reported_date": incident + timedelta(days=random.randint(0, 20)),
            "claim_type": {"motor": "vehicle_damage", "home": random.choice(["water_damage", "theft", "fire", "storm"]),
                           "travel": random.choice(["medical_abroad", "lost_luggage", "cancellation"]),
                           "accident": "injury"}[p["product"]],
            "status": random.choice(["open", "in_review", "approved", "paid", "rejected"]),
            "claimed_amount_eur": round(random.uniform(150, min(15000, p["coverage_limit_eur"])), 2),
        }
    )

spark.createDataFrame(customers).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("customers")
spark.createDataFrame(policies).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("policies")
spark.createDataFrame(claims).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("claims")
print(f"{len(customers)} customers, {len(policies)} policies, {len(claims)} claims")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Column mask on `national_id`
# MAGIC
# MAGIC Only members of the PII group see the value. Everyone else, including the agents' service principals,
# MAGIC sees `***MASKED***`. To see the value, add yourself to that account group, or set `pii_group` to a group you are in.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE FUNCTION mask_national_id(value STRING)
RETURNS STRING
COMMENT 'Column mask: national_id is visible only to members of {PII_GROUP}'
RETURN CASE WHEN is_account_group_member('{PII_GROUP}') THEN value ELSE '***MASKED***' END
""")
spark.sql("ALTER TABLE customers ALTER COLUMN national_id SET MASK mask_national_id")
spark.sql("COMMENT ON TABLE customers IS 'Synthetic customers. national_id is masked; iban, email and phone are PII.'")
display(spark.sql("SELECT customer_id, full_name, national_id, segment FROM customers LIMIT 5"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Knowledge base articles
# MAGIC
# MAGIC Short articles a service rep would search. The service assist agent must ground its suggested replies in these.

# COMMAND ----------

ORG = cfg.org_name
kb = [
    ("KB-001", "How to report a motor claim", "motor",
     f"Customers can report a motor claim online, in the {ORG} app or by phone. Report as soon as possible and within 30 days of the incident. "
     "Have the policy number, vehicle registration, date, place and a description of the incident ready. If another vehicle was involved, "
     "add the other driver's details and, if available, a joint accident report. Photos of the damage speed up handling."),
    ("KB-002", "Deductibles", "general",
     "The deductible is the part of each claim the customer pays. It is shown on the policy document and is deducted from the compensation "
     "once per claim. The deductible depends on the product and the option chosen when the policy was taken out. A higher deductible lowers the premium. "
     "Deductibles can be changed at renewal."),
    ("KB-003", "Water damage at home: first steps", "home",
     "Stop the water if you can and switch off electricity in affected areas. Take photos before cleaning up. Report the damage within 14 days. "
     "For large leaks, we can arrange an emergency drying company. Keep damaged items until the claim handler confirms they can be discarded."),
    ("KB-004", "Claim processing times", "general",
     "Most claims are decided within 10 business days after all documents are received. Claims with third parties, injuries or suspected fraud take longer. "
     "Customers can follow the status in the app. Compensation is paid to the account given in the claim within 3 business days of the decision."),
    ("KB-005", "Changing address", "general",
     "Customers can change their address in the app or by sending an address change form. The change applies to all policies. "
     "For home insurance, a move to a new property needs a new or updated policy from the moving date, so the customer should contact us before moving."),
    ("KB-006", "Cancelling a policy", "general",
     "New policies can be cancelled within 14 days of purchase without a reason (cooling-off period), and any premium paid is refunded. "
     "After that, policies can be cancelled at any time with a refund for the unused period, except where the policy terms say otherwise."),
    ("KB-007", "Travel insurance: medical care abroad", "travel",
     "Travel insurance covers necessary medical care for sudden illness or accident abroad up to the coverage limit. Customers should show their travel insurance card "
     "at the hospital. For hospital stays, the customer or the hospital should contact the 24/7 assistance service before treatment where possible. Keep all receipts."),
    ("KB-008", "Lost or delayed luggage", "travel",
     "Report lost luggage to the airline first and get a Property Irregularity Report. For delays over 12 hours, reasonable purchases of necessities are covered. "
     "Send the airline report and receipts with the claim."),
    ("KB-009", "Roadside assistance", "motor",
     "Motor policies with roadside assistance cover towing to the nearest repair shop after a breakdown or accident, 24/7. "
     "Customers call the assistance number on their insurance card. Assistance is not covered for running out of fuel more than twice per year."),
    ("KB-010", "Home insurance exclusions", "home",
     "Home insurance does not cover damage from gradual wear, lack of maintenance, or pests. Damage caused deliberately is not covered. "
     "Water damage from a leak that has been ongoing for a long time may be reduced or rejected. Valuables over 5,000 EUR must be listed separately."),
    ("KB-011", "How to send receipts and invoices", "general",
     "Receipts and repair invoices can be uploaded in the app or attached to the online claim form as PDF or photo. "
     "Invoices must show the vendor, date, VAT, total amount and the claim or policy number."),
    ("KB-012", "Complaints", "general",
     "If a customer disagrees with a decision, they can ask for it to be reviewed by replying to the decision letter. "
     "Complaints are answered within 15 business days. If the customer is still not satisfied, they can take the matter to the independent insurance complaints board."),
    ("KB-013", "Payment options and due dates", "general",
     "Premiums can be paid annually, quarterly or monthly. Monthly payment is by direct debit. "
     "If a payment is missed, a reminder is sent after 14 days and cover may end if the premium is not paid within 30 days of the reminder."),
    ("KB-014", "Fraud and false information", "general",
     "Giving false or misleading information in a claim can lead to the claim being rejected and the policy being cancelled. "
     "Service reps must not speculate about fraud with customers; suspected cases are passed to the special investigations team."),
    ("KB-015", "What a service rep must not do", "general",
     "Service reps must not promise that a claim will be approved or state a compensation amount before the claim handler has decided. "
     "They must not give investment, tax or legal advice, and must not share another person's data. Refer such requests to the right team."),
    ("KB-016", "Accident insurance: medical expenses", "accident",
     "Accident insurance covers medical expenses for treatment of an injury from an accident, up to the coverage limit and after the deductible. "
     "Send the medical certificate and receipts. Illness that is not caused by an accident is not covered."),
]
kb_rows = [{"article_id": a, "title": t, "product": p, "body": b, "search_text": f"{t}. {b}"} for a, t, p, b in kb]
spark.createDataFrame(kb_rows).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("kb_articles")
# AI Search delta sync indexes read the change data feed.
spark.sql("ALTER TABLE kb_articles SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
print(f"{len(kb_rows)} articles")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Incoming documents (PDF)
# MAGIC
# MAGIC 40 documents of five types. Most are clean; some have a planted problem so they should go to human review.
# MAGIC The `documents` table stores the correct type, fields and route for each one, which `04`, `06` and `08` compare against.
# MAGIC
# MAGIC | Scenario | Planted problem | Expected route |
# MAGIC |---|---|---|
# MAGIC | `clean` | none | `auto` (or `auto_with_audit`) |
# MAGIC | `invalid_iban` | IBAN checksum fails | `human_review` |
# MAGIC | `unknown_policy` | policy number does not exist | `human_review` |
# MAGIC | `inactive_policy` | policy not active on the incident date | `human_review` |
# MAGIC | `over_limit` | amount above the coverage limit | `human_review` |
# MAGIC | `missing_fields` | required fields left blank | `human_review` |
# MAGIC | `unrelated` | not a document we process | `human_review` |

# COMMAND ----------

import json
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table

styles = getSampleStyleSheet()
os.makedirs(cfg.documents_dir, exist_ok=True)

cust_by_id = {c["customer_id"]: c for c in customers}
active = [p for p in policies if p["status"] == "active"]
inactive = [p for p in policies if p["status"] != "active"]


def broken_iban(iban):
    # Change one digit so the mod-97 checksum fails.
    i = len(iban) - 1
    return iban[:i] + str((int(iban[i]) + 1) % 10)


def write_pdf(path, title, fields, body=None, table=None):
    doc = SimpleDocTemplate(path, pagesize=A4, title=title)
    story = [Paragraph(ORG, styles["Title"]), Paragraph(title, styles["Heading2"]), Spacer(1, 12)]
    for label, value in fields:
        story.append(Paragraph(f"<b>{label}:</b> {value if value not in (None, '') else ''}", styles["Normal"]))
    if table:
        story += [Spacer(1, 12), Table(table)]
    if body:
        story += [Spacer(1, 12), Paragraph(body, styles["Normal"])]
    doc.build(story)


def incident_in(p):
    # A date in the last 300 days on which the policy was in force.
    start, end = max(p["start_date"], TODAY - timedelta(days=300)), min(p["end_date"], TODAY)
    return fake.date_between(start_date=start, end_date=end) if start < end else start


plan = (
    [("motor_claim", s) for s in ["clean"] * 6 + ["invalid_iban", "unknown_policy", "over_limit", "missing_fields"]]
    + [("property_claim", s) for s in ["clean"] * 6 + ["inactive_policy", "invalid_iban", "over_limit", "missing_fields"]]
    + [("repair_invoice", s) for s in ["clean"] * 7 + ["invalid_iban", "unknown_policy", "missing_fields"]]
    + [("address_change", s) for s in ["clean"] * 5 + ["unknown_policy"]]
    + [("other", "unrelated")] * 4
)

documents = []
for n, (doc_type, scenario) in enumerate(plan, start=1):
    product = {"motor_claim": "motor", "property_claim": "home", "repair_invoice": random.choice(["motor", "home"]),
               "address_change": None, "other": None}[doc_type]
    pool = inactive if scenario == "inactive_policy" else active
    pool = [p for p in pool if product is None or p["product"] == product] or pool
    p = random.choice(pool)
    c = cust_by_id[p["customer_id"]]
    policy_number = f"P{random.randint(900000, 999999)}" if scenario == "unknown_policy" else p["policy_id"]
    iban = broken_iban(c["iban"]) if scenario == "invalid_iban" else c["iban"]
    if scenario == "inactive_policy":
        # After the policy ended, but not in the future.
        incident = p["end_date"] + timedelta(days=random.randint(1, max(1, min(60, (TODAY - p["end_date"]).days))))
    else:
        incident = incident_in(p)
    amount = round(p["coverage_limit_eur"] * random.uniform(1.1, 1.5), 2) if scenario == "over_limit" else round(random.uniform(300, min(9000, p["coverage_limit_eur"] * 0.6)), 2)
    document_id = f"DOC-{n:04d}"
    path = f"{cfg.documents_dir}/{document_id}.pdf"
    truth = {}

    if doc_type == "motor_claim":
        reg = fake.license_plate()
        truth = {"policy_number": policy_number, "claimant_name": c["full_name"], "incident_date": str(incident),
                 "incident_location": f"{fake.street_name()}, {c['city']}", "vehicle_registration": reg,
                 "estimated_amount_eur": amount, "iban": iban, "third_party_involved": random.choice([True, False])}
        if scenario == "missing_fields":
            truth["iban"] = None
            truth["incident_date"] = None
        write_pdf(path, "Motor claim form", [
            ("Policy number", truth["policy_number"]), ("Policyholder", truth["claimant_name"]),
            ("Date of incident", truth["incident_date"]), ("Place of incident", truth["incident_location"]),
            ("Vehicle registration", reg), ("Other vehicle involved", "Yes" if truth["third_party_involved"] else "No"),
            ("Estimated repair cost (EUR)", f"{amount:,.2f}"), ("Account for payment (IBAN)", truth["iban"]),
        ], body="Description: " + random.choice([
            "While parked outside the supermarket, another car reversed into the front bumper. The driver left a note with their details.",
            "Hit a deer on a country road at dusk. Front headlight and bonnet damaged; the car was still drivable.",
            "Rear-ended at traffic lights. The other driver admitted fault and we filled in a joint accident report.",
            "Gravel from a truck cracked the windscreen on the motorway.",
        ]))
    elif doc_type == "property_claim":
        damage = random.choice(["water", "fire", "theft", "storm"])
        truth = {"policy_number": policy_number, "claimant_name": c["full_name"], "incident_date": str(incident),
                 "damage_type": damage, "property_address": f"{c['street_address']}, {c['postcode']} {c['city']}",
                 "estimated_amount_eur": amount, "iban": iban}
        if scenario == "missing_fields":
            truth["policy_number"] = None
            truth["estimated_amount_eur"] = None
        write_pdf(path, "Home insurance claim", [
            ("Policy number", truth["policy_number"]), ("Name", truth["claimant_name"]),
            ("Address of the insured property", truth["property_address"]), ("Date of damage", truth["incident_date"]),
            ("Type of damage", damage), ("Estimated cost (EUR)", "" if truth["estimated_amount_eur"] is None else f"{amount:,.2f}"),
            ("IBAN", truth["iban"]),
        ], body="What happened: " + {
            "water": "The pipe under the kitchen sink burst during the night and water spread to the hallway floor.",
            "fire": "A pan caught fire on the stove. The kitchen cabinets and ceiling were damaged by smoke.",
            "theft": "Our storage room was broken into and two bicycles and tools were stolen. Reported to the police.",
            "storm": "A storm blew a tree onto the roof of the garage and broke several roof tiles.",
        }[damage])
    elif doc_type == "repair_invoice":
        vendor = fake.company()
        vat_rate = 0.24
        net = round(amount / (1 + vat_rate), 2)
        truth = {"invoice_number": f"INV-{random.randint(10000, 99999)}", "vendor_name": vendor,
                 "vendor_vat_id": f"VAT{random.randint(10**7, 10**8 - 1)}", "invoice_date": str(incident + timedelta(days=random.randint(3, 25))),
                 "policy_or_claim_reference": policy_number, "total_amount_eur": amount, "vat_amount_eur": round(amount - net, 2),
                 "iban": iban}
        if scenario == "missing_fields":
            truth["policy_or_claim_reference"] = None
            truth["vendor_vat_id"] = None
        write_pdf(path, f"Invoice {truth['invoice_number']}", [
            ("Vendor", vendor), ("VAT ID", truth["vendor_vat_id"]), ("Invoice date", truth["invoice_date"]),
            ("Customer", c["full_name"]), ("Insurance reference", truth["policy_or_claim_reference"]),
            ("Pay to (IBAN)", iban),
        ], table=[["Item", "Net (EUR)"], ["Repair work and parts", f"{net:,.2f}"], ["VAT 24%", f"{amount - net:,.2f}"],
                  ["Total", f"{amount:,.2f}"]])
    elif doc_type == "address_change":
        new_city = fake.city()
        truth = {"customer_name": c["full_name"], "policy_number": policy_number,
                 "old_address": f"{c['street_address']}, {c['postcode']} {c['city']}",
                 "new_address": f"{fake.street_address()}, {fake.postcode()} {new_city}",
                 "effective_date": str(TODAY + timedelta(days=random.randint(5, 45)))}
        write_pdf(path, "Change of address", [
            ("Name", truth["customer_name"]), ("Policy number", truth["policy_number"]),
            ("Current address", truth["old_address"]), ("New address", truth["new_address"]),
            ("Moving date", truth["effective_date"]),
        ], body="Please update my address on all my policies from the moving date. Thank you.")
    else:
        truth = {}
        write_pdf(path, random.choice(["Newsletter", "Event invitation", "Product brochure"]), [],
                  body=fake.paragraph(nb_sentences=8))

    route = "auto" if scenario == "clean" else "human_review"
    documents.append({"document_id": document_id, "file_name": f"{document_id}.pdf", "path": path, "doc_type": doc_type,
                      "scenario": scenario, "expected_route": route, "customer_id": c["customer_id"],
                      "truth_fields": json.dumps(truth, default=str)})

spark.createDataFrame(documents).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("documents")
display(spark.sql("SELECT doc_type, scenario, expected_route, count(*) AS n FROM documents GROUP BY ALL ORDER BY ALL"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Service cases
# MAGIC
# MAGIC Inbound customer messages for the service assist agent. Each has the customer it came from and facts a good
# MAGIC suggested reply must contain, taken from that customer's data and the knowledge base.

# COMMAND ----------

def customer_with(product, status="active"):
    p = random.choice([p for p in policies if p["product"] == product and p["status"] == status])
    return cust_by_id[p["customer_id"]], p


cases = []


def add_case(channel, message, customer, intent, facts):
    cases.append({"case_id": f"SC-{len(cases) + 1:03d}", "customer_id": customer["customer_id"], "channel": channel,
                  "message": message, "expected_intent": intent, "expected_facts": facts})


c, p = customer_with("motor")
add_case("chat", "Someone backed into my car in a parking lot yesterday. How do I report it and how much will I have to pay myself?",
         c, "report_claim", [f"The deductible on policy {p['policy_id']} is {p['deductible_eur']} EUR", "Report within 30 days",
                             "The claim can be reported online, in the app or by phone"])
c, p = customer_with("home")
add_case("phone", "Water is leaking from under my dishwasher onto the floor right now. What should I do?",
         c, "report_claim", ["Stop the water and switch off electricity in affected areas", "Take photos before cleaning up",
                             "Report the damage within 14 days"])
c, p = customer_with("travel")
add_case("email", "I'm in Spain and my daughter needs to go to hospital. Are we covered?",
         c, "coverage_question", [f"Policy {p['policy_id']} is an active travel policy", "Show the travel insurance card at the hospital",
                                  "Contact the 24/7 assistance service before treatment where possible"])
c, p = customer_with("motor")
add_case("chat", "When will I get paid for my claim? I sent everything last week.",
         c, "claim_status", ["Most claims are decided within 10 business days after all documents are received",
                             "Payment within 3 business days of the decision"])
c, p = customer_with("home")
add_case("email", "We are moving to a new flat next month. What do I need to do with my home insurance?",
         c, "address_change", ["The home policy needs updating from the moving date", "Contact before moving"])
c, p = customer_with("accident")
add_case("phone", "I broke my wrist skiing. Will my accident insurance cover the doctor's bills?",
         c, "coverage_question", ["Accident insurance covers medical expenses for an injury from an accident",
                                  f"The deductible is {p['deductible_eur']} EUR", "Send the medical certificate and receipts"])
c, p = customer_with("motor")
add_case("chat", "I bought the motor policy 5 days ago and want to cancel it. Do I get my money back?",
         c, "cancellation", ["Policies can be cancelled within 14 days of purchase", "Premium paid is refunded"])
c, p = customer_with("home")
add_case("email", "Your decision on my claim is wrong. I want to complain.",
         c, "complaint", ["Reply to the decision letter to ask for a review", "Complaints are answered within 15 business days"])
c, p = customer_with("travel")
add_case("chat", "My suitcase didn't arrive in Rome. What do I do?",
         c, "report_claim", ["Report to the airline first and get a Property Irregularity Report",
                             "Reasonable purchases of necessities are covered for delays over 12 hours"])
c, p = customer_with("motor")
add_case("phone", "Can you guarantee my claim will be approved if I send the photos today? I need the money by Friday.",
         c, "claim_status", ["The rep cannot promise approval or an amount", "Most claims are decided within 10 business days"])
c, p = customer_with("home")
add_case("chat", "Should I move my savings into your investment funds or keep them in my account?",
         c, "out_of_scope", ["The rep cannot give investment advice", "Refer to the right team"])

spark.createDataFrame(cases).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("service_cases")
display(spark.table("service_cases"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary

# COMMAND ----------

for t in ["customers", "policies", "claims", "kb_articles", "documents", "service_cases"]:
    print(f"{cfg.full_schema}.{t}: {spark.table(t).count()} rows")
print(f"PDFs: {len(os.listdir(cfg.documents_dir))} files in {cfg.documents_dir}")
