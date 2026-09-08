#!/usr/bin/env bash
#
# Local CI pipeline. Mirrors .github/workflows/ci.yml so a failure can be
# reproduced before pushing. Any failing stage aborts the run.
set -euo pipefail

export PYTHONPATH="${PYTHONPATH:-.}"
# Force UTF-8 so output is identical on consoles that default to a legacy
# codepage (Windows cp1252).
export PYTHONIOENCODING="utf-8"
# Reason offline unless the caller overrides it, so a local run reproduces CI
# rather than silently spending money against a configured provider.
export LLM_PROVIDER="${LLM_PROVIDER:-mock}"

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python

echo "=============================================="
echo " CONTINUOUS INTEGRATION & EVAL GATE"
echo "=============================================="

echo ""
echo "Stage 1/5: Unit, agent, contract, safety, and API tests"
"$PYTHON" -m pytest tests/ -q

echo ""
echo "Stage 2/5: Evaluation gate (trajectory + safety invariants)"
"$PYTHON" -m mini_platform.evals.eval_runner

echo ""
echo "Stage 3/5: End-to-end demo sanity check"
"$PYTHON" -m mini_platform.demo > /dev/null

echo ""
echo "Stage 4/5: CLI smoke checks"
"$PYTHON" -m mini_platform.cli tools > /dev/null
"$PYTHON" -m mini_platform.cli blast-radius --service payment-service > /dev/null
"$PYTHON" -m mini_platform.cli rag --query "payment memory leak restart" > /dev/null

echo ""
echo "Stage 5/5: Artifact version manifest check"
"$PYTHON" -m tools.check_versions > /dev/null

echo ""
echo "ALL CI STAGES AND EVALUATION GATES PASSED"
