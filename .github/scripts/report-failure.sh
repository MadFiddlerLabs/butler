#!/bin/bash
# Opens one issue per failing scheduled workflow, or comments on the open one, so a quiet
# weekly check can't fail unnoticed. Runs in GitHub Actions with GH_TOKEN set.
set -euo pipefail
title="Scheduled check failed: $GITHUB_WORKFLOW"
run_url="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
existing="$(gh issue list --repo "$GITHUB_REPOSITORY" --state open --search "in:title \"$title\"" \
  --json number --jq '.[0].number // empty')"
if [ -n "$existing" ]; then
  gh issue comment "$existing" --repo "$GITHUB_REPOSITORY" --body "Failed again: $run_url"
else
  gh issue create --repo "$GITHUB_REPOSITORY" --title "$title" \
    --body "The scheduled run failed: $run_url"
fi
