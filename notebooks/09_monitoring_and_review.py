# Databricks notebook source
# MAGIC %md
# MAGIC # 09 · Production monitoring and human review
# MAGIC
# MAGIC After deployment, quality is watched continuously and people review what the agents are unsure about.
# MAGIC
# MAGIC | Part | What | Status |
# MAGIC |---|---|---|
# MAGIC | Production monitoring | Scorers from `08` run automatically on a sample of the deployed agent's traces | Beta |
# MAGIC | Review queues | Traces are routed to named reviewers one at a time, with the questions you define; answers are stored on the trace | Beta (workspace admin enables "MLflow Review Queues" in Previews) |
# MAGIC | `review_cases` | The document intake agent's own work queue: every `human_review` or sampled-audit case, with the trace id | Delta table |
# MAGIC
# MAGIC Labels from reviewers become new evaluation rows, so the next evaluation in `08` covers the cases that went wrong.
# MAGIC
# MAGIC Run this after the agents are deployed and have handled some requests (or after `06`/`07`, which log traces to the
# MAGIC same experiments).

# COMMAND ----------

# MAGIC %pip install -q "mlflow[databricks]>=3.17.1" "databricks-sdk>=0.124.0"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./01_configuration $run_checks="false"

# COMMAND ----------

import os
import sys

import mlflow
from mlflow.genai.scorers import Guidelines, Safety, ScorerSamplingConfig

sys.path.insert(0, os.path.abspath("../agents/service_assist"))
from assist.guardrails import REPLY_GUIDELINES  # same judge guidelines as the evaluation in 08

dbutils.widgets.text("reviewer", spark.sql("SELECT current_user()").first()[0], "Reviewer (user name)")
REVIEWER = dbutils.widgets.get("reviewer")

assist_exp = mlflow.set_experiment(cfg.assist_experiment)
doc_exp = mlflow.get_experiment_by_name(cfg.doc_experiment)
print("service assist experiment:", assist_exp.experiment_id, "| document intake experiment:", doc_exp.experiment_id if doc_exp else None)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Production monitoring
# MAGIC
# MAGIC Registers scorers on the service assist experiment. Every new trace is scored at the sample rate; results appear as
# MAGIC assessments on the traces after 15–20 minutes. At most 20 scorers per experiment.

# COMMAND ----------

def start(scorer_obj, name, rate):
    try:
        registered = scorer_obj.register(name=name)
    except Exception as e:
        print(f"{name}: already registered or failed to register ({str(e)[:120]}); trying to start it")
        from mlflow.genai.scorers import get_scorer
        registered = get_scorer(name=name)
    return registered.start(sampling_config=ScorerSamplingConfig(sample_rate=rate))


start(Safety(), "safety", 1.0)
start(Guidelines(name="no_promises_or_advice", guidelines=REPLY_GUIDELINES["no_promises_or_advice"]),
      "no_promises_or_advice", 1.0)
start(Guidelines(name="format", guidelines=REPLY_GUIDELINES["format"]), "format", 0.3)

