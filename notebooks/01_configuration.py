# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Configuration
# MAGIC
# MAGIC **This is the only place you set environment details.** Every other notebook loads it with
# MAGIC `%run ./01_configuration $run_checks="false"` and reads the values from `cfg`.
# MAGIC
# MAGIC 1. Fill in your environment in `ENVIRONMENTS` below: at least `catalog`. The two entries are examples; edit one
# MAGIC    or add your own.
# MAGIC 2. Select it with the `environment` widget. Widgets appear below the notebook toolbar after the widget cell has run.
# MAGIC    In the bundle jobs, the target (`demo` or `demo-legacy` in `databricks.yml`) sets `environment` instead.
# MAGIC 3. Run all. The checks at the end confirm the workspace, catalog, models and warehouse are usable. They fail the
# MAGIC    run, and the deployment job, until every check passes.
# MAGIC
# MAGIC Nothing in this notebook creates or changes anything.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Environments
# MAGIC
# MAGIC `SHARED` holds defaults for every environment. An environment entry overrides any of them.
# MAGIC
# MAGIC | Key | Meaning |
# MAGIC |---|---|
# MAGIC | `workspace_host` | Workspace URL. Used to confirm you are running in the workspace you meant to configure. Leave empty to skip that check |
# MAGIC | `region` | Cloud region of the workspace. Informational; feature availability differs by region |
# MAGIC | `catalog` | Unity Catalog catalog for all starter assets |
# MAGIC | `create_catalog` | `True` if `02` should create the catalog (needs `CREATE CATALOG`). `False` to use an existing one |
# MAGIC | `schema` | Schema for data, model services and guardrail functions. Agent tools go in `<schema>_tools`. Dropping both removes the starter |
# MAGIC | `org_name` | Fictional organisation name used in synthetic data and prompts |
# MAGIC | `chat_model` | Foundation model the Unity Gateway model services route to: a `system.ai` model name, or `"auto"` to use the first model in `PREFERRED_CHAT_MODELS` that this workspace serves. Pin a name for repeatable results |
# MAGIC | `fallback_model` | Second `system.ai` model the services fall back to if the first is unavailable. Empty for no fallback |
# MAGIC | `embedding_model` | Foundation Model API embedding endpoint for the knowledge base index |
# MAGIC | `warehouse_id` | Serverless SQL warehouse. Leave empty to use the first serverless warehouse you can see |
# MAGIC | `ai_search_endpoint` | Databricks AI Search (formerly Vector Search) endpoint name. Created in `03` if it does not exist |
# MAGIC | `name_prefix` | Prefix for apps, services and experiments, so several people can deploy to one workspace. Lower case letters, digits and `_`; at most 10 characters (app names are capped at 30) |
# MAGIC | `enable_lakebase` | `True` deploys the agents with `DurableAgentServer`, which keeps run state in a Lakebase database that `agentbricks deploy` provisions. `False` deploys them as a plain FastAPI app with the same agent code, for regions where Lakebase is not available |
# MAGIC | `auto_threshold`, `audit_threshold`, `audit_sample_rate` | Document intake routing: confidence at or above `auto_threshold` is processed automatically; between the two thresholds it is processed and a share (`audit_sample_rate`) is sampled for audit; below `audit_threshold` it goes to human review |
# MAGIC
# MAGIC ### Choosing `chat_model`: check which models your workspace serves
# MAGIC
# MAGIC A model listed in `system.ai` (Catalog Explorer) is not necessarily served in your region, and a model that is
# MAGIC not served fails only when it is called (`404 ENDPOINT_NOT_FOUND`). Before deploying:
# MAGIC
# MAGIC 1. Run this notebook with `run_checks` = `true`. `Test call to chat_model` must pass. If it fails, the next line
# MAGIC    lists the models in `PREFERRED_CHAT_MODELS` that answer here; set `chat_model` to one of them and run again.
# MAGIC    The checks fail the run (and the deployment job) until the model answers.
# MAGIC 2. To see every model, open **Serving** in the left menu and filter on foundation models: an endpoint
# MAGIC    `databricks-X` that is **Ready** can be used as `chat_model = "system.ai.databricks-X"`. Try one in the
# MAGIC    **Playground** to confirm it answers.
# MAGIC
# MAGIC Served models differ by region and change over time; a region can serve the Claude Sonnet models and no GPT-5
# MAGIC model, for example. The `demo` example pins Sonnet 5.5. Set `chat_model` per environment when they differ.

# COMMAND ----------

SHARED = {
    "schema": "agentic_starter",
    "org_name": "Aurora Mutual",
    # "auto", or a system.ai model such as "system.ai.databricks-gpt-5-5". Foundation model names carry
    # the databricks- prefix, the same as their Foundation Model API endpoint (endpoint databricks-X = model
    # system.ai.databricks-X). A model listed in system.ai is not necessarily served in every region; the checks
    # below make a test call and list the models that answer.
    "chat_model": "system.ai.databricks-gpt-5-5",
    "fallback_model": "",
    "embedding_model": "databricks-gte-large-en",
    "warehouse_id": "",
    "ai_search_endpoint": "agentic-starter",
    "name_prefix": "starter",
    "create_catalog": False,
    "enable_lakebase": False,
    "auto_threshold": 0.90,
    "audit_threshold": 0.75,
    "audit_sample_rate": 0.10,
}

