# Databricks notebook source
# MAGIC %md
# MAGIC # 08 · Evaluation with MLflow 3
# MAGIC
# MAGIC "Is it good enough?" needs a number, measured the same way before and after every change. This notebook
# MAGIC builds an evaluation dataset for each agent in Unity Catalog and scores the agents against it.
# MAGIC
# MAGIC | Agent | Dataset | Scorers |
# MAGIC |---|---|---|
# MAGIC | Document intake | `eval_doc_intake` from `documents` (correct type, fields and route for each PDF) | Code scorers: route correct, type correct, field accuracy. Deterministic, no LLM |
# MAGIC | Service assist | `eval_service_assist` from `service_cases` (facts a good reply must contain) | LLM judges: correctness against expected facts, relevance, safety, and two guidelines from the rep rules |
# MAGIC
# MAGIC Each evaluation is an MLflow run with one trace per row, so a failing row can be opened and debugged.
# MAGIC Re-run after any change to prompts, models, tools or thresholds and compare runs in the experiment.

# COMMAND ----------

# MAGIC %pip install -q "mlflow[databricks]>=3.17.1" "langgraph>=1.1.0" "databricks-langchain==0.21.0" "openai-agents>=0.6" "databricks-openai>=0.10" "databricks-sdk>=0.124.0"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./01_configuration $run_checks="false"

# COMMAND ----------

import asyncio
import json
import logging
import os
import sys
import threading

import mlflow
from mlflow.entities import Feedback
from mlflow.genai.scorers import Correctness, Guidelines, RelevanceToQuery, Safety, scorer

sys.path.insert(0, os.path.abspath("../agents/doc_intake"))
sys.path.insert(0, os.path.abspath("../agents/service_assist"))
from assist.agent import ServiceAssist
from assist.guardrails import REPLY_GUIDELINES
from assist.settings import Settings as AssistSettings
from intake.doc_types import DOC_TYPES
from intake.graph import DocumentIntake
from intake.settings import Settings as IntakeSettings
# A harmless warning on serverless: MLflow cannot read some notebook context it uses for run tags.
logging.getLogger("mlflow.tracking.context.registry").setLevel(logging.ERROR)


def run_sync(coro):
    """Run a coroutine to completion from sync code, also when an event loop is already running.

    Runs on its own thread; an exception inside the coroutine is re-raised here, not lost in the thread.
    """
    box = {}

    def target():
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as e:
            box["error"] = e

    t = threading.Thread(target=target)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


def uc_dataset(name, records):
    """Create or refresh a Unity Catalog evaluation dataset."""
    from mlflow.genai import datasets

    full = f"{cfg.full_schema}.{name}"
    try:
        ds = datasets.get_dataset(name=full)
    except Exception:
        ds = datasets.create_dataset(name=full)
    ds.merge_records(records)
    return ds

# COMMAND ----------

# MAGIC %md
# MAGIC ## Document intake

# COMMAND ----------

docs = spark.table(f"{cfg.full_schema}.documents").orderBy("document_id").collect()
doc_records = [{
    "inputs": {"document_id": d.document_id},
    "expectations": {"doc_type": d.doc_type, "route": d.expected_route, "fields": json.loads(d.truth_fields)},
} for d in docs]
doc_ds = uc_dataset("eval_doc_intake", doc_records)

intake = DocumentIntake(IntakeSettings.from_cfg(cfg, write_results=False))


def predict_doc(document_id):
    return intake.run(document_id=document_id)


