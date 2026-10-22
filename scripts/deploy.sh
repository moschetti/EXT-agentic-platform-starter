#!/usr/bin/env bash
# Deploy the starter from a terminal with the bundle: the same as deploying it from the Git folder's
# Deployments panel and running the agentic-starter-deploy job.
#
#   scripts/deploy.sh <target> <profile>        e.g. scripts/deploy.sh demo my-workspace
#
# Set your environment in notebooks/01_configuration.py first. Needs only the Databricks CLI: the agents are
# deployed by the job on serverless (notebook 90), not from this machine.
set -euo pipefail

TARGET="${1:?usage: deploy.sh <target> <profile>}"
PROFILE="${2:?usage: deploy.sh <target> <profile>}"
cd "$(dirname "$0")/.."

databricks bundle deploy -t "$TARGET" -p "$PROFILE"
databricks bundle run deploy -t "$TARGET" -p "$PROFILE"

echo
echo "Done. Next: attach the guardrail policies (notebook 05, section 4), then:"
echo "  databricks bundle run quality -t $TARGET -p $PROFILE"