# Used by chat_model = "auto", and listed by the checks when the configured model does not answer: in order of
# preference. The first one this workspace serves wins.
PREFERRED_CHAT_MODELS = [
    "databricks-gpt-5-5", "databricks-gpt-5-4", "databricks-gpt-5-2", "databricks-gpt-5-1", "databricks-gpt-5",
    "databricks-claude-sonnet-5-5", "databricks-claude-sonnet-5", "databricks-claude-sonnet-4-6",
    "databricks-claude-sonnet-4-5",
]

ENVIRONMENTS = {
    # Example: an Azure westus2 workspace with Lakebase (durable runtime). Fill in catalog; workspace_host is optional.
    "demo": {
        "cloud": "azure",
        "region": "westus2",
        "workspace_host": "",
        "catalog": "",
        # Pinned to a Claude model; check which models your workspace serves (see Choosing chat_model above).
        "chat_model": "system.ai.databricks-claude-sonnet-5-5",
        "enable_lakebase": True,
    },
    # Example: a second workspace, with Lakebase off (FastAPI runtime mode). Set enable_lakebase to True if
    # Lakebase is available in its region.
    "demo-legacy": {
        "cloud": "azure",
        "region": "swedencentral",
        "workspace_host": "",
        "catalog": "",
        "enable_lakebase": False,
    },
}

# COMMAND ----------

dbutils.widgets.dropdown("environment", "demo", list(ENVIRONMENTS), "Environment")
dbutils.widgets.dropdown("run_checks", "true", ["true", "false"], "Run checks")

# COMMAND ----------

import json
from types import SimpleNamespace

_env_name = dbutils.widgets.get("environment")
_values = {**SHARED, **ENVIRONMENTS[_env_name], "environment": _env_name}

_missing = [k for k in ("catalog",) if not _values.get(k)]
if _missing:
    raise ValueError(f"Set {_missing} for environment '{_env_name}' in ENVIRONMENTS above.")

_c, _s, _p = _values["catalog"], _values["schema"], _values["name_prefix"]
_dash = _p.replace("_", "-")

# Names derived from the values above. Change the values, not these.
_values.update(
    {
        "full_schema": f"{_c}.{_s}",
        "tools_schema": f"{_s}_tools",
        "full_tools_schema": f"{_c}.{_s}_tools",
        "volume_raw": f"/Volumes/{_c}/{_s}/raw",
        "documents_dir": f"/Volumes/{_c}/{_s}/raw/documents",
        # Unity Gateway model services: one per agent, so each has its own limits, logs and guardrails.
        "doc_service": f"{_c}.{_s}.{_p}_doc_intake_llm",
        "assist_service": f"{_c}.{_s}.{_p}_service_assist_llm",
        "kb_index": f"{_c}.{_s}.kb_articles_index",
        # Agent Bricks deploys an agent named X as the Databricks App "agent-bricks-X" (at most 30 characters).
        "doc_agent_name": f"{_dash}-intake",
        "assist_agent_name": f"{_dash}-assist",
        "doc_agent_app": f"agent-bricks-{_dash}-intake",
        "assist_agent_app": f"agent-bricks-{_dash}-assist",
        "doc_experiment": f"/Shared/{_dash}-agentic-starter/doc-intake",
        "assist_experiment": f"/Shared/{_dash}-agentic-starter/service-assist",
        "server_mode": "agentbricks" if _values["enable_lakebase"] else "custom",
    }
)

from databricks.sdk import WorkspaceClient

_too_long = [n for n in (_values["doc_agent_app"], _values["assist_agent_app"]) if len(n) > 30]
if _too_long:
    raise ValueError(f"App names are limited to 30 characters: {_too_long}. Shorten name_prefix (at most 10 characters).")

if not _values["warehouse_id"]:
    _serverless = [x for x in WorkspaceClient().warehouses.list() if x.enable_serverless_compute]
    _values["warehouse_id"] = _serverless[0].id if _serverless else ""


def served_chat_endpoints():
    """Foundation Model API endpoints (databricks-*) that are ready in this workspace."""
    return [e.name for e in WorkspaceClient().serving_endpoints.list()
            if e.name.startswith("databricks-") and e.state and getattr(e.state.ready, "value", None) == "READY"]


if _values["chat_model"] == "auto":
    _served = served_chat_endpoints()
    _pick = next((m for m in PREFERRED_CHAT_MODELS if m in _served), None) \
        or next((m for m in sorted(_served, reverse=True) if "sonnet" in m), None)
    if not _pick:
        raise ValueError(f"No preferred chat model is served here. Set chat_model explicitly; served endpoints: {sorted(_served)}")
    _values["chat_model"] = f"system.ai.{_pick}"

