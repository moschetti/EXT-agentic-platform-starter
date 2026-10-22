# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Document processing with AI Functions (SQL, no agent)
# MAGIC
# MAGIC Many document processes don't need an agent. This notebook processes all incoming PDFs with built-in
# MAGIC SQL functions and no model endpoints to manage:
# MAGIC
# MAGIC 1. `ai_parse_document`: PDF → text and layout
# MAGIC 2. `ai_classify`: document type
# MAGIC 3. `ai_extract` (v2.1): fields for that type, **with a confidence score per field**
# MAGIC 4. Unity Catalog functions from `03`: policy and IBAN checks
# MAGIC 5. Routing: `auto`, `auto_with_audit` or `human_review`, using the thresholds in `01`
# MAGIC
# MAGIC The same document types, fields and checks (`agents/doc_intake/intake/doc_types.py`) drive the
# MAGIC document intake agent in `06`. Use this pipeline for high-volume batch work. Use the agent when a
# MAGIC request needs to be handled one at a time over an API, needs reasoning between steps, or needs a
# MAGIC full trace of each decision.
# MAGIC
# MAGIC Each step writes a Delta table, so every intermediate result can be inspected and has lineage.

# COMMAND ----------

# MAGIC %run ./01_configuration $run_checks="false"

# COMMAND ----------

import json
import os
import sys

sys.path.insert(0, os.path.abspath("../agents/doc_intake"))
from intake.doc_types import DOC_TYPES, OTHER, classification_labels, extraction_schema

S = f"`{cfg.catalog}`.`{cfg.schema}`"
T = f"`{cfg.catalog}`.`{cfg.tools_schema}`"
spark.sql(f"USE {S}")


