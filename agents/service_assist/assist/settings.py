"""Runtime settings, read from environment variables (app.yaml) or built from notebook `cfg`."""

import os
from dataclasses import dataclass


def _model_service_from_binding() -> str | None:
    # Set by `agentbricks deploy` from [experimental.model_services.agent] in agent.toml.
    try:
        from databricks_agentkit.runtime.tool_manifest import resolve_model_service

        return resolve_model_service()
    except Exception:
        return None


@dataclass
class Settings:
    catalog: str
    tools_schema: str
    model_service: str
    org_name: str = "Aurora Mutual"
    max_turns: int = 10

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            catalog=os.environ["STARTER_CATALOG"],
            tools_schema=os.environ["STARTER_TOOLS_SCHEMA"],
            model_service=_model_service_from_binding() or os.environ["STARTER_MODEL_SERVICE"],
            org_name=os.getenv("STARTER_ORG_NAME", "Aurora Mutual"),
        )

    @classmethod
    def from_cfg(cls, cfg, **overrides) -> "Settings":
        values = dict(catalog=cfg.catalog, tools_schema=cfg.tools_schema, model_service=cfg.assist_service,
                      org_name=cfg.org_name)
        values.update(overrides)
        return cls(**values)
