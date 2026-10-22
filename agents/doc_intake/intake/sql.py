"""Run SQL on a serverless warehouse with named parameters.

The agent calls Unity Catalog functions this way, so every check is a governed, audited function call
and runs with the identity of the caller (the app's service principal when deployed).
"""

import time
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem, StatementState


def run_sql(w: WorkspaceClient, warehouse_id: str, statement: str, params: dict[str, tuple[Any, str]] | None = None,
            timeout_s: int = 120) -> list[dict[str, Any]]:
    """Execute `statement` and return rows as dicts of strings.

    `params` maps a parameter name to (value, SQL type), e.g. {"policy_id": ("P000123", "STRING")}.
    A value of None is passed as a typed NULL.
    """
    parameters = [
        StatementParameterListItem(name=name, value=None if value is None else str(value), type=sql_type)
        for name, (value, sql_type) in (params or {}).items()
    ]
    r = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id, statement=statement, parameters=parameters, wait_timeout="30s")
    deadline = time.time() + timeout_s
    while r.status and r.status.state in (StatementState.PENDING, StatementState.RUNNING):
        if time.time() > deadline:
            raise TimeoutError(f"SQL statement did not finish in {timeout_s}s")
        time.sleep(1)
        r = w.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"SQL failed: {r.status.error.message if r.status.error else r.status.state}")
    if not r.manifest or not r.manifest.schema or not r.manifest.schema.columns:
        return []
    columns = [c.name for c in r.manifest.schema.columns]
    return [dict(zip(columns, row)) for row in (r.result.data_array or [])] if r.result else []


def as_bool(value: Any) -> bool:
    return str(value).lower() == "true"
