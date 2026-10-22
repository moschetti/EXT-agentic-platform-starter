# Troubleshooting

Problems you may meet when deploying and running the starter, with their cause and what to do. Notebook numbers
refer to `notebooks/`; checklist items (W8, W9) refer to `00_prerequisites`.

## Setup and configuration

| Symptom | Cause | What to do |
|---|---|---|
| `01` warns about catalog privileges | No direct grant to you was found. Ownership is not a grant, and privileges that come through a group may not show | Fine if you own the catalog or get the privileges through a group; otherwise run the `GRANT` that `01` prints |
| `01`: chat model check fails, or calls return `Model ... is not available in your region` | The model is listed in `system.ai` but not served in the region (data residency) | Use `chat_model = "auto"` or pick a model from the list `01` prints after its test call |
| `07` or the agents: `NotFoundError: 404 ENDPOINT_NOT_FOUND` on calls to a model service | The model behind the services is not served in the workspace. `01` and `05` stop the deployment job when the model does not answer, so this appears when they were skipped or the model was changed afterwards | Check the `configuration` task output for `Test call to chat_model`. Set `chat_model` to a model that answers, re-run `05` (it recreates the services), re-attach the policies, then re-run `91` |
| Web terminal keeps saying "press enter to reconnect" | The web terminal is off by default (a workspace admin enables it) and has [compute requirements](https://learn.microsoft.com/en-us/azure/databricks/compute/web-terminal). A VPN or proxy can also block its websocket | The starter does not need the web terminal: deploy with the bundle job (`90_deploy_agents` runs on serverless), or run `scripts/deploy.sh` from a laptop |

## Data and tools

| Symptom | Cause | What to do |
|---|---|---|
| `03`: "Delta sync index creation is pending endpoint provisioning" for a long time | The first AI Search endpoint in a workspace takes 10–30 minutes. An index created meanwhile can stay pending after the endpoint is online | `03` waits up to 45 minutes and prints the state. If the endpoint is online and the index is still pending after about 10 minutes, delete the index and re-run the cell |
| `03`: "Databricks managed MCP servers are not enabled" | Preview off | Workspace **Previews** page → **Managed MCP Servers** (checklist W8) |
| The first MCP tool call is slow | The compute that runs the function is starting | `03` retries on a timeout, and the agent allows 120 seconds per MCP call |

## Unity Gateway and guardrails

| Symptom | Cause | What to do |
|---|---|---|
| No Policies tab on a model service | Preview off | Workspace **Previews** page → **Unity Ai Gateway Service Policies** (checklist W9). No account console needed |
| Policies are gone from a model service | `05` recreates a service when `chat_model` changes, which removes its policies and grants | Re-attach the policies (`05`, section 4) and re-run `91` |
| Calls still reach the old model right after a service is recreated | The gateway takes a few minutes to route to the new destination | Wait 5–15 minutes and re-run |
| Guardrail tests all show `allowed` | No policies attached yet | Attach them (`05`, section 4), wait a minute, re-run the tests |
| Correct, grounded agent replies are blocked on output (for example ones that quote "within 14 days") | Output-phase judges (hallucination, LLM-as-a-judge) see only the reply, not the tool results it was based on | Set those policies to **Log** mode on a tool-using agent; check groundedness in MLflow evaluation and monitoring, which see the whole trace |
| The ungrounded-answer test shows `refused` | The model declined by itself, before any guardrail | Expected: that test passes on `blocked` or `refused` |

## Agents and evaluation

| Symptom | Cause | What to do |
|---|---|---|
| `WARNING mlflow.tracking.context.registry: ... extraContext() is not a CommandContext accessor` | MLflow cannot read some notebook context on serverless | Harmless; `06`–`08` hide it |
| An API client gets no reply text from an agent | With `enable_lakebase = True` the agent's result is nested under `output` | Read `output.output` in that mode, and `output` with `enable_lakebase = False` |

## Deploying the agents

| Symptom | Cause | What to do |
|---|---|---|
| `90` fails while downloading the Databricks CLI | On serverless notebooks the built-in `databricks` command only works from the web terminal, so `90` and `92` download the CLI release from GitHub | Allow access to GitHub from serverless, or deploy from a laptop (`scripts/deploy.sh`) |
| A deployed agent answers with a permission error | The app's service principal is missing a grant | Re-run `91_grant_agent_access`; it prints each grant and any that failed |

## Removing and redeploying

| Symptom | Cause | What to do |
|---|---|---|
| `Cannot set a deleted experiment ... as the active experiment` | The experiment was deleted; its name stays reserved while it is in the trash | Restore it, remove it from the trash, or use a new `name_prefix`. `92_teardown` keeps experiments by default for this reason |
| A Lakebase database is left after deleting an agent app in the UI | Deleting the app directly skips Agent Bricks' cleanup of its runtime store | Use `92_teardown` (or `agentbricks deployments delete`) to remove agent apps |