from mlflow.genai.scorers import list_scorers
for s in list_scorers():
    print(s.name, getattr(s, "sample_rate", ""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Review queues
# MAGIC
# MAGIC One queue per agent. Questions are what the reviewer answers for each item. Re-running reuses the queues and
# MAGIC adds only new traces.

# COMMAND ----------

from mlflow.genai.databricks import review_queues as rq

mlflow.set_tracking_uri("databricks")


def queue_with_question(experiment_id, queue_name, title, options):
    """Get or create a custom queue with one question, and make REVIEWER a member. Safe to re-run."""
    queue = next((q for q in rq.list_review_queues(experiment_id) if q.display_name == queue_name), None)
    if queue is None:
        question = next((q for q in rq.list_review_questions(experiment_id) if q.title == title), None) \
            or rq.create_review_question(experiment_id, title=title, type="FEEDBACK",
                                         input=rq.InputCategorical(options=options))
        queue = rq.create_review_queue(experiment_id, queue_name, question_ids=[question.question_id])
        print(f"created queue '{queue_name}' ({queue.queue_id})")
    else:
        print(f"reusing queue '{queue_name}' ({queue.queue_id})")
    try:
        rq.add_review_queue_member(experiment_id, queue.queue_id, REVIEWER)
    except Exception as e:
        if "ALREADY_EXISTS" not in str(e):
            raise
    return queue


def add_new_items(experiment_id, queue, trace_ids):
    """Add only the traces that are not in the queue yet."""
    present = {i.trace_id for i in rq.list_review_queue_items(experiment_id, queue.queue_id)}
    new = [t for t in trace_ids if t not in present]
    if new:
        rq.add_review_queue_items(experiment_id, queue.queue_id, trace_ids=new)
    return len(new)


assist_queue = queue_with_question(assist_exp.experiment_id, "Service assist: reply review",
                                   "Could the rep send this suggested reply as it is?", ["yes", "needs edits", "no"])

# Service assist: send the traces a guardrail blocked, plus the most recent ones.
traces = mlflow.search_traces(experiment_ids=[assist_exp.experiment_id], max_results=50, order_by=["timestamp_ms DESC"])
assist_ids = list(traces["trace_id"][:20])
print(f"service assist queue: {add_new_items(assist_exp.experiment_id, assist_queue, assist_ids)} traces added")

# COMMAND ----------

if doc_exp:
    doc_queue = queue_with_question(doc_exp.experiment_id, "Document intake: human review",
                                    "Is the extracted data correct and the route right?", ["correct", "fields wrong", "route wrong"])
    ids = [r.trace_id for r in spark.sql(f"""
        SELECT trace_id FROM {cfg.full_schema}.review_cases
        WHERE status = 'open' AND trace_id IS NOT NULL ORDER BY created_at DESC LIMIT 20""").collect()]
    print(f"document intake queue: {add_new_items(doc_exp.experiment_id, doc_queue, ids)} traces added from review_cases")

# COMMAND ----------

# MAGIC %md
# MAGIC Reviewers open the queue from the experiment's **Reviews** tab (or the link MLflow shows for the queue) and answer one
# MAGIC item at a time.
# MAGIC
# MAGIC ## Read the labels back
# MAGIC
# MAGIC Answers are stored as assessments on the traces. Labeled traces can be merged into the evaluation datasets from `08`.

# COMMAND ----------

labeled = mlflow.search_traces(experiment_ids=[assist_exp.experiment_id], max_results=200)
rows = []
for _, t in labeled.iterrows():
    for a in t.get("assessments") or []:
        if isinstance(a, dict) and a.get("source", {}).get("source_type") == "HUMAN":
            rows.append((t["trace_id"], a.get("assessment_name"), str(a.get("feedback", {}).get("value")),
                         a.get("source", {}).get("source_id")))
if rows:
    display(spark.createDataFrame(rows, "trace_id STRING, question STRING, answer STRING, reviewer STRING"))
else:
    print("No human labels yet. Answer some items in the review queue, then re-run this cell.")

# COMMAND ----------

# Add the traces a reviewer rejected to the evaluation dataset, so the next run of 08 includes them.
rejected = [r[0] for r in rows if r[2] in ("no", "needs edits")]
if rejected:
    from mlflow.genai import datasets

    ds = datasets.get_dataset(name=f"{cfg.full_schema}.eval_service_assist")
    ds.merge_records(labeled[labeled["trace_id"].isin(rejected)])
    print(f"added {len(rejected)} reviewed traces to eval_service_assist")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Close a document review case
# MAGIC
# MAGIC The `review_cases` table is the work queue a downstream process or a review app would update. Example:

# COMMAND ----------

display(spark.sql(f"SELECT status, count(*) AS n FROM {cfg.full_schema}.review_cases GROUP BY ALL"))
first_open = spark.sql(f"SELECT document_id FROM {cfg.full_schema}.review_cases WHERE status = 'open' ORDER BY created_at LIMIT 1").first()
if first_open:
    spark.sql(f"""
        UPDATE {cfg.full_schema}.review_cases
        SET status = 'corrected', reviewer = current_user(), review_note = 'Example: IBAN corrected after calling the customer',
            reviewed_at = current_timestamp()
        WHERE document_id = '{first_open.document_id}' AND status = 'open'
    """)
    print("closed", first_open.document_id)
