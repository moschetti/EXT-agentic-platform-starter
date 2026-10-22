# Agentic platform starter for Databricks

A deployable starting point for running AI agents in production on Databricks. It deploys two working agents on
one shared, governed platform layer, with synthetic data, so you can run the whole path in your own workspace and
then replace the parts you need. Each platform layer has its own notebook, so you can see what each step does and
change it.

## What it shows

| Agent | What it does | Pattern | Framework |
|---|---|---|---|
| **Document intake** | Classifies incoming documents, extracts fields with evidence, validates them with governed tools, and routes each case to automatic processing, sampled audit or human review | Fixed, auditable flow; the LLM never decides the route | LangGraph |
| **Service assist** | Drafts replies for a customer service rep from the customer's records and the knowledge base, behind input and output guardrails | Tool-calling agent over MCP; a person sends the reply | OpenAI Agents SDK |

Both agents are built on **Agent Bricks**, the Databricks agent developer platform, and share one governed
platform layer:

- **Agent Bricks**: both agents run on **Agent Runtime** (Databricks Apps) with the AgentKit agent server
  `DurableAgentServer`, which gives them background runs, idempotent invocations and crash recovery, with run state
  in a managed Lakebase Runtime Store. They are deployed with the **Agent Bricks CLI** from a declarative
  `agent.toml`
- **Unity Gateway**: every LLM call goes through a model service with access control, rate limits, inference
  logging, fallback and **guardrails** (built-in and custom service policies)
- **Tools as Unity Catalog functions**, called through MCP or as SQL, plus a **Databricks AI Search** index
- **MLflow 3**: tracing of every step, evaluation datasets and scorers, production monitoring, review queues
- **Unity Catalog** governance: grants, column masks, lineage, audit and system tables
- **AI Functions** in SQL for the same document process without an agent, for comparison

All data is synthetic. Organisation names, catalogs, models and thresholds are configuration.

Some features used here are in Beta or Preview. [docs/feature-status.md](docs/feature-status.md) lists the release
status of each, with a link to the Databricks documentation. [docs/architecture.md](docs/architecture.md) has the
component diagram, what Agent Bricks provides and how the starter uses it, the request flows, and which parts
are Databricks features and which are code in this repository. [docs/troubleshooting.md](docs/troubleshooting.md) lists problems you may meet and what to do
about them.

## Quick start

Deploys everything into one workspace from the browser, with no local tools. Plan for about an hour for the first
deployment, most of it provisioning the AI Search endpoint and the two agent apps.

**1. Check the prerequisites.** You need a Unity Catalog workspace with serverless compute and Databricks Apps,
in a region that supports Unity Gateway and Foundation Model APIs, and a catalog in which you can create schemas.
A workspace admin turns on three previews on the workspace **Previews** page (your user name, top right →
**Previews**):

