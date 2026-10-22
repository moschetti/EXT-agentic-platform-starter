# Databricks notebook source
# MAGIC %md
# MAGIC # 10 · Governance and audit
# MAGIC
# MAGIC Every part of the starter is a Unity Catalog object or writes to system tables, so the questions a risk or audit
# MAGIC function asks have answers in SQL:
# MAGIC
# MAGIC | Question | Where the answer is |
# MAGIC |---|---|
# MAGIC | Who can call which model, tool or data? | Grants on the schemas, functions and model services |
# MAGIC | What did each agent send to the model, and what came back? | Inference tables of the model services (`05`) |
# MAGIC | How much did each agent use, and when were guardrails triggered? | `system.ai_gateway.usage` |
# MAGIC | Who changed a service, a function or a grant? | `system.access.audit` |
# MAGIC | Where did the data in a decision come from? | `system.access.table_lineage` and the MLflow trace of the decision |
# MAGIC | Why did the agent decide this? | `agent_decisions` → `trace_id` → the trace, step by step |
# MAGIC | What does it cost? | `system.billing.usage` |
# MAGIC
# MAGIC Reading system tables needs `SELECT` on the `system` schemas, which an account or metastore admin grants. Some
# MAGIC tables are updated with a delay of up to a few hours, so run this notebook again later if a query returns nothing.

# COMMAND ----------

# MAGIC %run ./01_configuration $run_checks="false"

# COMMAND ----------

S = f"`{cfg.catalog}`.`{cfg.schema}`"
T = f"`{cfg.catalog}`.`{cfg.tools_schema}`"


def show(sql, note=None):
    try:
        display(spark.sql(sql))
    except Exception as e:
        print(f"Not available here ({note or 'check that the system table is enabled and you can read it'}): {str(e)[:200]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Access

# COMMAND ----------

show(f"SHOW GRANTS ON SCHEMA {T}")
show(f"SHOW GRANTS ON SCHEMA {S}")
show(f"DESCRIBE FUNCTION EXTENDED {T}.search_kb")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Unity Gateway usage and guardrail decisions
# MAGIC
# MAGIC The column set of `system.ai_gateway.usage` is shown first, then a summary per service and day.

# COMMAND ----------

show("DESCRIBE TABLE system.ai_gateway.usage")

# COMMAND ----------

try:
    cols = {r.col_name for r in spark.sql("DESCRIBE TABLE system.ai_gateway.usage").collect()}
    service_col = next((c for c in ("service_name", "model_service_name", "endpoint_name", "securable_full_name") if c in cols), None)
    time_col = next((c for c in ("event_time", "request_time", "timestamp") if c in cols), None)
    if service_col and time_col:
        show(f"""
            SELECT {service_col} AS service, date({time_col}) AS day, count(*) AS requests
            FROM system.ai_gateway.usage
            WHERE {service_col} ILIKE '%{cfg.name_prefix}_%' AND {time_col} >= current_date() - INTERVAL 14 DAYS
            GROUP BY ALL ORDER BY day DESC, service""")
    else:
        show(f"SELECT * FROM system.ai_gateway.usage LIMIT 20")
except Exception as e:
    print("system.ai_gateway.usage not available:", str(e)[:200])

# COMMAND ----------

# MAGIC %md
# MAGIC ## Inference logs

# COMMAND ----------

for t in spark.sql(f"SHOW TABLES IN {S} LIKE '{cfg.name_prefix}_*'").collect():
    print(t.tableName)
    show(f"SELECT * FROM {S}.`{t.tableName}` LIMIT 5")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Audit: changes to the starter's services, functions and apps

# COMMAND ----------

show(f"""
SELECT event_time, user_identity.email AS who, service_name, action_name, request_params
FROM system.access.audit
WHERE event_date >= current_date() - INTERVAL 7 DAYS
  AND (to_json(request_params) ILIKE '%{cfg.schema}%' OR to_json(request_params) ILIKE '%{cfg.name_prefix.replace('_', '-')}%')
ORDER BY event_time DESC
LIMIT 200
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Lineage of the agent's decisions

# COMMAND ----------

show(f"""
SELECT DISTINCT source_table_full_name, target_table_full_name, entity_type
FROM system.access.table_lineage
WHERE event_date >= current_date() - INTERVAL 14 DAYS
  AND (target_table_full_name LIKE '{cfg.catalog}.{cfg.schema}.%' OR source_table_full_name LIKE '{cfg.catalog}.{cfg.schema}.%')
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## From a decision to its explanation
# MAGIC
# MAGIC Every row in `agent_decisions` has the trace id of the run that made it. The trace holds the prompt, the model's
# MAGIC structured output, the evidence, each check and the route.

# COMMAND ----------

show(f"""
SELECT document_id, doc_type, route, confidence, reasons, trace_id, decided_at
FROM {S}.agent_decisions ORDER BY decided_at DESC LIMIT 20
""")

# COMMAND ----------

import mlflow

row = spark.sql(f"SELECT trace_id FROM {S}.agent_decisions WHERE trace_id IS NOT NULL ORDER BY decided_at DESC LIMIT 1").first()
if row:
    trace = mlflow.get_trace(row.trace_id)
    for span in trace.data.spans:
        print(f"{span.span_type or '':10} {span.name}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cost of the agents' apps and model usage
# MAGIC
# MAGIC Quantities are in `usage_unit` (DBUs for most SKUs). Multiply by the price in `system.billing.list_prices` for
# MAGIC cost. The workspace may share an account with others, so the query filters to this workspace.

# COMMAND ----------

# system.billing.usage covers every workspace in the account, so filter to this one.
from databricks.sdk import WorkspaceClient

workspace_id = WorkspaceClient().get_workspace_id()
print("workspace_id:", workspace_id)
show(f"""
SELECT usage_date, billing_origin_product, sku_name, usage_unit, round(sum(usage_quantity), 2) AS quantity
FROM system.billing.usage
WHERE workspace_id = '{workspace_id}'
  AND usage_date >= current_date() - INTERVAL 14 DAYS
  AND billing_origin_product IN ('APPS', 'MODEL_SERVING', 'AI_GATEWAY', 'VECTOR_SEARCH', 'AI_FUNCTIONS', 'SQL', 'INTERACTIVE', 'JOBS')
GROUP BY ALL ORDER BY usage_date DESC, quantity DESC
""")
