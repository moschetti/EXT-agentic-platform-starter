"""Run the Databricks and Agent Bricks CLIs from a serverless notebook.

Used by notebooks/90_deploy_agents.py and notebooks/92_teardown.py. On serverless notebook compute the
`databricks` command is a stand-in that only works in the web terminal, and agentbricks calls it for app
operations, so this downloads the real CLI release and runs both CLIs with the notebook's credentials.
"""

import os
import platform
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


def cli_env(host: str, token: str) -> dict[str, str]:
    """Environment for the CLIs: the notebook's host and token, and this Python's scripts first on PATH.

    The CLIs use the Databricks SDK's default authentication when no profile is given. The token is short-lived;
    build a new environment if a long run hits expiry.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("DATABRICKS_")}
    env |= {
        "DATABRICKS_HOST": host,
        "DATABRICKS_TOKEN": token,
        "DATABRICKS_AUTH_TYPE": "pat",
        "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
    }
    return env


# The Databricks CLI release the starter was tested with. Pinned so a new release cannot change the deploy.
CLI_VERSION = "1.21.0"


def install_databricks_cli(env: dict[str, str], version: str = CLI_VERSION) -> Path:
    """Download a Databricks CLI release for this machine and put it first on env's PATH."""
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}[platform.machine().lower()]
    name = f"databricks_cli_{version}_linux_{arch}.zip"
    url = f"https://github.com/databricks/cli/releases/download/v{version}/{name}"
    dest = Path(tempfile.mkdtemp(prefix="databricks-cli-"))
    archive = dest / name
    urllib.request.urlretrieve(url, archive)
    with zipfile.ZipFile(archive) as z:
        z.extract("databricks", dest)
    binary = dest / "databricks"
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    env["PATH"] = f"{dest}:{env['PATH']}"
    print("Databricks CLI", version, "at", binary)
    return binary


def run(cmd: list[str], env: dict[str, str], cwd=None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, print the tail of its output, and raise on failure if `check`."""
    print("$", " ".join(cmd))
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    print(p.stdout[-6000:])
    if p.returncode != 0:
        print(p.stderr[-6000:])
        if check:
            raise RuntimeError(f"{cmd[0]} exited with {p.returncode}")
    return p
