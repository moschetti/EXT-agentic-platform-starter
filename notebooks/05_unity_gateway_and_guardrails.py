# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Unity Gateway and guardrails
# MAGIC
# MAGIC Every LLM call the agents make goes through a **Unity Gateway model service**: a Unity Catalog securable
# MAGIC that routes to one or more models. Because it is a securable, it has an owner, `EXECUTE` grants and audit logs,
# MAGIC and it can carry:
# MAGIC
# MAGIC | Control | Where in this notebook |
# MAGIC |---|---|
# MAGIC | Routing and fallback to a second model, without changing agent code | §1 |
# MAGIC | Rate limits per user, group, service principal or service | §1, §5 |
# MAGIC | Inference logging to a Unity Catalog table | §1, §6 |
# MAGIC | **Service policies (guardrails)** on input and output: built-in and custom | §3, §4 |
# MAGIC
# MAGIC Each agent gets its own service, so each has its own limits, logs and guardrails:
# MAGIC
# MAGIC | Service | Guardrails | Why |
# MAGIC |---|---|---|
# MAGIC | `doc_service` (document intake) | unsafe content, jailbreak | Documents can carry prompt injection. Sensitive-data redaction is **off** here because the agent must extract IBANs; that data is protected with Unity Catalog grants and masks instead |
# MAGIC | `assist_service` (service assist) | unsafe content, jailbreak, sensitive data (redact), hallucination, three custom policies, one LLM judge | Customer-facing text |
# MAGIC
# MAGIC Unity Gateway model services are GA. **Service policies are Beta**: a workspace admin turns on the **Unity Ai
# MAGIC Gateway Service Policies** preview on the workspace **Previews** page (checklist W9 in `00`). Attaching a policy
# MAGIC needs `MANAGE` on the service, which you have as its owner.
# MAGIC
# MAGIC Sections 1–3 and 5–6 run in the `agentic-starter-deploy` job. Section 4 is done by hand in the Unity Gateway UI
# MAGIC after that job; the guardrail tests after it show `allowed` until the policies are attached.

# COMMAND ----------

# MAGIC %pip install -q "databricks-sdk>=0.124.0" "databricks-openai>=0.10"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./01_configuration $run_checks="false"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · Create the model services

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.catalog import (
    InferenceTableConfig,
    ModelService,
    ModelServiceConfig,
    ModelServiceConfigDestinationConfig,
    ModelServiceConfigDestinationConfigDestinationType as DestinationType,
    ModelServiceConfigFallbackConfig,
    ModelServiceConfigPayPerTokenConfig,
    ModelServiceConfigRoutingConfig,
    RateLimit,
    RateLimitRateLimitKey,
    RateLimitRateLimitRenewalPeriod,
)

w = WorkspaceClient()
PARENT = f"schemas/{cfg.catalog}.{cfg.schema}"
print("Model behind the services:", cfg.chat_model)


def destination(name, model):
    return ModelServiceConfigDestinationConfig(
        name=name,
        destination_type=DestinationType.DESTINATION_TYPE_PAY_PER_TOKEN_FOUNDATION_MODEL,
        pay_per_token_config=ModelServiceConfigPayPerTokenConfig(model=f"models/{model}"),
        traffic_percentage=100,
    )


def find_service(full_name):
    """The service with its full configuration (the list call returns a basic view without it), or None."""
    short = full_name.split(".")[-1]
    for s in w.ai_gateway.list_model_services(parent=PARENT):
        if (s.name or "").split(".")[-1].split("/")[-1] == short:
            return w.ai_gateway.get_model_service(name=s.name)
    return None


def routed_models(service):
    r = service.config.routing if service and service.config else None
    return [d.pay_per_token_config.model for d in (r.destinations or [])] if r else []


