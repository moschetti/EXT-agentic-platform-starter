"""Runtime settings, read from environment variables.

The deployed app gets these from app.yaml (rendered by scripts/render_agent_config.py from notebook
01_configuration). Notebooks build Settings directly from `cfg`.
"""

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
    schema: str
    tools_schema: str
    model_service: str
    warehouse_id: str
    auto_threshold: float = 0.90
    audit_threshold: float = 0.75
    audit_sample_rate: float = 0.10
    write_results: bool = True

    @property
    def full_schema(self) -> str:
        return f"`{self.catalog}`.`{self.schema}`"

    @property
    def full_tools_schema(self) -> str:
        return f"`{self.catalog}`.`{self.tools_schema}`"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            catalog=os.environ["STARTER_CATALOG"],
            schema=os.environ["STARTER_SCHEMA"],
            tools_schema=os.environ["STARTER_TOOLS_SCHEMA"],
            model_service=_model_service_from_binding() or os.environ["STARTER_MODEL_SERVICE"],
            warehouse_id=os.environ["STARTER_WAREHOUSE_ID"],
            auto_threshold=float(os.getenv("STARTER_AUTO_THRESHOLD", "0.90")),
            audit_threshold=float(os.getenv("STARTER_AUDIT_THRESHOLD", "0.75")),
            audit_sample_rate=float(os.getenv("STARTER_AUDIT_SAMPLE_RATE", "0.10")),
            write_results=os.getenv("STARTER_WRITE_RESULTS", "true").lower() == "true",
        )

    @classmethod
    def from_cfg(cls, cfg, **overrides) -> "Settings":
        """Build settings from the `cfg` object of notebook 01_configuration."""
        values = dict(
            catalog=cfg.catalog,
            schema=cfg.schema,
            tools_schema=cfg.tools_schema,
            model_service=cfg.doc_service,
            warehouse_id=cfg.warehouse_id,
            auto_threshold=cfg.auto_threshold,
            audit_threshold=cfg.audit_threshold,
            audit_sample_rate=cfg.audit_sample_rate,
        )
        values.update(overrides)
        return cls(**values)
