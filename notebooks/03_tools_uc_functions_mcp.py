# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Tools: Unity Catalog functions, AI Search and MCP
# MAGIC
# MAGIC Agent tools are **Unity Catalog functions** in `<schema>_tools`. That gives every tool the same controls
# MAGIC as a table: an owner, `EXECUTE` grants, lineage, and audit logs of every call.
# MAGIC
# MAGIC Databricks exposes all functions in a schema through the **Unity Catalog functions MCP server**
# MAGIC (`/api/2.0/mcp/functions/<catalog>/<schema>`), so any MCP-capable agent framework can use them
# MAGIC without custom integration code. It needs the **Managed MCP Servers** preview (checklist W8 in `00`). The
# MAGIC Databricks documentation lists this server as a legacy workspace endpoint and points new Python agents to
# MAGIC `UCFunctionToolkit` or a `uc_function` tool in `agent.toml`; both call the same functions, so the tools
# MAGIC themselves do not change. See `docs/feature-status.md`.
# MAGIC
# MAGIC | Function | Used by | What it does |
# MAGIC |---|---|---|
# MAGIC | `get_customer` | service assist | Customer profile without contact details or identifiers |
# MAGIC | `get_policies` | service assist | A customer's policies |
# MAGIC | `get_recent_claims` | service assist | A customer's latest claims |
# MAGIC | `search_kb` | service assist | Semantic search over the knowledge base (Databricks AI Search) |
# MAGIC | `check_claim_policy` | document intake, pipeline | Is the policy known, active on the incident date, and is the amount within coverage |
# MAGIC | `is_valid_iban` | document intake, pipeline | IBAN checksum (Python function) |
# MAGIC
# MAGIC The document intake agent calls its two checks itself, in fixed order. The service assist agent chooses
# MAGIC which tools to call through MCP.

# COMMAND ----------

# MAGIC %pip install -q "databricks-mcp>=0.4" "databricks-sdk>=0.124.0"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./01_configuration $run_checks="false"

# COMMAND ----------

S = f"`{cfg.catalog}`.`{cfg.schema}`"
T = f"`{cfg.catalog}`.`{cfg.tools_schema}`"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Lookup functions

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE FUNCTION {T}.get_customer(customer_id STRING COMMENT 'Customer id, for example C00042')
RETURNS TABLE (customer_id STRING, full_name STRING, segment STRING, preferred_language STRING, city STRING, customer_since DATE)
COMMENT 'Returns the profile of one customer. Contains no contact details, account numbers or national identifiers.'
RETURN
  SELECT c.customer_id, c.full_name, c.segment, c.preferred_language, c.city, c.customer_since
  FROM {S}.customers c
  WHERE c.customer_id = get_customer.customer_id
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {T}.get_policies(customer_id STRING COMMENT 'Customer id, for example C00042')
RETURNS TABLE (policy_id STRING, product STRING, status STRING, start_date DATE, end_date DATE,
               deductible_eur BIGINT, coverage_limit_eur BIGINT)
COMMENT 'Returns all policies of a customer with product, status, validity dates, deductible and coverage limit.'
RETURN
  SELECT p.policy_id, p.product, p.status, p.start_date, p.end_date, p.deductible_eur, p.coverage_limit_eur
  FROM {S}.policies p
  WHERE p.customer_id = get_policies.customer_id
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {T}.get_recent_claims(customer_id STRING COMMENT 'Customer id, for example C00042')
RETURNS TABLE (claim_id STRING, policy_id STRING, claim_type STRING, incident_date DATE, reported_date DATE,
               status STRING, claimed_amount_eur DOUBLE)
COMMENT 'Returns the five most recent claims of a customer with status and claimed amount.'
RETURN
  SELECT cl.claim_id, cl.policy_id, cl.claim_type, cl.incident_date, cl.reported_date, cl.status, cl.claimed_amount_eur
  FROM {S}.claims cl
  WHERE cl.customer_id = get_recent_claims.customer_id
  ORDER BY cl.reported_date DESC
  LIMIT 5