def ensure_service(full_name, comment, requests_per_minute, log_prefix):
    existing = find_service(full_name)
    if existing:
        current = (routed_models(existing) or [None])[0]
        if current == f"models/{cfg.chat_model}":
            print(f"exists: {existing.name} → {current}")
            return existing
        # The model changed in 01_configuration. UpdateModelService does not accept changes to config.routing, so
        # recreate the service under the same name. Policies attached to it, and grants on it, are removed with it.
        w.ai_gateway.delete_model_service(name=existing.name)
        print(f"recreating {existing.name}: {current} → models/{cfg.chat_model}. "
              "Re-attach its guardrail policies (section 4) and re-run 91_grant_agent_access if agents are deployed.")
    routing = ModelServiceConfigRoutingConfig(destinations=[destination("primary", cfg.chat_model)])
    if cfg.fallback_model:
        routing.fallback = ModelServiceConfigFallbackConfig(destinations=[destination("fallback", cfg.fallback_model)])
    config = ModelServiceConfig(
        routing=routing,
        rate_limits=[RateLimit(key=RateLimitRateLimitKey.RATE_LIMIT_KEY_USER_DEFAULT,
                               renewal_period=RateLimitRateLimitRenewalPeriod.RATE_LIMIT_RENEWAL_PERIOD_MINUTE,
                               requests=requests_per_minute)],
        inference_table=InferenceTableConfig(parent=PARENT, table_name_prefix=log_prefix),
    )
    short = full_name.split(".")[-1]
    try:
        created = w.ai_gateway.create_model_service(
            model_service=ModelService(comment=comment, config=config), parent=PARENT, model_service_id=short)
    except Exception as e:
        # Inference logging needs CREATE TABLE on the schema; create the service without it rather than fail.
        print(f"create with inference table failed ({str(e)[:160]}); retrying without inference logging")
        config.inference_table = None
        created = w.ai_gateway.create_model_service(
            model_service=ModelService(comment=comment, config=config), parent=PARENT, model_service_id=short)
    print(f"created: {created.name}")
    return created


doc_svc = ensure_service(cfg.doc_service, "Document intake agent: LLM calls", 300, f"{cfg.name_prefix}_doc_intake")
assist_svc = ensure_service(cfg.assist_service, "Service assist agent: LLM calls, with guardrails", 120, f"{cfg.name_prefix}_service_assist")
# Confirm what each service routes to now. After a service is recreated, the gateway can take a few minutes to
# route to the new destination.
for svc in (doc_svc, assist_svc):
    print(svc.name, "→", routed_models(w.ai_gateway.get_model_service(name=svc.name)))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Call a model service
# MAGIC
# MAGIC The gateway speaks the OpenAI API. The model name is the Unity Catalog name of the service, so changing the model
# MAGIC behind it (in the service config) needs no change in any agent.

# COMMAND ----------

from databricks_openai import DatabricksOpenAI

client = DatabricksOpenAI(use_ai_gateway=True)


def text_of(content):
    """Message content as text. Some models return a list of content parts instead of a string."""
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else getattr(p, "text", "") or "" for p in content)
    return content or ""


def ask_full(service, text, system=None):
    """Return (reply text, policy decision or None). A denied call is HTTP 200: the reply names the policy and the
    response carries a top-level `databricks_service_policy` object with the reason."""
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": text}]
    try:
        # No max_tokens or temperature: some models reject them (GPT-5 family: max_tokens; newer Claude: temperature).
        r = client.chat.completions.create(model=service, messages=messages)
        return (text_of(r.choices[0].message.content), (r.model_extra or {}).get("databricks_service_policy"),
                r.choices[0].finish_reason)
    except Exception as e:
        return f"<error> {e}", None, None


def ask(service, text, system=None):
    return ask_full(service, text, system)[0]


