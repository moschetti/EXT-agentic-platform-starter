# Databricks notebook source
# MAGIC %md
# MAGIC # 92 · Remove the starter
# MAGIC
# MAGIC Deletes everything the starter created in the environment selected in `01_configuration`, so it can be deployed
# MAGIC again from scratch. Runs on serverless; no terminal needed.
# MAGIC
# MAGIC | Step | What is removed |
# MAGIC |---|---|
# MAGIC | 1 | Both agent apps, with `agentbricks deployments delete` (this also removes the Runtime Store Agent Bricks created in Lakebase; deleting the app in the UI would leave it) |
# MAGIC | 2 | The two Unity Gateway model services, with their guardrail policies |
# MAGIC | 3 | The AI Search index, and optionally the endpoint |
# MAGIC | 4 | Optionally, the MLflow experiments (traces, evaluation runs, review queues, monitoring scorers). Off by default: a deleted experiment's name cannot be set again until it is removed from the trash, so a fresh run under the same `name_prefix` would fail. To start with empty experiments, change `name_prefix` in `01` instead |
# MAGIC | 5 | The two schemas: tables, volume and PDFs, functions, guardrail functions, inference tables, evaluation datasets |
# MAGIC
# MAGIC Not removed: the catalog, the workspace previews, and the bundle (if you deployed it, use the Git folder's
# MAGIC **Deployments** panel → **Destroy**, or `databricks bundle destroy`).
# MAGIC
# MAGIC Every step skips what is already gone, so the notebook can be re-run.

# COMMAND ----------

# MAGIC %pip install -q databricks-agentbricks

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./01_configuration $run_checks="false"

# COMMAND ----------

import os
import sys
from pathlib import Path

import mlflow
from databricks.sdk import WorkspaceClient

sys.path.insert(0, str(Path(os.path.abspath("..")) / "scripts"))
from notebook_cli import cli_env, install_databricks_cli, run

# Confirm here, in the 'confirm' widget (it appears below the notebook toolbar once this cell has run), or as a
# job parameter.
CONFIRM = ""  # e.g. "my_catalog.agentic_starter"

dbutils.widgets.text("confirm", "", "Type the schema (catalog.schema) to confirm")
dbutils.widgets.dropdown("delete_ai_search_endpoint", "no", ["no", "yes"], "Also delete the AI Search endpoint")
dbutils.widgets.dropdown("delete_experiments", "no", ["no", "yes"], "Also delete the MLflow experiments")

if (dbutils.widgets.get("confirm").strip() or CONFIRM.strip()) != cfg.full_schema:
    raise ValueError(f"To remove the starter, set CONFIRM in this cell (or the 'confirm' widget) to "
                     f"{cfg.full_schema} and run again.")

w = WorkspaceClient()
print(f"Removing the starter from {w.config.host}: {cfg.full_schema} and {cfg.full_tools_schema}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · Agent apps

# COMMAND ----------

env = cli_env(w.config.host, dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get())
install_databricks_cli(env)

for app_name in (cfg.doc_agent_app, cfg.assist_agent_app):
    try:
        w.apps.get(app_name)
    except Exception:
        print(f"{app_name}: not found, skipping")
        continue
    p = run(["agentbricks", "deployments", "delete", app_name, "--yes"], env, check=False)
    if p.returncode != 0:
        print(f"{app_name}: agentbricks delete failed; deleting the app directly (a Lakebase runtime store may remain)")
        w.apps.delete(app_name)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Model services

# COMMAND ----------

parent = f"schemas/{cfg.catalog}.{cfg.schema}"
try:
    services = list(w.ai_gateway.list_model_services(parent=parent))
except Exception as e:
    services = []
    print("Could not list model services (schema may be gone already):", str(e)[:150])
for s in services:
    if (s.name or "").split(".")[-1].startswith(f"{cfg.name_prefix}_"):
        w.ai_gateway.delete_model_service(name=s.name)
        print("deleted", s.name)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · AI Search

# COMMAND ----------

try:
    w.vector_search_indexes.delete_index(cfg.kb_index)
    print("deleted index", cfg.kb_index)
except Exception as e:
    print("index:", str(e)[:150])

if dbutils.widgets.get("delete_ai_search_endpoint") == "yes":
    try:
        w.vector_search_endpoints.delete_endpoint(cfg.ai_search_endpoint)
        print("deleted endpoint", cfg.ai_search_endpoint)
    except Exception as e:
        print("endpoint:", str(e)[:150])
else:
    print(f"Keeping endpoint {cfg.ai_search_endpoint}. It costs while it exists; delete it if nothing else uses it.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · MLflow experiments

# COMMAND ----------

if dbutils.widgets.get("delete_experiments") == "yes":
    mlflow.set_tracking_uri("databricks")
    for name in (cfg.doc_experiment, cfg.assist_experiment):
        exp = mlflow.get_experiment_by_name(name)
        if exp and exp.lifecycle_stage == "active":
            mlflow.delete_experiment(exp.experiment_id)
            print("deleted experiment", name)
        else:
            print(f"{name}: not found, skipping")
    print("A fresh run under the same name_prefix needs these removed from the trash first, or a new name_prefix.")
else:
    print("Keeping the experiments. A fresh run adds new runs and traces to them; change name_prefix for new ones.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Schemas

# COMMAND ----------

for schema in (cfg.full_tools_schema, cfg.full_schema):
    catalog, name = schema.split(".")
    spark.sql(f"DROP SCHEMA IF EXISTS `{catalog}`.`{name}` CASCADE")
    print("dropped", schema)

print("\nDone. To deploy again: 01_configuration, then 02–05, attach the guardrail policies, 91, 90.")
