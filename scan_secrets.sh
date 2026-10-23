#!/bin/bash
# Scan full git history for hardcoded secrets
cd /c/Users/dylan/OneDrive/Desktop/coding-agent

echo "=== Scanning git history for hardcoded secrets ==="
MATCHES=$(git grep -n -I -E "(api_key|apikey|secret|token|password|passwd|pwd|private_key|BEGIN [A-Z]+ PRIVATE KEY)[\"']?\s*[:=]\s*[\"'][^\"']{8,}" $(git rev-list --all) -- "*.py" "*.json" "*.yml" "*.yaml" "*.ts" "*.js" "*.env*" 2>/dev/null | grep -v -i -E "placeholder|example|your-key|your_api|INSERT|CHANGEME|TODO|xxxx|\.\.\.|env\.get|os\.environ|getenv|process\.env|sk-test-key|test-api-key|TEST_API_KEY")

if [ -z "$MATCHES" ]; then
  echo "CLEAN: No hardcoded secrets found in git history"
else
  echo "FOUND POTENTIAL SECRETS:"
  echo "$MATCHES"
fi

echo ""
echo "=== Checking for .env / credential files ever committed ==="
git log --all --full-history -- "*.env" "*.env.*" "secrets.json" "credentials*" "*.pem" "*.key" --oneline 2>/dev/null | head -10
echo "(empty = never committed)"