- **Unity Ai Gateway Service Policies** (the guardrails, Beta)
- **Managed MCP Servers** (the service assist agent's tools)
- **MLflow Review Queues** (Beta)

`notebooks/00_prerequisites` has the full checklist and read-only checks you can run first.

**2. Add the repository as a Git folder.** In the workspace: **Workspace → Create → Git folder**, and paste the
repository URL.

**3. Set your environment.** Open `notebooks/01_configuration` and edit one of the two examples in `ENVIRONMENTS`:

| Environment (bundle target) | Use it when | Set at least |
|---|---|---|
| `demo` | Lakebase is available in your region (durable agent runtime) | `catalog` |
| `demo-legacy` | Lakebase is not available in your region (FastAPI runtime) | `catalog` |

Then:

1. **Set `catalog`.** Optional: `workspace_host` (a guard against running in the wrong workspace) and
   `create_catalog = True` if `02` should create the catalog.
2. **Choose the chat model by hand.** A model listed in `system.ai` is not necessarily served in your region, and
   a model that is not served fails only when it is called. Open **Serving** in the left menu, filter on foundation
   models, and pick an endpoint `databricks-X` whose state is **Ready**. Send it a message in the **Playground** to
   confirm it answers, then set `chat_model = "system.ai.databricks-X"` in your environment entry. The `demo`
   example pins `system.ai.databricks-claude-sonnet-5-5`; `demo-legacy` uses the default in `SHARED`,
   `system.ai.databricks-gpt-5-5`. Change either if your workspace does not serve it.
3. **Run the checks.** **Run all**, select your environment in the `environment` widget that appears below the
   notebook toolbar, and run all again. Every check at the end must pass, including `Test call to chat_model`. If
   that check fails, the next line lists the models that answer in your workspace; set `chat_model` to one of them
   and run again.

**4. Deploy the bundle.** Open `databricks.yml` in the Git folder, open the **Deployments** panel, select the
target you configured in step 3, and click **Deploy**. This creates four jobs and runs nothing.

**5. Run the deploy job.** In the same panel, or under **Jobs & Pipelines**, run **`agentic-starter-deploy`**. It
creates the data, tools, document pipeline, Unity Gateway model services and both agents, and grants the agents
access.

**6. Attach the guardrail policies.** In the Unity Gateway UI, attach the policies listed in
`notebooks/05_unity_gateway_and_guardrails`, section 4. Set `block-hallucination` and
`judge-no-unsupported-claims` to **Log** and the rest to **Enforce** (`05` explains why).

**7. Run the quality job.** Run **`agentic-starter-quality`**. It runs both agents over the test data and the
guardrail tests, evaluates them, sets up monitoring and review queues, and prints the governance views.

**8. Try it.** See [Try it](#try-it).

To see each step as it runs instead of using jobs, run the notebooks yourself from the Git folder on serverless:
`00` → `05`, attach the guardrail policies, `90_deploy_agents`, `91_grant_agent_access`, then `06` → `10`.

## Notebooks

| Notebook | What it does |
|---|---|
| `00_prerequisites` | What must be in place, who does it, and read-only checks |
| `01_configuration` | **Set your environment here.** Workspace, catalog, models, thresholds, runtime mode |
| `02_synthetic_data` | Schemas, volume, synthetic data, a column mask on PII |
| `03_tools_uc_functions_mcp` | Tool functions, AI Search index, and the functions as an MCP server |
| `04_ai_functions_document_pipeline` | Document processing in SQL with AI Functions, no agent; tables the agent writes |
| `05_unity_gateway_and_guardrails` | Model services, rate limits, inference logging, custom guardrail functions, attaching and testing policies (writes `guardrail_tests`) |
| `06_agent_document_intake` | Agent 1 step by step, prompt injection, all documents, comparison with `04` |
| `07_agent_service_assist` | Agent 2 on the service cases and the guardrail tests |
| `08_evaluation` | Evaluation datasets in Unity Catalog; code scorers and LLM judges |
| `09_monitoring_and_review` | Production monitoring scorers, review queues, labels back into evaluation |
| `10_governance` | Grants, inference logs, gateway usage, audit, lineage, decision → trace, cost |
| `90_deploy_agents` | Deploys both agents with the Agent Bricks CLI, from serverless (no terminal) |
| `91_grant_agent_access` | Least-privilege grants for the deployed agents' service principals |
| `92_teardown` | Removes the apps, model services, AI Search index and schemas, so the starter can be deployed again |

## Deploy with the bundle

The bundle (`databricks.yml`, `resources/jobs.yml`) is a Declarative Automation Bundle (formerly Databricks Asset
Bundle). It deploys the notebooks and four jobs. All jobs run on serverless. No job has a schedule or trigger, so
deploying the bundle runs nothing; each job runs only when you start it.

| Job | Tasks | When |
|---|---|---|
| `agentic-starter-deploy` | `00` → `01` → `02` → `03` → `04` and `05` → `90` deploy agents → `91` grants | Once, to deploy everything |
| `agentic-starter-agents` | `90` → `91` (parameter `agents`: `both`, `doc_intake` or `service_assist`) | After changing agent code |
| `agentic-starter-quality` | `06` and `07` → `08` → `09` → `10` | After the guardrail policies are attached |
| `agentic-starter-teardown` | `92` (parameter `confirm` = `<catalog>.<schema>`; without it the job stops before deleting anything) | To remove everything |

**From the workspace:** Quick start steps 4 to 7.

**From a terminal**, with only the Databricks CLI (the agents are still deployed by the job, on serverless):

```bash
scripts/deploy.sh <target> <profile>     # bundle deploy + agentic-starter-deploy
# attach the guardrail policies (05, section 4), then:
databricks bundle run quality -t <target> -p <profile>
```

**Targets and environments.** Each target in `databricks.yml` sets the `environment` job parameter, which selects
an entry in `ENVIRONMENTS` in `01_configuration`. `demo` is a development-mode target (resources are prefixed with
your user name); `demo-legacy` is a production-mode target deployed under `/Workspace/Shared/.bundle/`. To add an
environment, add an entry to `ENVIRONMENTS` and a target with the same name.

**Which files the jobs run.** Deployed from the workspace to the `demo` target, the jobs run the files in your Git
folder, so pulled changes take effect on the next run. Deployed from the CLI, or to the `demo-legacy` target,
the jobs run the copy the bundle uploaded, so deploy the bundle again after changing files.

## Try it

| Where | What to look at |
|---|---|
| `06_agent_document_intake` | One document step by step, a prompt injection that cannot change the route, all 40 documents compared with the correct answers and with the SQL pipeline, the review cases, a call to the deployed agent |
| Rep console | Open the app `agent-bricks-<prefix>-assist` (**Compute → Apps**) and send a customer message, for example "How do I report water damage?" with customer `C00001`; try the guardrail test messages from `05` too |
| `07_agent_service_assist` | The service cases, and the guardrail tests through the agent |
| MLflow experiments | `/Shared/<prefix>-agentic-starter/doc-intake` and `/service-assist`: a trace per request with every prompt, tool call and guardrail outcome, and the evaluation runs from `08` |
| `08_evaluation` | Scores per agent and the rows that failed a scorer |
| `09_monitoring_and_review` | Production scorers, review queues to label (experiment → **Reviews**), labels back into the evaluation datasets |
| `10_governance` | Grants, inference logs, gateway usage, audit, lineage, decision → trace, cost |

`<prefix>` is `name_prefix` in `01_configuration` (default `starter`).

### Call the agents from another application

Each agent is a Databricks App that serves `POST /api/invocations` and accepts Databricks OAuth tokens. With the
Databricks CLI:

```bash
TOKEN=$(databricks auth token -p <profile> | jq -r .access_token)
curl -s -X POST "<app-url>/api/invocations" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"id": "'"$(uuidgen)"'", "input": {"document_id": "DOC-0001"}}'
```

The app URL is on the app's page under **Compute → Apps**. Request bodies for each agent are in
[agents/doc_intake/README.md](agents/doc_intake/README.md) and
[agents/service_assist/README.md](agents/service_assist/README.md). The `id` is an idempotency key: sending the
same `id` again returns the existing result. With `enable_lakebase = True` the agent's result is in
`output.output`; with `False` it is in `output`.

## Adapt it

| To change | Edit |
|---|---|
| Environment, models, thresholds, names | `notebooks/01_configuration` |
| Data | `02_synthetic_data`, or point the tools and pipeline at your own tables |
| Document types, fields, checks and the routing rule | `agents/doc_intake/intake/doc_types.py` (shared by `04` and the agent) |
| Tools | The functions in `03`, and the grants in `91` |
| Guardrails | The custom policy functions in `05` and the attach plan in `05`, section 4; test cases in `agents/service_assist/assist/guardrails.py` |
| Agent prompts and logic | `agents/doc_intake/intake/graph.py`, `agents/service_assist/assist/agent.py` |
| Evaluation | Datasets, scorers and judge guidelines in `08` |

After changing agent code, run `agentic-starter-agents`, then `agentic-starter-quality`.

## Runtime mode

`enable_lakebase` in `01_configuration` selects how the agents are served:

| `enable_lakebase` | Server | What you get |
|---|---|---|
| `True` | `DurableAgentServer` on Agent Runtime | Synchronous, streaming and background invocations, status and event replay, idempotent invocation ids, recovery after restarts. Run state is kept in a Lakebase Runtime Store that `agentbricks deploy` provisions |
| `False` | Plain FastAPI app on Databricks Apps | Same request and response; no background mode or recovery. For regions where Lakebase is not available |

The agent code is the same in both modes.

## Remove

Run **`agentic-starter-teardown`** with `confirm` set to `<catalog>.<schema>`, or
`scripts/teardown.sh <target> <profile> <catalog>.<schema>` (which also destroys the bundle), or the notebook
`92_teardown` with `CONFIRM` set in code (or the `confirm` widget). It deletes:

- both agent apps, with `agentbricks deployments delete`, which also removes the Lakebase Runtime Store (deleting
  the apps in the UI would leave it)
- the model services, with their guardrail policies
- the AI Search index, and the endpoint if `delete_ai_search_endpoint` is `yes`
- both schemas, with all tables, PDFs, functions, inference tables and evaluation datasets

It keeps the MLflow experiments unless `delete_experiments` is `yes`: a deleted experiment's name cannot be used
again while it is in the trash, so a fresh deployment under the same `name_prefix` would fail. It also keeps the
catalog, the previews and the bundle (destroy the bundle from the **Deployments** panel or with
`databricks bundle destroy`).

To deploy again from scratch: run the teardown job, optionally change `name_prefix` in `01_configuration` for new
app names and empty experiments, then follow the Quick start from step 4. The previews stay on; the guardrail
policies need attaching again, because they are deleted with the model services.

## Layout

```
databricks.yml              bundle: one target per environment
resources/                  jobs: deploy, agents, quality, teardown
notebooks/                  one notebook per platform layer
agents/doc_intake/          intake/ (agent code, shared with notebooks), runtime/ (HTTP server), tests/
agents/service_assist/      assist/ (agent code), runtime/ (HTTP server + rep console), tests/
scripts/                    deploy.sh, teardown.sh, render_agent_config.py, notebook_cli.py, check_names.sh
docs/                       architecture, feature status, troubleshooting
LICENSE                     Apache License 2.0
```

The agents' `agent.toml` and `app.yaml` are rendered from the `*.tmpl` files by `scripts/render_agent_config.py`,
using the configuration from `01_configuration`. Edit the templates, not the rendered files.

## Blocked terms

To keep names out of commits (organisations, people, hostnames), list them in a local `.blocked-terms` file, one
per line; lines starting with `#` are ignored. The file is git-ignored. Run `scripts/check_names.sh` before
committing: it fails and prints the matches if any term appears in a tracked or staged file.

## License

Licensed under the [Apache License 2.0](LICENSE).