""")

display(spark.sql(f"SELECT * FROM {T}.get_policies('C00001')"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Validation functions
# MAGIC
# MAGIC `check_claim_policy` always returns one row, also when the policy does not exist, so callers can treat a
# MAGIC missing policy as a failed check instead of an empty result.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE FUNCTION {T}.check_claim_policy(
  policy_id STRING COMMENT 'Policy number, for example P000123',
  incident_date DATE COMMENT 'Date of the incident or invoice; NULL if unknown',
  amount_eur DOUBLE COMMENT 'Claimed or invoiced amount in EUR; NULL if unknown')
RETURNS TABLE (policy_found BOOLEAN, policy_active_on_date BOOLEAN, within_coverage BOOLEAN,
               product STRING, holder_customer_id STRING, deductible_eur BIGINT, coverage_limit_eur BIGINT)
COMMENT 'Checks a claim or invoice against the policy: does the policy exist, was it active on the date, and is the amount within the coverage limit.'
RETURN
  SELECT
    p.policy_id IS NOT NULL,
    coalesce(check_claim_policy.incident_date BETWEEN p.start_date AND p.end_date, false),
    coalesce(check_claim_policy.amount_eur <= p.coverage_limit_eur, false),
    p.product, p.customer_id, p.deductible_eur, p.coverage_limit_eur
  FROM (SELECT 1 AS one) AS d
  LEFT JOIN {S}.policies p ON p.policy_id = check_claim_policy.policy_id
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {T}.is_valid_iban(iban STRING COMMENT 'IBAN, spaces allowed')
RETURNS BOOLEAN
LANGUAGE PYTHON
COMMENT 'True if the IBAN has a valid format and mod-97 checksum.'
AS $$
if not iban:
    return False
s = iban.replace(" ", "").upper()
if not (15 <= len(s) <= 34 and s[:2].isalpha() and s[2:4].isdigit() and s.isalnum()):
    return False
digits = "".join(str(int(ch, 36)) for ch in s[4:] + s[:4])
return int(digits) % 97 == 1
$$
""")

