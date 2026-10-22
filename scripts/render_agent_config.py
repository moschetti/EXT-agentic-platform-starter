#!/usr/bin/env python3
"""Render agent.toml, app.yaml and .agentbricks/project.toml for both agents from the configuration.

Used by notebooks/90_deploy_agents.py, which passes the resolved `cfg` of notebooks/01_configuration.py.

For local development with `agentbricks dev`: save `vars(cfg)` from a notebook as JSON, then run
`scripts/render_agent_config.py <config.json>` to render the files into agents/. Prints `<agent_dir> <agent_name>`
per agent.
"""

import json
import sys
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent


def agents(cfg: dict) -> dict[str, dict]:
    return {
        "doc_intake": {"framework": "langgraph", "agent_name": cfg["doc_agent_name"],
                       "model_service": cfg["doc_service"], "experiment": cfg["doc_experiment"]},
        "service_assist": {"framework": "openai", "agent_name": cfg["assist_agent_name"],
                           "model_service": cfg["assist_service"], "experiment": cfg["assist_experiment"]},
    }


def render(cfg: dict, agents_root: Path) -> list[tuple[str, str]]:
    """Write the rendered files into each agent folder under `agents_root`; return (folder, agent name) pairs."""
    rendered = []
    for folder, a in agents(cfg).items():
        d = agents_root / folder
        values = {**{k: str(v) for k, v in cfg.items()}, **a}
        for name in ("agent.toml", "app.yaml"):
            (d / name).write_text(Template((d / f"{name}.tmpl").read_text()).substitute(values))
        template = ("custom-agent-" if cfg["server_mode"] == "custom" else "agent-") + a["framework"]
        (d / ".agentbricks").mkdir(exist_ok=True)
        (d / ".agentbricks" / "project.toml").write_text(
            f'schema_version = 1\nframework = "{a["framework"]}"\ntemplate = "{template}"\n')
        rendered.append((folder, a["agent_name"]))
    return rendered


if __name__ == "__main__":
    for folder, name in render(json.loads(Path(sys.argv[1]).read_text()), ROOT / "agents"):
        print(folder, name)
