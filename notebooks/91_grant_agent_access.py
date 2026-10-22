# Databricks notebook source
# MAGIC %md
# MAGIC # 91 · Grant the deployed agents access
# MAGIC
# MAGIC Each deployed agent runs as its Databricks App's service principal. That principal starts with no data access.
# MAGIC This notebook grants each agent only what it uses (least privilege). The `agentic-starter-deploy` and `agentic-starter-agents` jobs run it after deploying
# MAGIC the agents; run it again if you redeploy under new names.
# MAGIC
# MAGIC | Grant | Document intake | Service assist |
# MAGIC |---|---|---|
# MAGIC | `USE CATALOG`, `USE SCHEMA` (both schemas) | ✓ | ✓ |
# MAGIC | `EXECUTE` on the tool functions | `check_claim_policy`, `is_valid_iban` | `get_customer`, `get_policies`, `get_recent_claims`, `search_kb` |
# MAGIC | `SELECT` on tables | `doc_parsed`, `policies` | `customers`, `policies`, `claims`, `kb_articles`, the AI Search index |
# MAGIC | `MODIFY` on tables | `agent_decisions`, `review_cases` | none |
# MAGIC | `CAN_USE` on the SQL warehouse | ✓ (runs its checks as SQL) | ✓ (the MCP server runs the functions on serverless SQL) |
# MAGIC | `EXECUTE` on its model service | granted by `agentbricks deploy` from the model service binding in `agent.toml` | same |

# COMMAND ----------

# MAGIC %run ./01_configuration $run_checks="false"

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import WarehouseAccessControlRequest, WarehousePermissionLevel

w = WorkspaceClient()
C, S, T = f"`{cfg.catalog}`", f"`{cfg.catalog}`.`{cfg.schema}`", f"`{cfg.catalog}`.`{cfg.tools_schema}`"

AGENTS = {
    cfg.doc_agent_app: {
        "execute": ["check_claim_policy", "is_valid_iban"],
        "select": ["doc_parsed", "policies"],
        "modify": ["agent_decisions", "review_cases"],
    },
    cfg.assist_agent_app: {
        "execute": ["get_customer", "get_policies", "get_recent_claims", "search_kb"],
        "select": ["customers", "policies", "claims", "kb_articles"],
        "modify": [],
        "index": True,
    },
}


def grant(sql):
    try:
        spark.sql(sql)
        print("ok  ", sql)
    except Exception as e:
        print("FAIL", sql, "->", str(e)[:200])


for app_name, needs in AGENTS.items():
    try:
        app = w.apps.get(app_name)
    except Exception as e:
        print(f"\n{app_name}: not deployed ({str(e)[:100]}); skipping")
        continue
    sp = app.service_principal_client_id
    print(f"\n{app_name} runs as service principal {sp}")
    p = f"`{sp}`"
    grant(f"GRANT USE CATALOG ON CATALOG {C} TO {p}")
    grant(f"GRANT USE SCHEMA ON SCHEMA {S} TO {p}")
    grant(f"GRANT USE SCHEMA ON SCHEMA {T} TO {p}")
    for f in needs["execute"]:
        grant(f"GRANT EXECUTE ON FUNCTION {T}.{f} TO {p}")
    for t in needs["select"]:
        grant(f"GRANT SELECT ON TABLE {S}.{t} TO {p}")
    for t in needs["modify"]:
        grant(f"GRANT SELECT, MODIFY ON TABLE {S}.{t} TO {p}")
    if needs.get("index"):
        grant(f"GRANT SELECT ON TABLE {cfg.kb_index} TO {p}")
    try:
        w.warehouses.update_permissions(cfg.warehouse_id, access_control_list=[
            WarehouseAccessControlRequest(service_principal_name=sp, permission_level=WarehousePermissionLevel.CAN_USE)])
        print("ok   CAN_USE on warehouse", cfg.warehouse_id)
    except Exception as e:
        print("FAIL CAN_USE on warehouse:", str(e)[:200])
