#!/usr/bin/env bash
set -euo pipefail

# Ensure we are inside the git repository
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ]; then
  if [ -d "skin_care/.git" ]; then
    cd skin_care
    REPO_ROOT="$(pwd)"
  else
    echo "Error: Not inside the skin_care git repository." >&2
    exit 1
  fi
fi

cd "$REPO_ROOT"

# Remote URL defaults to the exact repository with the trailing hyphen
REMOTE_URL="${1:-git@github.com:qut2006sib-hash/skin_care-.git}"

echo "Configuring remote origin: $REMOTE_URL"
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

echo "=================================================="
echo "Pushing baseline and feature branches to skin_care-"
echo "=================================================="

echo "-> Pushing 'main' baseline..."
git push -u origin main

for branch in \
  feat/pr1-postgres-schema-and-seed \
  feat/pr2-inventory-and-order-service \
  feat/pr3-drf-api-and-nextjs-rtl-pwa \
  feat/pr4-concurrency-and-acceptance-tests; do
  echo "-> Pushing feature branch: $branch..."
  git push -u origin "$branch"
done

echo ""
echo "All branches pushed successfully to $REMOTE_URL!"
echo "Open your Pull Requests on GitHub in sequence: PR1 -> PR2 -> PR3 -> PR4."
