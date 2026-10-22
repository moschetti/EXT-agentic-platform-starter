"""HTTP entry point for the document intake agent.

AGENT_SERVER_MODE selects the server (set by scripts/render_agent_config.py from `enable_lakebase`):

- ``agentbricks``: the managed Agent Runtime server (DurableAgentServer). Synchronous, streaming and
  background invocations, status and event replay, idempotent invocation ids; state kept in Lakebase.
- ``custom``: a plain FastAPI app with the same request and response shape, for workspaces without
  Lakebase. No background mode or recovery.

Request body, both modes (POST /api/invocations):

    {"id": "<uuid>", "input": {"document_id": "DOC-0001"}}     # or {"text": "..."}
"""

import os
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)

from intake.graph import DocumentIntake  # noqa: E402
from intake.settings import Settings  # noqa: E402

MODE = os.getenv("AGENT_SERVER_MODE", "agentbricks")
_agent: DocumentIntake | None = None


def _get_agent() -> DocumentIntake:
    global _agent
    if _agent is None:
        _agent = DocumentIntake(Settings.from_env())
    return _agent


def _parse(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value:
        # Chat-style input: treat the last message as the document text.
        last = value[-1]
        return {"text": last.get("content") if isinstance(last, dict) else str(last)}
    raise ValueError("input must be an object with 'document_id' or 'text'")


async def handle(value: Any) -> dict[str, Any]:
    payload = _parse(value)
    return await _get_agent().arun(document_id=payload.get("document_id"), text=payload.get("text"))


if MODE == "agentbricks":
    from databricks_agentkit import DurableAgentServer, InvocationContext
    from databricks_agentkit.langgraph import configure_tracing

    configure_tracing()
    app = DurableAgentServer()

    async def invoke(value: Any, context: InvocationContext) -> dict:
        await context.emit({"type": "status", "message": "processing document"})
        result = await handle(value)
        await context.emit({"type": "status", "message": f"route: {result['route']}"})
        return {"output": result, "status": "completed"}

    app.invoke(invoke)
else:
    import mlflow
    from fastapi import FastAPI
    from pydantic import BaseModel

    mlflow.set_tracking_uri("databricks")
    if os.getenv("MLFLOW_EXPERIMENT_NAME"):
        mlflow.set_experiment(os.environ["MLFLOW_EXPERIMENT_NAME"])
    mlflow.langchain.autolog()

    class InvocationRequest(BaseModel):
        id: str | None = None
        input: Any

    app = FastAPI(title="Document intake agent")

    @app.post("/api/invocations")
    async def invocations(body: InvocationRequest) -> dict[str, Any]:
        return {"id": body.id, "output": await handle(body.input), "status": "completed"}


def main() -> None:
    uvicorn.run("runtime.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
