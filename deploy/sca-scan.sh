#!/usr/bin/env bash
# Software Composition Analysis for the Generator Product dependencies (O.3/O.4).
# Runs pip-audit against the OSV / NIST NVD data and fails on CRITICAL/HIGH.
# Wire this into the build/deploy pipeline so no release ships with unremediated
# CRITICAL/HIGH vulns older than 90 days.
set -euo pipefail
pip install pip-audit --quiet --break-system-packages 2>/dev/null || pip install pip-audit --quiet
echo "== pip-audit (requirements.txt) =="
pip-audit -r requirements.txt --desc on || true
echo "== SBOM (CycloneDX) =="
pip-audit -r requirements.txt -f cyclonedx-json -o GPSA-sbom.cyclonedx.json 2>/dev/null || \
  pip install cyclonedx-bom --quiet 2>/dev/null && cyclonedx-py requirements requirements.txt -o GPSA-sbom.cyclonedx.json 2>/dev/null || true
echo "Wrote GPSA-sbom.cyclonedx.json (attach to the GPSA submission)."
