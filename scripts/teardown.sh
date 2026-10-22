#!/usr/bin/env bash
# Remove the starter from a terminal: runs the agentic-starter-teardown job (notebook 92: agent apps, model
# services, AI Search index, schemas), then removes the bundle's jobs and files.
#
#   scripts/teardown.sh <target> <profile> <catalog.schema>
#
# <catalog.schema> must match the environment's catalog and schema; it is the confirmation notebook 92 asks for.
set -euo pipefail

TARGET="${1:?usage: teardown.sh <target> <profile> <catalog.schema>}"
PROFILE="${2:?usage: teardown.sh <target> <profile> <catalog.schema>}"
SCHEMA="${3:?usage: teardown.sh <target> <profile> <catalog.schema>}"
cd "$(dirname "$0")/.."

databricks bundle run teardown -t "$TARGET" -p "$PROFILE" --params "confirm=$SCHEMA"
databricks bundle destroy -t "$TARGET" -p "$PROFILE"
