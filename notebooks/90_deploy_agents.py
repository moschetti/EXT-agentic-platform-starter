# Databricks notebook source
# MAGIC %md
# MAGIC # 90 · Deploy the agents from a notebook
# MAGIC
# MAGIC Deploys both agents with the Agent Bricks CLI from serverless, with no terminal or laptop needed:
# MAGIC
# MAGIC 1. Copies each agent project to local disk and renders its `agent.toml` and `app.yaml` from `01_configuration`
# MAGIC 2. Runs `agentbricks deploy` for each, authenticated as you (the notebook's or job's credentials)
# MAGIC
# MAGIC Run `00`–`05` first, then this notebook, then `91_grant_agent_access`. The `agentic-starter-deploy` job runs
# MAGIC them in that order; `agentic-starter-agents` runs only this notebook and `91`. The `agents` widget (or job
# MAGIC parameter) selects `both`, `doc_intake` or `service_assist`.
# MAGIC
# MAGIC The Agent Bricks CLI calls the Databricks CLI for some steps. On serverless notebook compute the built-in
# MAGIC `databricks` command reports that it is only supported from the web terminal, so this notebook downloads a pinned
# MAGIC CLI release from GitHub for the deploy (`scripts/notebook_cli.py`). If the workspace cannot reach GitHub, deploy
# MAGIC from a laptop instead (`scripts/deploy.sh`).
# MAGIC
# MAGIC Each deploy uploads the source to the workspace, creates or updates the Databricks App, and installs the
# MAGIC dependencies in the app. The first deploy of each agent takes several minutes.

# COMMAND ----------

# MAGIC %pip install -q databricks-agentbricks uv

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./01_configuration $run_checks="false"

# COMMAND ----------

import os
import shutil
import sys
import tempfile
from pathlib import Path

from databricks.sdk import WorkspaceClient

REPO = Path(os.path.abspath(".."))
sys.path.insert(0, str(REPO / "scripts"))
from notebook_cli import cli_env, install_databricks_cli
from notebook_cli import run as run_cli
from render_agent_config import render

dbutils.widgets.dropdown("agents", "both", ["both", "doc_intake", "service_assist"], "Agents to deploy")
selected = ["doc_intake", "service_assist"] if dbutils.widgets.get("agents") == "both" else [dbutils.widgets.get("agents")]

w = WorkspaceClient()
env = cli_env(w.config.host, dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get())


def run(cmd, cwd=None):
    return run_cli(cmd, env, cwd=cwd).stdout


install_databricks_cli(env)
run(["databricks", "--version"])
run(["databricks", "current-user", "me", "-o", "json"])
run(["agentbricks", "--version"])
run(["uv", "--version"])
print("Deploying to", w.config.host, "as", w.current_user.me().user_name, "| server mode:", cfg.server_mode)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Render and deploy

# COMMAND ----------

work = Path(tempfile.mkdtemp(prefix="agents-"))
ignore = shutil.ignore_patterns(".venv", "__pycache__", ".agentbricks", "agent.toml", "app.yaml", ".env")
for folder in ("doc_intake", "service_assist"):
    shutil.copytree(REPO / "agents" / folder, work / folder, ignore=ignore)

deployed = []
for folder, name in render(vars(cfg), work):
    if folder not in selected:
        continue
    print(f"\n=== {folder} → agent-bricks-{name}")
    print((work / folder / "agent.toml").read_text())
    run(["agentbricks", "deploy", name], cwd=work / folder)
    deployed.append(name)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Result

# COMMAND ----------

for app_name in (cfg.doc_agent_app, cfg.assist_agent_app):
    try:
        app = w.apps.get(app_name)
        state = app.app_status.state if app.app_status else None
        print(f"{app_name}: {state}  {app.url}")
    except Exception as e:
        print(f"{app_name}: not found ({str(e)[:100]})")
print("\nNext: run 91_grant_agent_access, then attach the guardrail policies (05, section 4).")
