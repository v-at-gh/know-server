#!/usr/bin/env bash
set -e

TEST_PATH="${1:-tests/}"
EXTRA_ARGS="${@:2}"

echo "=== Verifying sabotage branches ==="
CURRENT_BRANCH=$(git branch --show-current)

BRANCHES=$(git branch -a | grep "sabotage/" | sed 's|remotes/origin/||' | sort -u)

if [ -z "$BRANCHES" ]; then
    echo "No sabotage branches found matching 'sabotage/*'"
    exit 1
fi

FAILED=0
PASSED=0

for branch in $BRANCHES; do
    branch=$(echo "$branch" | tr -d ' *')
    echo "--------------------------------------------------"
    echo "Checking branch: $branch"
    git checkout "$branch" --quiet || { echo "Failed to checkout $branch"; continue; }

    set +e
    .venv/bin/pytest "$TEST_PATH" $EXTRA_ARGS
    EXIT_CODE=$?
    set -e

    if [ $EXIT_CODE -ne 0 ]; then
        echo "SUCCESS: Test failed on sabotage branch $branch (as expected)"
        FAILED=$((FAILED + 1))
    else
        echo "FAILURE: Test PASSED on sabotage branch $branch (expected failure!)"
        PASSED=$((PASSED + 1))
    fi
done

git checkout "$CURRENT_BRANCH" --quiet
echo "--------------------------------------------------"
echo "Summary: $FAILED sabotage branches failed tests (expected), $PASSED passed tests (unexpected)."
if [ $PASSED -gt 0 ]; then
    exit 1
fi
exit 0