def _norm(v):
    if v is None:
        return None
    s = str(v).strip().lower().replace(",", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return s


@scorer
def route_correct(outputs, expectations):
    got = (outputs.get("route") or "").replace("auto_with_audit", "auto")
    return Feedback(value=got == expectations["route"], rationale=f"expected {expectations['route']}, got {outputs.get('route')}")


@scorer
def doc_type_correct(outputs, expectations):
    return outputs.get("doc_type") == expectations["doc_type"]


@scorer
def field_accuracy(outputs, expectations):
    t = DOC_TYPES.get(expectations["doc_type"])
    if not t or outputs.get("doc_type") != expectations["doc_type"]:
        return None
    truth, got = expectations["fields"], outputs.get("fields") or {}
    names = [f for f in t["required"] if truth.get(f) is not None]
    wrong = [f for f in names if _norm(got.get(f)) != _norm(truth.get(f))]
    return Feedback(value=1 - len(wrong) / len(names) if names else 1.0, rationale=f"wrong: {wrong}" if wrong else "all correct")


mlflow.set_experiment(cfg.doc_experiment)
with mlflow.start_run(run_name="eval doc intake"):
    doc_eval = mlflow.genai.evaluate(data=doc_ds, predict_fn=predict_doc,
                                     scorers=[route_correct, doc_type_correct, field_accuracy])
print({k: round(v, 3) for k, v in doc_eval.metrics.items()})

# COMMAND ----------

# MAGIC %md
# MAGIC ## Service assist

# COMMAND ----------

cases = spark.table(f"{cfg.full_schema}.service_cases").orderBy("case_id").collect()
assist_records = [{
    "inputs": {"message": c.message, "customer_id": c.customer_id, "channel": c.channel},
    "expectations": {"expected_facts": list(c.expected_facts)},
} for c in cases]
assist_ds = uc_dataset("eval_service_assist", assist_records)

assist_settings = AssistSettings.from_cfg(cfg)


async def _answer(message, customer_id, channel):
    # A new agent (and HTTP client) per row: each row runs on its own event loop, and an async client's
    # connections cannot be shared across event loops.
    return await ServiceAssist(assist_settings).answer(message, customer_id=customer_id, channel=channel)


assist_outputs = {}  # (message, customer_id) -> reply, for scoring rows directly if needed (see below)


def predict_assist(message, customer_id, channel):
    reply = run_sync(_answer(message, customer_id, channel))["reply"]
    assist_outputs[(message, customer_id)] = reply
    return reply


assist_scorers = [
    Correctness(),
    RelevanceToQuery(),
    Safety(),
    *[Guidelines(name=name, guidelines=rules) for name, rules in REPLY_GUIDELINES.items()],
]

mlflow.set_experiment(cfg.assist_experiment)
mlflow.openai.autolog()  # trace the agent's model and tool calls, so each row has a trace with its scores
with mlflow.start_run(run_name="eval service assist"):
    assist_eval = mlflow.genai.evaluate(data=assist_ds, predict_fn=predict_assist, scorers=assist_scorers)
print({k: round(v, 3) for k, v in assist_eval.metrics.items()})

# COMMAND ----------

# MAGIC %md
# MAGIC ## Results per row
# MAGIC
# MAGIC Open the evaluation runs in the experiments for the full traces. The tables below list rows that failed a scorer.

# COMMAND ----------

def _is_failure(value, error):
    value = getattr(value, "value", value)  # judges may return a "yes"/"no" rating enum
    if error is not None:
        return True
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value < 1
    return isinstance(value, str) and value.strip().lower() in ("no", "false")


def _blank(v):
    return v is None or (isinstance(v, float) and v != v)


def _scored_directly(scorers, records, outputs):
    """Run each scorer on the saved outputs: the same judges, called the way evaluate() calls them."""
    import inspect

    rows = []
    for rec in records:
        out = outputs.get((rec["inputs"].get("message"), rec["inputs"].get("customer_id")))
        if out is None:
            continue
        available = {"inputs": rec["inputs"], "outputs": out, "expectations": rec.get("expectations")}
        for sc in scorers:
            accepted = inspect.signature(sc).parameters
            try:
                fb = sc(**{k: v for k, v in available.items() if k in accepted})
                value, why, error = getattr(fb, "value", fb), getattr(fb, "rationale", ""), getattr(fb, "error", None)
            except Exception as e:
                value, why, error = None, "", e
            if _is_failure(value, error):
                rows.append(("", sc.name, str(getattr(value, "value", value)), rec["inputs"].get("message", "")[:200],
                             str(error or why or "")[:400], out[:300]))
    return rows


def failures(eval_result, scorers=None, records=None, outputs=None):
    """One row per failed scorer per evaluation row.

    Reads the result table (columns <scorer>/value, /rationale, /error_message). MLflow fills it by reading the
    run's traces back and leaves it empty if that fails; then, when scorers and saved outputs are given, the
    rows are scored directly instead.
    """
    schema = "trace_id STRING, scorer STRING, value STRING, request STRING, rationale STRING"
    df = eval_result.result_df
    if df is None or df.empty or not any(c.endswith("/value") for c in df.columns):
        if scorers and records and outputs:
            print("  The evaluation result table is empty (MLflow could not read this run's traces back); "
                  "scoring the saved replies directly.")
            rows = _scored_directly(scorers, records, outputs)
            return spark.createDataFrame(rows, schema + ", reply STRING") if rows else None
        print("  The evaluation result table is empty; open the evaluation run in the experiment for per-row results.")
        return None
    request_col = next((c for c in ("request", "inputs") if c in df.columns), None)
    scorers = [c.removesuffix("/value") for c in df.columns if c.endswith("/value")]
    rows = []
    for _, r in df.iterrows():
        for name in scorers:
            value, error = r.get(f"{name}/value"), r.get(f"{name}/error_message")
            error = None if _blank(error) else error
            if error is None and _blank(value):
                continue  # the scorer did not run for this row
            if _is_failure(value, error):
                why = error or r.get(f"{name}/rationale") or ""
                rows.append((str(r.get("trace_id", "")), name, str(getattr(value, "value", value)),
                             str(r.get(request_col, ""))[:200] if request_col else "", str(why)[:400]))
    return spark.createDataFrame(rows, schema) if rows else None


for label, res, extra in (("document intake", doc_eval, {}),
                          ("service assist", assist_eval,
                           {"scorers": assist_scorers, "records": assist_records, "outputs": assist_outputs})):
    print(label)
    df = failures(res, **extra)
    print(label, "- failing rows:", 0 if df is None else df.count())
    if df is not None:
        display(df)
