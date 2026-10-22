"""HTTP entry point for the service assist agent.

AGENT_SERVER_MODE selects the server (see agents/doc_intake/runtime/main.py for the two modes).

POST /api/invocations, both modes:

    {"id": "<uuid>", "input": {"message": "...", "customer_id": "C00001", "channel": "chat"}}

`input` may also be a list of chat messages; the last one is the customer message and the rest are history.
GET / serves a small rep console for trying the agent in a browser.
"""

import os
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi.responses import HTMLResponse

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)

from assist.agent import ServiceAssist  # noqa: E402
from assist.settings import Settings  # noqa: E402

MODE = os.getenv("AGENT_SERVER_MODE", "agentbricks")
CONSOLE = (Path(__file__).parent / "console.html").read_text()
_agent: ServiceAssist | None = None


def _get_agent() -> ServiceAssist:
    global _agent
    if _agent is None:
        _agent = ServiceAssist(Settings.from_env())
    return _agent


def _parse(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value:
        *history, last = value
        return {"message": last.get("content", ""), "history": history}
    raise ValueError("input must be an object with 'message' or a list of chat messages")


async def handle(value: Any) -> dict[str, Any]:
    p = _parse(value)
    return await _get_agent().answer(p["message"], customer_id=p.get("customer_id"),
                                     channel=p.get("channel", "chat"), history=p.get("history"))


if MODE == "agentbricks":
    from databricks_agentkit import DurableAgentServer, InvocationContext
    from databricks_agentkit.openai import configure_tracing

    configure_tracing()
    app = DurableAgentServer()

    async def invoke(value: Any, context: InvocationContext) -> dict:
        result = await handle(value)
        return {"output": result, "status": "completed"}

    app.invoke(invoke)
else:
    import mlflow
    from fastapi import FastAPI
    from pydantic import BaseModel

    mlflow.set_tracking_uri("databricks")
    if os.getenv("MLFLOW_EXPERIMENT_NAME"):
        mlflow.set_experiment(os.environ["MLFLOW_EXPERIMENT_NAME"])
    mlflow.openai.autolog()

    class InvocationRequest(BaseModel):
        id: str | None = None
        input: Any

    app = FastAPI(title="Service assist agent")

    @app.post("/api/invocations")
    async def invocations(body: InvocationRequest) -> dict[str, Any]:
        return {"id": body.id, "output": await handle(body.input), "status": "completed"}


@app.get("/", response_class=HTMLResponse)
async def console() -> str:
    return CONSOLE


def main() -> None:
    uvicorn.run("runtime.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