cfg = SimpleNamespace(**_values)
print(json.dumps(_values, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Checks
# MAGIC
# MAGIC Read-only. Skipped when another notebook loads this one with `run_checks="false"`.

# COMMAND ----------

if dbutils.widgets.get("run_checks") == "true":
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    me = w.current_user.me().user_name
    problems = []

    def check(item, ok, detail=""):
        print(f"[{'PASS' if ok else 'FAIL'}] {item}" + (f": {detail}" if detail else ""))
        if not ok:
            problems.append(item)

    def warn(item, detail=""):
        print(f"[WARN] {item}" + (f": {detail}" if detail else ""))

    # Workspace
    if cfg.workspace_host:
        check("Workspace", w.config.host.rstrip("/") == cfg.workspace_host.rstrip("/"),
              f"running in {w.config.host}, configured {cfg.workspace_host}")
    else:
        print(f"[SKIP] Workspace: workspace_host not set (running in {w.config.host})")

    # Catalog (D1)
    catalog_owner = None
    try:
        catalog_owner = w.catalogs.get(cfg.catalog).owner
        check("Catalog exists", True, f"{cfg.catalog} (owner: {catalog_owner})")
        if cfg.create_catalog:
            print("       create_catalog=True, but the catalog already exists; 02 will reuse it.")
    except Exception:
        check("Catalog exists", cfg.create_catalog, f"{cfg.catalog} not found"
              + ("; 02 will create it" if cfg.create_catalog else "; set create_catalog=True or use an existing catalog"))

    # Ownership is not a grant, so an owner has no privilege assignments to find. Privileges that come from
    # a group the owner role is given to, or from metastore admin, may not show either; those are a warning.
    if catalog_owner == me:
        check("Catalog privileges", True, "you own the catalog")
    elif catalog_owner:
        try:
            eff = w.grants.get_effective("catalog", cfg.catalog, principal=me)
            privs = {p.privilege.value for a in (eff.privilege_assignments or []) for p in (a.privileges or [])}
            if "ALL_PRIVILEGES" in privs or {"USE_CATALOG", "CREATE_SCHEMA"} <= privs:
                check("Catalog privileges (USE CATALOG + CREATE SCHEMA)", True, ", ".join(sorted(privs)))
            else:
                warn("Catalog privileges (USE CATALOG + CREATE SCHEMA)",
                     f"no direct grant found for {me} (found: {', '.join(sorted(privs)) or 'none'}). Fine if you get them "
                     f"through a group or own the catalog through a group ({catalog_owner}); otherwise ask the owner: "
                     f"GRANT USE CATALOG, CREATE SCHEMA ON CATALOG `{cfg.catalog}` TO `{me}`")
        except Exception as e:
            warn("Catalog privileges", str(e)[:150])

    # Models (W3): the Unity Gateway destinations are system.ai models; the embedding model is an endpoint.
    try:
        system_ai = {m.full_name for m in w.registered_models.list(catalog_name="system", schema_name="ai")}
        for key in ("chat_model", "fallback_model"):
            name = getattr(cfg, key)
            if name:
                check(f"system.ai model {key}", name in system_ai, name)
    except Exception as e:
        print(f"[SKIP] system.ai models: {str(e)[:150]}")

    # Listed is not the same as served: make a one-line test call to each configured model.
    from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

    def answers(model):
        try:
            # No max_tokens: some models (GPT-5 family) reject it.
            w.serving_endpoints.query(name=model.removeprefix("system.ai."),
                                      messages=[ChatMessage(role=ChatMessageRole.USER, content="Say OK")])
            return True, ""
        except Exception as e:
            return False, str(e)[:160]

    for key in ("chat_model", "fallback_model"):
        name = getattr(cfg, key)
        if name:
            ok, err = answers(name)
            check(f"Test call to {key}", ok, name if ok else f"{name}: {err}")
            if not ok:
                working = [m for m in PREFERRED_CHAT_MODELS if m in served_chat_endpoints() and answers(m)[0]]
                print(f"       Preferred models that answer here: {working or 'none'}. Set {key} to one of them.")

    endpoints = {e.name for e in w.serving_endpoints.list()}
    check("Embedding endpoint", cfg.embedding_model in endpoints, cfg.embedding_model)

    # Warehouse (W4, D3)
    match = [x for x in w.warehouses.list() if x.id == cfg.warehouse_id]
    check("SQL warehouse", bool(match), f"{match[0].id} {match[0].name}" if match else "none found")

    print(f"\nlakebase: {'on' if cfg.enable_lakebase else 'off'}")
    # Fail here, so a deployment job stops at this task instead of running on with a model that doesn't answer.
    if problems:
        raise RuntimeError(f"{len(problems)} check(s) failed: {problems}. See the [FAIL] lines above.")
    print("All checks passed.")
