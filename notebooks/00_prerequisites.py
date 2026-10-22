# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Prerequisites
# MAGIC
# MAGIC This notebook lists everything that must be in place before you deploy the starter, and who usually does it.
# MAGIC The cells at the end run read-only checks against the current workspace. They need no configuration and
# MAGIC create nothing. The `agentic-starter-deploy` job runs this notebook as its first task.
# MAGIC
# MAGIC Next step: set your environment details in `01_configuration`.
# MAGIC
# MAGIC ## What the starter deploys
# MAGIC
# MAGIC | Layer | What you get | Notebook |
# MAGIC |---|---|---|
# MAGIC | Data | Synthetic customers, policies, claims, knowledge base articles, 40 PDFs with known correct answers, service cases, guardrail tests | `02` |
# MAGIC | Tools | Unity Catalog functions, AI Search index, and the same functions through the Unity Catalog functions MCP server | `03` |
# MAGIC | Document processing (SQL) | `ai_parse_document` → `ai_classify` → `ai_extract` with confidence scores → checks → routing | `04` |
# MAGIC | Unity Gateway | One model service per agent: routing, rate limits, inference logging, guardrails (service policies) | `05` |
# MAGIC | Agent 1: document intake | LangGraph: classify → extract with evidence → validate with tools → confidence → route | `06` |
# MAGIC | Agent 2: service assist | OpenAI Agents SDK: customer and policy lookups + knowledge search through MCP → suggested reply, behind guardrails | `07` |
# MAGIC | Quality | MLflow 3 evaluation datasets and scorers; production monitoring and review queues | `08`, `09` |
# MAGIC | Governance | Grants, inference logs, gateway usage, audit, lineage, decision → trace, cost | `10` |
# MAGIC | Deployment | Both agents on Agent Bricks: Agent Runtime (Databricks Apps), deployed with the Agent Bricks CLI, with least-privilege grants | `agentic-starter-deploy` job, or `90` and `91` |

# COMMAND ----------

# MAGIC %md
# MAGIC ## Checklist
# MAGIC
# MAGIC Release status is noted where a feature is not generally available (GA). `docs/feature-status.md` lists the status of
# MAGIC each feature with a link to the Databricks documentation it was taken from.
# MAGIC
# MAGIC ### Account admin
# MAGIC
# MAGIC | # | Item | Needed for | How to check |
# MAGIC |---|---|---|---|
# MAGIC | A1 | Workspace is attached to a Unity Catalog metastore | Everything | Check C0 below, or Catalog Explorer |
# MAGIC | A2 | Workspace is in a Unity Gateway supported region | `05` onward | Check C3 below, or the region list in the Unity Gateway docs |
# MAGIC | A4 | Lakebase available in the workspace region | Only when `enable_lakebase = True` in `01` (durable run state for the agents) | Region list in the Lakebase documentation |
# MAGIC
# MAGIC ### Workspace admin
# MAGIC
# MAGIC | # | Item | Needed for | How to check |
# MAGIC |---|---|---|---|
# MAGIC | W1 | Serverless compute for notebooks and jobs | All notebooks | This notebook runs on serverless |
# MAGIC | W2 | Databricks Apps enabled | Both agents (Agent Runtime runs on Databricks Apps) | Check C2 below |
# MAGIC | W3 | Foundation Model APIs (pay-per-token) available, including the chat and embedding models chosen in `01` | AI Functions, both agents | Check C1 below |
# MAGIC | W4 | A serverless SQL warehouse the deployer can use | AI Functions, UC function tools | Check C4 below |
# MAGIC | W5 | If the workspace keeps processing within its geography (cross-geo processing off), the models chosen in `01` are served in the region | AI Functions, both agents | Check C1 below; `01` makes a test call to each model |
# MAGIC | W6 | **MLflow Review Queues** preview enabled (Beta), on the workspace **Previews** page | `09` | `09` fails when it creates a queue if it is off |
# MAGIC | W7 | System tables enabled (`system.access`, `system.billing`, `system.ai_gateway`) and readable by the deployer | `10` | `10` prints an error per query it cannot run |
# MAGIC | W8 | **Managed MCP Servers** preview enabled, on the workspace **Previews** page | `03`, agent 2 (all its tools come through MCP) | `03` stops with "managed MCP servers are not enabled" if it is off |
# MAGIC | W9 | **Unity Ai Gateway Service Policies** preview enabled (Beta), on the workspace **Previews** page: the guardrails | `05`, agent 2 | The **Policies** tab of a model service is missing if it is off |
# MAGIC
# MAGIC The workspace **Previews** page is under your user name (top right) → **Previews**.
# MAGIC
# MAGIC ### Deployer (the person or service principal running the starter)
# MAGIC
# MAGIC | # | Item | Needed for |
# MAGIC |---|---|---|
# MAGIC | D1 | `CREATE CATALOG` on the metastore, **or** an existing catalog with `USE CATALOG` + `CREATE SCHEMA` | `02` |
# MAGIC | D2 | On the starter schemas: `CREATE TABLE`, `CREATE VOLUME`, `CREATE FUNCTION`, `CREATE SERVICE`, `MANAGE` (you get these as schema owner) | `02`–`05`, `91` |
# MAGIC | D3 | `CAN_USE` on the SQL warehouse (and `CAN_MANAGE` to grant it to the agents) | `03`, `04`, `91` |
# MAGIC | D4 | Permission to create Databricks Apps and MLflow experiments | `90` (apps); `06`, `07` and `90` (experiments) |
# MAGIC | D5 | Permission to create a Databricks AI Search (formerly Vector Search) endpoint, or `CAN_USE` on an existing one | `03` |
# MAGIC
# MAGIC ### Local machine (only to deploy the bundle from a terminal)
# MAGIC
# MAGIC Not needed if you deploy the bundle from the Git folder's **Deployments** panel or run the notebooks yourself.
# MAGIC The agents are deployed by notebook `90` on serverless, which installs what it needs.
# MAGIC
# MAGIC | # | Item | How to check |
# MAGIC |---|---|---|
# MAGIC | L1 | Databricks CLI, authenticated to the workspace | `databricks --version` and `databricks auth profiles` |
# MAGIC | L2 | Only for local agent development (`agentbricks dev`): Python 3.11+, `uv`, Agent Bricks CLI | `uv --version`, `agentbricks --help` |
# MAGIC 
# COMMAND ----------