# Both services must answer before anything is built on them; ask() returns errors as text, so check here.
for svc in (cfg.doc_service, cfg.assist_service):
    reply = ask(svc, f"In one sentence: what does an insurance deductible mean? You work for {cfg.org_name}.")
    print(f"{svc}: {reply}")
    if reply.startswith("<error>"):
        raise RuntimeError(f"{svc} did not answer. If the error is ENDPOINT_NOT_FOUND or 'not available in your "
                           f"region', {cfg.chat_model} is not served here: run 01 with checks for models that answer, "
                           "set chat_model, and run this notebook again (it recreates the services).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Custom guardrail functions
# MAGIC
# MAGIC A custom policy is a SQL function that receives the event (`event:type` is `request` or `response`) and returns
# MAGIC `ALLOW` or `DENY` with a reason. Only a subset of SQL is allowed in policy functions (no subqueries, no `ai_query`);
# MAGIC see the service policy function reference. These three implement rules from the knowledge base article
# MAGIC "What a service rep must not do".

# COMMAND ----------

S = f"`{cfg.catalog}`.`{cfg.schema}`"

spark.sql(f"""
CREATE OR REPLACE FUNCTION {S}.deny_restricted_topics(event VARIANT)
RETURNS VARIANT
LANGUAGE SQL
COMMENT 'Guardrail (input): the service assistant does not handle investment, tax or legal advice.'
RETURN
  CASE
    WHEN event:type::string = 'request'
      AND (contains(lower(event:context.message::string), 'investment')
        OR contains(lower(event:context.message::string), 'equity fund')
        OR contains(lower(event:context.message::string), 'savings into')
        OR contains(lower(event:context.message::string), 'legal advice')
        OR contains(lower(event:context.message::string), 'tax advice'))
    THEN to_variant_object(named_struct('result', 'DENY', 'reason',
      'This assistant does not give investment, tax or legal advice. Refer the customer to the right team.'))
    ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
  END
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {S}.block_claim_promises(event VARIANT)
RETURNS VARIANT
LANGUAGE SQL
COMMENT 'Guardrail (output): replies must not promise that a claim is approved or state a payout before a decision.'
RETURN
  CASE
    WHEN event:type::string = 'response'
      AND (contains(lower(event:context.message::string), 'your claim is approved')
        OR contains(lower(event:context.message::string), 'your claim will be approved')
        OR contains(lower(event:context.message::string), 'we guarantee')
        OR contains(lower(event:context.message::string), 'you will receive'))
    THEN to_variant_object(named_struct('result', 'DENY', 'reason',
      'Reply blocked: it promised a claim outcome or payout before a decision.'))
    ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
  END
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {S}.block_insecure_links(event VARIANT)
RETURNS VARIANT
LANGUAGE SQL
COMMENT 'Guardrail (output): replies must not contain insecure links.'
RETURN
  CASE
    WHEN event:type::string = 'response'
      AND (contains(lower(event:context.message::string), 'http://')
        OR contains(lower(event:context.message::string), 'javascript:'))
    THEN to_variant_object(named_struct('result', 'DENY', 'reason', 'Reply blocked: it contained an insecure link.'))
    ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
  END
""")

# The functions are plain SQL, so they can be unit tested before they are attached.
display(spark.sql(f"""
SELECT
  {S}.deny_restricted_topics(parse_json('{{"type":"request","context":{{"message":"Should I buy equity funds?"}}}}')):result::string AS topics_deny,
  {S}.deny_restricted_topics(parse_json('{{"type":"request","context":{{"message":"How do I report water damage?"}}}}')):result::string AS topics_allow,
  {S}.block_claim_promises(parse_json('{{"type":"response","context":{{"message":"Good news, your claim is approved!"}}}}')):result::string AS promise_deny,
  {S}.block_insecure_links(parse_json('{{"type":"response","context":{{"message":"Upload at http://x.example"}}}}')):result::string AS link_deny
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Attach the policies (Unity Gateway UI)
# MAGIC
# MAGIC Attach the policies in the Unity Gateway UI. For each service: **AI Gateway → Models → select the service →
# MAGIC Policies → New policy**. Set each one up as in the tables below, then re-run the guardrail tests in the next
# MAGIC section.
# MAGIC
# MAGIC **`<prefix>_service_assist_llm`**
# MAGIC
# MAGIC | Rank | Name | Guardrail type | Input | Output | Settings |
# MAGIC |---|---|---|---|---|---|
# MAGIC | 1 | `block-unsafe-content` | Unsafe Content | ✓ | ✓ | |
# MAGIC | 2 | `block-jailbreak` | Jailbreak | ✓ | | |
# MAGIC | 3 | `redact-sensitive-data` | Sensitive Data Detection | ✓ | ✓ | Action: **Redact**. Categories: email, phone number, credit card, IBAN, IP address |
# MAGIC | 4 | `block-hallucination` | Hallucination | | ✓ | Mode: **Log** (see below) |
# MAGIC | 10 | `deny-restricted-topics` | Custom → function `deny_restricted_topics` | ✓ | | |
# MAGIC | 20 | `block-claim-promises` | Custom → function `block_claim_promises` | | ✓ | |
# MAGIC | 30 | `block-insecure-links` | Custom → function `block_insecure_links` | | ✓ | |
# MAGIC | 40 | `judge-no-unsupported-claims` | Custom → LLM-as-a-judge | | ✓ | Mode: **Log**. Criteria: *"Deny if the reply states coverage, amounts, deadlines or decisions that are not stated in the conversation, or if it is rude or blames the customer."* |
# MAGIC
# MAGIC **`<prefix>_doc_intake_llm`**
# MAGIC
# MAGIC | Rank | Name | Guardrail type | Input | Output |
# MAGIC |---|---|---|---|---|
# MAGIC | 1 | `block-unsafe-content` | Unsafe Content | ✓ | ✓ |
# MAGIC | 2 | `block-jailbreak` | Jailbreak | ✓ | |
# MAGIC
# MAGIC **Why two policies run in Log mode.** An output-phase policy judges only the model's reply. It does not see the
# MAGIC tool results the agent used. A tool-using agent's reply quotes facts from those results ("within 14 days"), so
# MAGIC a hallucination or "unsupported claims" judge at the gateway flags correct, grounded replies. In Log mode the
# MAGIC policy records what it would have decided without blocking. Groundedness and correctness are checked where the
# MAGIC whole trace is visible: MLflow evaluation (`08`) and production monitoring (`09`). Use output-phase judges in
# MAGIC Enforce mode for rules that the reply alone can decide, such as tone or the custom SQL policies above.
# MAGIC
# MAGIC Allow a short time after attaching before testing. Policies are fail-closed: an error while evaluating a policy denies the call.

# COMMAND ----------

print("Attach policies to:")
print("  ", assist_svc.name)
print("  ", doc_svc.name)
print(f"Custom functions: {cfg.full_schema}.deny_restricted_topics, block_claim_promises, block_insecure_links")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test the guardrails
# MAGIC
# MAGIC The test cases are in `agents/service_assist/assist/guardrails.py`; this cell writes them to `guardrail_tests`
# MAGIC (also used by `07`) and runs each against the assist service.
# MAGIC
# MAGIC - A denied call still returns HTTP 200: the reply names the policy, and `policy_decision` shows the reason.
# MAGIC - The sensitive-data and claim-promise tests ask the model to repeat a message back, so redaction placeholders
# MAGIC   (such as `[US_SSN]`) and blocked outputs are visible in the result.
# MAGIC - `refused` means the model declined by itself. Models are the first line of defence and guardrails the second;
# MAGIC   the ungrounded-answer test passes on either.
# MAGIC - `empty` means no text came back; `finish_reason` says why.
# MAGIC
# MAGIC To see which policies ran on each request, open the service's **Policies** tab → **See execution flow**.

# COMMAND ----------

import os
import sys

sys.path.insert(0, os.path.abspath("../agents/service_assist"))
from assist.guardrails import classify, guardrail_tests, passed

sample = spark.sql(f"SELECT email, phone, iban FROM {cfg.full_schema}.customers ORDER BY customer_id LIMIT 1").first()
spark.createDataFrame(guardrail_tests(sample.email, sample.phone, sample.iban),
                      "test_id STRING, category STRING, message STRING, expected STRING, guardrail STRING") \
    .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{cfg.full_schema}.guardrail_tests")

SYSTEM = f"You draft replies for {cfg.org_name} customer service reps. Follow the user's instructions exactly."

results = []
for row in spark.table(f"{cfg.full_schema}.guardrail_tests").orderBy("test_id").collect():
    reply, policy, finish = ask_full(cfg.assist_service, row.message, SYSTEM)
    observed = classify(reply, policy)
    results.append((row.test_id, row.category, row.guardrail, row.expected, observed, passed(observed, row.expected),
                    finish, str(policy)[:300] if policy else None, reply[:300]))

df = spark.createDataFrame(results, "test_id STRING, category STRING, guardrail STRING, expected STRING, observed STRING, "
                                    "passed BOOLEAN, finish_reason STRING, policy_decision STRING, reply STRING")
print(f"{sum(r[5] for r in results)}/{len(results)} passed")
display(df)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Rate limits
# MAGIC
# MAGIC A throwaway service with a limit of 3 requests per minute per user. The fourth call in the same minute is rejected.

# COMMAND ----------

demo_name = f"{cfg.full_schema}.{cfg.name_prefix}_rate_limit_demo"
demo = find_service(demo_name) or w.ai_gateway.create_model_service(
    model_service=ModelService(
        comment="Rate limit demo; safe to delete",
        config=ModelServiceConfig(
            routing=ModelServiceConfigRoutingConfig(destinations=[destination("primary", cfg.chat_model)]),
            rate_limits=[RateLimit(key=RateLimitRateLimitKey.RATE_LIMIT_KEY_USER_DEFAULT,
                                   renewal_period=RateLimitRateLimitRenewalPeriod.RATE_LIMIT_RENEWAL_PERIOD_MINUTE,
                                   requests=3)],
        ),
    ),
    parent=PARENT, model_service_id=demo_name.split(".")[-1])

for i in range(5):
    print(i + 1, ask(demo_name, "Say OK.")[:120])

w.ai_gateway.delete_model_service(name=demo.name)
print("deleted", demo.name)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · Inference logs
# MAGIC
# MAGIC Each service with inference logging writes requests and responses to a Delta table in the schema. Access to that
# MAGIC table is governed like any other table: it contains prompts, so grant it narrowly.

# COMMAND ----------

display(spark.sql(f"SHOW TABLES IN {S} LIKE '{cfg.name_prefix}_*'"))
