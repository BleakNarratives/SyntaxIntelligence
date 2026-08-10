#!/usr/bin/env bash
# =====================================================================
# SyntaxIntelligence smoke suite — discover & run all test_*.py files
# One command: ./run-smoke.sh
# =====================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$SCRIPT_DIR"

# Add parent dir to PYTHONPATH so `from SyntaxIntelligence import ...` works
export PYTHONPATH="$PARENT_DIR:${PYTHONPATH:-}"

echo "===== SyntaxIntelligence smoke suite ====="

# Count test files
TEST_COUNT=$(find . -maxdepth 1 -name 'test_*.py' | wc -l)
echo "[*] Found $TEST_COUNT test files"

FAILED=0

for test_file in test_*.py; do
    if [ ! -f "$test_file" ]; then
        echo "[-] No test files found"
        exit 1
    fi
    module_name="${test_file%.py}"
    echo
    echo "--- $module_name ---"
    if python3 -m unittest "$module_name" -v 2>&1; then
        echo "  ✅ $module_name PASSED"
    else
        echo "  ❌ $module_name FAILED"
        FAILED=$((FAILED + 1))
    fi
done

echo
echo "===== Results ====="
echo "[*] $TEST_COUNT test files, $FAILED failures"

if [ "$FAILED" -ne 0 ]; then
    echo "[!] $FAILED test file(s) failed"
    exit 1
else
    echo "[+] All tests passed"
fi