# MAGIC %md
# MAGIC ## Read-only checks
# MAGIC
# MAGIC Each check prints `PASS`, `WARN` or `FAIL`. `WARN` means the starter can still run, but a feature may be missing.

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
results = []


def record(check_id, item, status, detail=""):
    results.append((check_id, item, status, detail))
    print(f"[{status}] {check_id} {item}" + (f": {detail}" if detail else ""))

# COMMAND ----------

# C0: Unity Catalog metastore
try:
    m = w.metastores.current()
    record("C0", "Unity Catalog metastore", "PASS", f"metastore_id={m.metastore_id}")
except Exception as e:
    record("C0", "Unity Catalog metastore", "FAIL", str(e)[:200])

# COMMAND ----------

# C1: Foundation Model API endpoints (pay-per-token endpoints are named databricks-*)
try:
    fm = sorted(e.name for e in w.serving_endpoints.list() if e.name.startswith("databricks-"))
    record("C1", "Foundation Model API endpoints", "PASS" if fm else "FAIL", f"{len(fm)} found")
    for name in fm:
        print("   ", name)
except Exception as e:
    record("C1", "Foundation Model API endpoints", "FAIL", str(e)[:200])

# COMMAND ----------

# C2: Databricks Apps
try:
    apps = list(w.apps.list())
    record("C2", "Databricks Apps API", "PASS", f"{len(apps)} apps visible")
except Exception as e:
    record("C2", "Databricks Apps API", "FAIL", str(e)[:200])

# COMMAND ----------

# C3: Unity Gateway model services API (GA; not available in every region)
try:
    w.api_client.do("GET", "/api/2.1/unity-catalog/model-services")
    record("C3", "Unity Gateway model services API", "PASS")
except Exception as e:
    record("C3", "Unity Gateway model services API", "FAIL", str(e)[:200])

# COMMAND ----------

# C4: SQL warehouses the current user can see
try:
    whs = list(w.warehouses.list())
    serverless = [x for x in whs if x.enable_serverless_compute]
    status = "PASS" if serverless else ("WARN" if whs else "FAIL")
    record("C4", "SQL warehouse", status, f"{len(serverless)} serverless of {len(whs)} visible")
    for x in whs:
        print(f"    {x.id}  {x.name}  serverless={x.enable_serverless_compute}  state={x.state}")
except Exception as e:
    record("C4", "SQL warehouse", "FAIL", str(e)[:200])

# COMMAND ----------

me = w.current_user.me()
print(f"Workspace: {w.config.host}")
print(f"Running as: {me.user_name}\n")
for check_id, item, status, detail in results:
    print(f"{status:5} {check_id}  {item}")
print("\nManual items: A4, W5–W9, D1–D5 (D1, D3 and the models are checked in 01_configuration).")
