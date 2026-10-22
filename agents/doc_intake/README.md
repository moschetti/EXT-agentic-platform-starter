# Document intake agent

LangGraph agent that classifies an incoming document, extracts its fields with quoted evidence, validates them with
Unity Catalog functions and routes it to `auto`, `auto_with_audit` or `human_review`. See notebook `06` for a
step-by-step walkthrough and `docs/architecture.md` for the design.

| Path | What |
|---|---|
| `intake/doc_types.py` | Document types, fields, required fields, checks, routing rule (shared with notebook `04`) |
| `intake/graph.py` | The flow: load → classify → extract → validate → decide → record |
| `intake/settings.py`, `intake/sql.py` | Settings from env; SQL calls to the tool functions |
| `runtime/main.py` | HTTP server: `DurableAgentServer` or FastAPI (`AGENT_SERVER_MODE`) |
| `agent.toml.tmpl`, `app.yaml.tmpl` | Rendered by `scripts/render_agent_config.py` |

Request: `POST /api/invocations` with `{"id": "<uuid>", "input": {"document_id": "DOC-0001"}}` or `{"input": {"text": "..."}}`.

Local run (after rendering the config): `agentbricks dev`, with `DATABRICKS_CONFIG_PROFILE` in `.env`.
Tests: `uv run pytest`.
