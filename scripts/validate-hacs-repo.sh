#!/usr/bin/env bash
# Validate HACS repository layout (run from repo root).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAIL=0

fail() { echo "FAIL: $1" >&2; FAIL=1; }
ok() { echo "OK: $1"; }

echo "=== ZigbeeLens HACS repo validation ==="

for f in hacs.json README.md LICENSE custom_components/zigbeelens/manifest.json custom_components/zigbeelens/__init__.py; do
  if [[ -f "${ROOT}/${f}" ]]; then ok "$f"; else fail "missing $f"; fi
done

python3 - <<PY
import json, sys
from pathlib import Path

hacs = json.loads(Path("${ROOT}/hacs.json").read_text())
if hacs.get("content_in_root") is not False:
    sys.exit("hacs.json content_in_root must be false for custom_components layout")
if hacs.get("filename") != "zigbeelens":
    sys.exit("hacs.json filename must be zigbeelens")
manifest = json.loads(Path("${ROOT}/custom_components/zigbeelens/manifest.json").read_text())
if manifest.get("domain") != "zigbeelens":
    sys.exit("manifest domain must be zigbeelens")
if not manifest.get("version"):
    sys.exit("manifest version required")
print("OK: hacs.json and manifest.json parse")
PY

# Syntax check Python modules
while IFS= read -r py; do
  python3 -m py_compile "$py" && ok "syntax $(basename "$py")"
done < <(find "${ROOT}/custom_components/zigbeelens" -name '*.py' -type f)

# Obvious secrets scan
if grep -RniE 'password\s*=\s*["\x27][^"\x27]{8,}|api_key\s*=\s*["\x27]|hunter2|secret-pass' "${ROOT}/custom_components" 2>/dev/null; then
  fail "possible secret in custom_components"
else
  ok "no obvious secrets in custom_components"
fi

if [[ "${FAIL}" -ne 0 ]]; then exit 1; fi
echo "HACS repo validation passed."
