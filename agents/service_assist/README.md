# Service assist agent

OpenAI Agents SDK agent that drafts replies for a customer service rep. Tools come from the Unity Catalog functions MCP server for
the tools schema; every model call goes through the guarded Unity Gateway model service. See notebook `07`.

| Path | What |
|---|---|
| `assist/agent.py` | Instructions, MCP tools, model through Unity Gateway, block detection |
| `assist/settings.py` | Settings from env |
| `runtime/main.py` | HTTP server: `DurableAgentServer` or FastAPI (`AGENT_SERVER_MODE`); rep console at `/` |
| `runtime/console.html` | Single-page rep console |
| `agent.toml.tmpl`, `app.yaml.tmpl` | Rendered by `scripts/render_agent_config.py` |

Request: `POST /api/invocations` with
`{"id": "<uuid>", "input": {"customer_id": "C00001", "channel": "chat", "message": "..."}}`.

Local run (after rendering the config): `agentbricks dev`, with `DATABRICKS_CONFIG_PROFILE` in `.env`.
Tests: `uv run pytest`.