display(spark.sql(f"""
SELECT d.document_id, d.scenario, chk.*
FROM {S}.documents d,
LATERAL {T}.check_claim_policy(
  get_json_object(d.truth_fields, '$.policy_number'),
  try_cast(get_json_object(d.truth_fields, '$.incident_date') AS DATE),
  try_cast(get_json_object(d.truth_fields, '$.estimated_amount_eur') AS DOUBLE)) AS chk
WHERE d.doc_type IN ('motor_claim', 'property_claim')
LIMIT 10
"""))
display(spark.sql(f"""
SELECT scenario, {T}.is_valid_iban(get_json_object(truth_fields, '$.iban')) AS iban_valid, count(*) AS n
FROM {S}.documents WHERE get_json_object(truth_fields, '$.iban') IS NOT NULL GROUP BY ALL ORDER BY ALL
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Knowledge base search with Databricks AI Search
# MAGIC
# MAGIC A delta sync index over `kb_articles` keeps itself in step with the table. Databricks computes the embeddings
# MAGIC with the endpoint in `cfg.embedding_model`. Creating the endpoint and the first sync takes several minutes.

# COMMAND ----------

import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.vectorsearch import (
    DeltaSyncVectorIndexSpecRequest,
    EmbeddingSourceColumn,
    EndpointStatusState,
    EndpointType,
    PipelineType,
    VectorIndexType,
)

w = WorkspaceClient()


def wait_for(what, poll, timeout_min, every_s=30):
    """Call poll() until it returns True; print progress; fail after timeout_min minutes."""
    deadline = time.time() + timeout_min * 60
    while not poll():
        if time.time() > deadline:
            raise TimeoutError(f"{what} not ready after {timeout_min} minutes; see the checks in the cell below.")
        time.sleep(every_s)


# The first AI Search endpoint in a workspace often takes 10-30 minutes to provision.
if cfg.ai_search_endpoint not in {e.name for e in w.vector_search_endpoints.list_endpoints()}:
    print(f"Creating AI Search endpoint {cfg.ai_search_endpoint}...")
    w.vector_search_endpoints.create_endpoint(name=cfg.ai_search_endpoint, endpoint_type=EndpointType.STANDARD)


def endpoint_online():
    st = w.vector_search_endpoints.get_endpoint(cfg.ai_search_endpoint).endpoint_status
    print(time.strftime("%H:%M:%S"), "endpoint:", st.state.value if st and st.state else None, "-", (st.message if st else "") or "")
    if st and st.state in (EndpointStatusState.OFFLINE, EndpointStatusState.DELETED):
        raise RuntimeError(f"Endpoint {cfg.ai_search_endpoint} is {st.state.value}: {st.message}")
    return bool(st and st.state == EndpointStatusState.ONLINE)


wait_for(f"Endpoint {cfg.ai_search_endpoint}", endpoint_online, timeout_min=45)

try:
    w.vector_search_indexes.get_index(cfg.kb_index)
    print("Index exists; syncing.")
    w.vector_search_indexes.sync_index(cfg.kb_index)
except Exception:
    print(f"Creating index {cfg.kb_index}...")
    w.vector_search_indexes.create_index(
        name=cfg.kb_index,
        endpoint_name=cfg.ai_search_endpoint,
        primary_key="article_id",
        index_type=VectorIndexType.DELTA_SYNC,
        delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(
            source_table=f"{cfg.full_schema}.kb_articles",
            pipeline_type=PipelineType.TRIGGERED,
            embedding_source_columns=[
                EmbeddingSourceColumn(name="search_text", embedding_model_endpoint_name=cfg.embedding_model)
            ],
        ),
    )


def index_ready():
    st = w.vector_search_indexes.get_index(cfg.kb_index).status
    print(time.strftime("%H:%M:%S"), "index:", "ready" if st and st.ready else "not ready", "-", (st.message if st else "") or "")
    return bool(st and st.ready)


wait_for(f"Index {cfg.kb_index}", index_ready, timeout_min=30)
print("Index ready:", cfg.kb_index)

# COMMAND ----------

# MAGIC %md
# MAGIC **If the endpoint or index is slow or fails**, run the next cell. It shows the full status of both. Also check
# MAGIC **Compute → AI Search** (the endpoint) and the index page in Catalog Explorer (sync pipeline and errors).
# MAGIC If the endpoint stays `PROVISIONING` for more than about 45 minutes, delete it and create it again, or set another
# MAGIC `ai_search_endpoint` name in `01`.

# COMMAND ----------

ep = w.vector_search_endpoints.get_endpoint(cfg.ai_search_endpoint)
print("endpoint:", ep.name, ep.endpoint_type, ep.endpoint_status, "| indexes:", ep.num_indexes, "| created:", ep.creation_timestamp)
try:
    idx = w.vector_search_indexes.get_index(cfg.kb_index)
    print("index:", idx.name, idx.status)
except Exception as e:
    print("index not created yet:", str(e)[:200])

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE FUNCTION {T}.search_kb(query STRING COMMENT 'What to look for, in plain language')
RETURNS TABLE (article_id STRING, title STRING, body STRING)
COMMENT 'Searches the customer service knowledge base and returns the three most relevant articles. Use it to ground every answer about processes, deadlines and coverage rules.'
RETURN
  SELECT article_id, title, body
  FROM vector_search(index => '{cfg.kb_index}', query_text => search_kb.query, num_results => 3)
""")
display(spark.sql(f"SELECT * FROM {T}.search_kb('my kitchen is flooded, what now?')"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## The same functions as an MCP server
# MAGIC
# MAGIC This is how the service assist agent sees the tools: a list of MCP tools with names, descriptions and
# MAGIC argument schemas taken from the function definitions above. Calls run with the caller's Unity Catalog permissions.

# COMMAND ----------

from concurrent.futures import ThreadPoolExecutor

from databricks.sdk import WorkspaceClient
from databricks_mcp import DatabricksMCPClient

w = WorkspaceClient()


def in_thread(fn, *args):
    # The client's sync methods call asyncio.run(), which can't run inside the notebook's event loop.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args).result()


mcp_url = f"{w.config.host.rstrip('/')}/api/2.0/mcp/functions/{cfg.catalog}/{cfg.tools_schema}"
mcp = DatabricksMCPClient(server_url=mcp_url, workspace_client=w)
def root_message(e):
    # MCP client errors arrive wrapped in exception groups; the first leaf has the server's message.
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return str(e)


try:
    tools = in_thread(mcp.list_tools)
except BaseException as e:
    msg = root_message(e)
    if "not enabled" in msg.lower():
        raise RuntimeError(
            "Managed MCP servers are not enabled in this workspace. A workspace admin turns on the managed MCP "
            "servers preview in Settings > Previews (checklist item W8 in 00_prerequisites); then re-run this cell. "
            f"Server message: {msg}") from None
    raise RuntimeError(f"MCP call failed: {msg}") from None
print(mcp_url)
for t in tools:
    print(f"- {t.name}: {(t.description or '')[:90]}")

# COMMAND ----------

import time

name = next(t.name for t in tools if t.name.endswith("search_kb"))
# The first call can be slow while the compute that runs the function starts; the client has no timeout setting,
# so retry on a timeout.
for attempt in range(1, 4):
    try:
        result = in_thread(mcp.call_tool, name, {"query": "how long does a claim decision take"})
        break
    except BaseException as e:
        msg = root_message(e)
        if "timeout" not in msg.lower() and "timed out" not in msg.lower() and "ReadTimeout" not in repr(e):
            raise RuntimeError(f"MCP call failed: {msg}") from None
        print(f"attempt {attempt}: timed out, retrying in 15 s")
        time.sleep(15)
else:
    raise RuntimeError("MCP tool call timed out 3 times; check that the SQL warehouse and the AI Search index are up.")
print("".join(getattr(c, "text", "") for c in result.content)[:1500])

# COMMAND ----------

# MAGIC %md
# MAGIC ## Who can use the tools
# MAGIC
# MAGIC Agents get `EXECUTE` on these functions in `91_grant_agent_access`. A caller without the grant gets a permission
# MAGIC error from the MCP server, the same as from SQL.

# COMMAND ----------

display(spark.sql(f"SHOW FUNCTIONS IN {T}"))