def sql_str(value: str) -> str:
    """Quote a Python string as a SQL string literal."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · Parse

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TABLE doc_parsed AS
SELECT
  regexp_extract(path, '([^/]+)\\\\.pdf$', 1) AS document_id,
  path,
  concat_ws('\\n', transform(variant_get(parsed, '$.document.elements', 'ARRAY<VARIANT>'),
                            e -> variant_get(e, '$.content', 'STRING'))) AS text,
  variant_get(parsed, '$.error_status', 'STRING') AS parse_error
FROM (
  SELECT path, ai_parse_document(content, map('version', '2.0', 'descriptionElementTypes', '')) AS parsed
  FROM read_files('{cfg.documents_dir}', format => 'binaryFile')
)
""")
display(spark.sql("SELECT document_id, parse_error, left(text, 300) AS text_start FROM doc_parsed ORDER BY document_id LIMIT 5"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Classify

# COMMAND ----------

labels = sql_str(json.dumps(classification_labels()))
spark.sql(f"""
CREATE OR REPLACE TABLE doc_classified AS
SELECT *,
  ai_classify(text, {labels},
              map('version', '2.0', 'instructions', 'Documents received by the claims and customer service teams of an insurer.')
             ):response[0]::STRING AS doc_type
FROM doc_parsed
WHERE parse_error IS NULL AND text IS NOT NULL
""")
display(spark.sql("SELECT doc_type, count(*) AS n FROM doc_classified GROUP BY ALL ORDER BY ALL"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Extract, with a confidence score per field
# MAGIC
# MAGIC One `ai_extract` call per document type, each with that type's schema. With `enableConfidenceScores`, every
# MAGIC field comes back as `{value, confidence_score}`.

# COMMAND ----------

selects = []
for doc_type in DOC_TYPES:
    schema = sql_str(json.dumps(extraction_schema(doc_type)))
    selects.append(f"""
SELECT document_id, doc_type, text,
  ai_extract(text, {schema},
             map('version', '2.1', 'enableConfidenceScores', 'true',
                 'instructions', 'Extract exactly what the document says. Leave a field empty if it is missing.')) AS extraction
FROM doc_classified WHERE doc_type = '{doc_type}'""")
selects.append(f"SELECT document_id, doc_type, text, NULL AS extraction FROM doc_classified WHERE doc_type = '{OTHER}'")
spark.sql("CREATE OR REPLACE TABLE doc_extracted AS " + "\nUNION ALL\n".join(selects))
display(spark.sql("SELECT document_id, doc_type, extraction:response AS fields FROM doc_extracted ORDER BY document_id LIMIT 5"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Check and 5 · Route
# MAGIC
# MAGIC Checks run as Unity Catalog functions, so the pipeline and the agent call the same governed logic. The routing rule:
# MAGIC
# MAGIC - Any failed check or missing required field → `human_review`
# MAGIC - Otherwise the **lowest** confidence across the required fields decides: `≥ auto_threshold` → `auto`,
# MAGIC   `≥ audit_threshold` → `auto_with_audit`, below → `human_review`

# COMMAND ----------

def field(name, cast="STRING"):
    return f"try_cast(e.extraction:response:{name}:value::STRING AS {cast})" if name else f"CAST(NULL AS {cast})"


def sql_array(values):
    return "array(" + ", ".join(f"'{v}'" for v in values) + ")"


# Step 4a: copy what the checks need out of the VARIANT into typed columns. The check function is called per row
# (LATERAL), and a per-row call cannot take arguments that reference a VARIANT column.
selects = []
for doc_type, t in DOC_TYPES.items():
    req = t["required"]
    missing = " || ".join(
        f"CASE WHEN coalesce(trim({field(f)}), '') = '' THEN '{f};' ELSE '' END" for f in req)
    conf = ", ".join(f"coalesce(e.extraction:response:{f}:confidence_score::DOUBLE, 0.0)" for f in req)
    selects.append(f"""
SELECT e.document_id, e.doc_type, e.extraction:response AS fields,
  CAST(least({conf}) AS DOUBLE) AS confidence,
  filter(split({missing}, ';'), x -> x <> '') AS missing_required,
  {field(t['policy_field'])} AS policy_ref,
  {field(t['date_field'], 'DATE')} AS ref_date,
  {field(t['amount_field'], 'DOUBLE')} AS amount,
  {field('iban') if 'iban' in t['fields'] else 'CAST(NULL AS STRING)'} AS iban,
  {sql_array(t['checks'])} AS applicable_checks
FROM doc_extracted e WHERE e.doc_type = '{doc_type}'""")
selects.append(f"""
SELECT document_id, doc_type, CAST(NULL AS VARIANT) AS fields, CAST(0.0 AS DOUBLE) AS confidence, CAST(array() AS ARRAY<STRING>) AS missing_required,
  CAST(NULL AS STRING) AS policy_ref, CAST(NULL AS DATE) AS ref_date, CAST(NULL AS DOUBLE) AS amount,
  CAST(NULL AS STRING) AS iban, array('known_document_type') AS applicable_checks
FROM doc_extracted WHERE doc_type = '{OTHER}'""")
spark.sql("CREATE OR REPLACE TABLE doc_fields AS " + "\nUNION ALL\n".join(selects))

# Step 4b: run the checks on the typed columns and keep the ones that apply to each document type.
spark.sql(f"""
CREATE OR REPLACE TEMP VIEW doc_checked AS
SELECT f.document_id, f.doc_type, f.fields, f.confidence, f.missing_required,
  map_filter(
    map('policy_found', coalesce(chk.policy_found, false),
        'policy_active_on_date', coalesce(chk.policy_active_on_date, false),
        'within_coverage', coalesce(chk.within_coverage, false),
        'iban_valid', coalesce({T}.is_valid_iban(f.iban), false),
        'known_document_type', f.doc_type <> '{OTHER}'),
    (k, v) -> array_contains(f.applicable_checks, k)) AS checks
FROM doc_fields f,
LATERAL {T}.check_claim_policy(f.policy_ref, f.ref_date, f.amount) AS chk
""")
spark.sql(f"""
CREATE OR REPLACE TABLE doc_pipeline_results AS
SELECT *,
  CASE
    WHEN size(missing_required) > 0 OR array_contains(map_values(checks), false) THEN 'human_review'
    WHEN confidence >= {cfg.auto_threshold} THEN 'auto'
    WHEN confidence >= {cfg.audit_threshold} THEN 'auto_with_audit'
    ELSE 'human_review'
  END AS route,
  current_timestamp() AS processed_at
FROM doc_checked
""")
display(spark.sql("SELECT document_id, doc_type, round(confidence, 2) AS confidence, missing_required, checks, route FROM doc_pipeline_results ORDER BY document_id"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## How good is it?
# MAGIC
# MAGIC Compared with the correct answers stored when the documents were generated. `auto_with_audit` counts as `auto`.

# COMMAND ----------

display(spark.sql("""
SELECT
  round(avg(CASE WHEN r.doc_type = d.doc_type THEN 1 ELSE 0 END), 3) AS doc_type_accuracy,
  round(avg(CASE WHEN replace(r.route, 'auto_with_audit', 'auto') = d.expected_route THEN 1 ELSE 0 END), 3) AS route_accuracy,
  count(*) AS documents
FROM doc_pipeline_results r JOIN documents d USING (document_id)
"""))
display(spark.sql("""
SELECT d.expected_route, r.route, count(*) AS n
FROM doc_pipeline_results r JOIN documents d USING (document_id)
GROUP BY ALL ORDER BY ALL
"""))
display(spark.sql("""
SELECT d.document_id, d.scenario, d.expected_route, r.route, r.missing_required, r.checks
FROM doc_pipeline_results r JOIN documents d USING (document_id)
WHERE replace(r.route, 'auto_with_audit', 'auto') <> d.expected_route
ORDER BY d.document_id
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Field accuracy for clean documents

# COMMAND ----------

rows = spark.sql("""
SELECT r.document_id, r.doc_type, r.fields, d.truth_fields
FROM doc_pipeline_results r JOIN documents d USING (document_id)
WHERE d.scenario = 'clean' AND r.doc_type = d.doc_type
""").collect()


def norm(v):
    if v is None:
        return None
    s = str(v).strip().lower().replace(",", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return s


stats = {}
for r in rows:
    fields = json.loads(r.fields.toJson()) if r.fields is not None else {}
    truth = json.loads(r.truth_fields)
    for name in DOC_TYPES[r.doc_type]["required"]:
        got = (fields.get(name) or {}).get("value")
        ok = norm(got) == norm(truth.get(name))
        hit, total = stats.get(name, (0, 0))
        stats[name] = (hit + ok, total + 1)
for name, (hit, total) in sorted(stats.items()):
    print(f"{name:28} {hit}/{total}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Tables the document intake agent writes
# MAGIC
# MAGIC The agent (`06`) records every decision in `agent_decisions`, and every case that needs a person in `review_cases`.
# MAGIC Both carry the MLflow trace id, so a reviewer can open the full trace of how the decision was made.

# COMMAND ----------

spark.sql("""
CREATE TABLE IF NOT EXISTS agent_decisions (
  document_id STRING, doc_type STRING, route STRING, confidence DOUBLE,
  reasons STRING COMMENT 'JSON array', fields STRING COMMENT 'JSON object of extracted values',
  checks STRING COMMENT 'JSON object of check results', trace_id STRING, decided_at TIMESTAMP)
COMMENT 'Every decision made by the document intake agent'
""")
spark.sql("""
CREATE TABLE IF NOT EXISTS review_cases (
  document_id STRING, doc_type STRING, route STRING, confidence DOUBLE, reasons STRING, fields STRING,
  trace_id STRING, status STRING COMMENT 'open, approved, corrected, rejected', reviewer STRING,
  review_note STRING, created_at TIMESTAMP, reviewed_at TIMESTAMP)
COMMENT 'Cases routed to human review or sampled for audit by the document intake agent'
""")
