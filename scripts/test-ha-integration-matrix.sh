#!/usr/bin/env bash
# Run the same integration suite against the exact reviewed Home Assistant lanes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -d "${ROOT}/apps/ha_integration" ]]; then
  HA_DIR="${ROOT}/apps/ha_integration"
elif [[ -d "${ROOT}/custom_components/zigbeelens" ]]; then
  # Generated HACS repository layout.
  HA_DIR="${ROOT}"
else
  echo "FAIL: unable to locate the Home Assistant integration test root" >&2
  exit 1
fi

MATRIX="${HA_DIR}/ha-test-matrix.json"
LANE="${1:-all}"
MATRIX_READER="${ZIGBEELENS_HA_MATRIX_READER:-python3}"
if ! command -v "${MATRIX_READER}" >/dev/null 2>&1; then
  echo "FAIL: matrix reader not found: ${MATRIX_READER}" >&2
  exit 1
fi

if [[ -f "${ROOT}/SOURCE_COMMIT" ]]; then
  SCHEDULER_STAGE_ROOT="${ROOT}"
  SCHEDULER_SOURCE_COMMIT="$(tr -d '[:space:]' < "${ROOT}/SOURCE_COMMIT")"
else
  SCHEDULER_STAGE_ROOT="${ROOT}/dist/zigbeelens-hacs"
  if [[ ! -f "${SCHEDULER_STAGE_ROOT}/SOURCE_COMMIT" ]]; then
    echo "FAIL: package and validate dist/zigbeelens-hacs before the exact scheduler matrix" >&2
    exit 1
  fi
  SCHEDULER_SOURCE_COMMIT="$(git -C "${ROOT}" rev-parse HEAD)"
  if [[ "$(tr -d '[:space:]' < "${SCHEDULER_STAGE_ROOT}/SOURCE_COMMIT")" != "${SCHEDULER_SOURCE_COMMIT}" ]]; then
    echo "FAIL: staged scheduler integration provenance does not equal HEAD" >&2
    exit 1
  fi
fi
SCHEDULER_COMPONENTS="${SCHEDULER_STAGE_ROOT}/custom_components"
SCHEDULER_MANIFEST="${SCHEDULER_COMPONENTS}/zigbeelens/manifest.json"
if [[ ! -f "${SCHEDULER_MANIFEST}" ]]; then
  echo "FAIL: staged scheduler integration is missing its manifest" >&2
  exit 1
fi
"${MATRIX_READER}" - \
  "${SCHEDULER_STAGE_ROOT}/SOURCE_COMMIT" \
  "${SCHEDULER_MANIFEST}" \
  "${SCHEDULER_SOURCE_COMMIT}" <<'PY'
import json
import re
import sys
from pathlib import Path

source_commit_path = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
selected_commit = sys.argv[3]
source_commit_raw = source_commit_path.read_text(encoding="utf-8")
if re.fullmatch(r"[0-9a-f]{40}\n", source_commit_raw) is None:
    raise SystemExit(
        "FAIL: staged scheduler SOURCE_COMMIT must be one normalized commit"
    )
source_commit = source_commit_raw[:-1]
if source_commit != selected_commit:
    raise SystemExit(
        "FAIL: staged scheduler SOURCE_COMMIT does not equal the selected commit"
    )

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
documentation = manifest.get("documentation")
match = (
    re.fullmatch(
        r"https://github\.com/[^/\s]+/[^/\s]+/blob/"
        r"(?P<commit>[0-9a-f]{40})/docs/hacs\.md",
        documentation,
    )
    if isinstance(documentation, str)
    else None
)
if match is None or match.group("commit") != source_commit:
    raise SystemExit(
        "FAIL: staged scheduler SOURCE_COMMIT does not match "
        "manifest documentation"
    )
PY

REQUIRED_SCHEDULER_TESTS=(
  "${HA_DIR}/tests/test_enrichment_scheduler_runtime.py::test_default_debounce_registry_event_runs_on_hass_loop_and_stops_cleanly"
  "${HA_DIR}/tests/test_enrichment_scheduler_runtime.py::test_default_retry_runs_on_hass_loop_and_stop_cancels_pending_retry"
  "${HA_DIR}/tests/test_enrichment_scheduler_runtime.py::test_default_periodic_reconciliation_runs_on_hass_loop_without_overlap_and_stops"
)

if [[ "${LANE}" != "all" && "${LANE}" != "minimum" && "${LANE}" != "current" ]]; then
  echo "Usage: $0 [all|minimum|current]" >&2
  exit 2
fi

if [[ -n "${ZIGBEELENS_HA_MATRIX_STATE_DIR:-}" ]]; then
  if [[ "${ZIGBEELENS_HA_MATRIX_STATE_DIR}" != /* ]]; then
    echo "FAIL: ZIGBEELENS_HA_MATRIX_STATE_DIR must be an absolute path" >&2
    exit 1
  fi
  if [[ -e "${ZIGBEELENS_HA_MATRIX_STATE_DIR}" ]]; then
    echo "FAIL: ZIGBEELENS_HA_MATRIX_STATE_DIR must not already exist" >&2
    exit 1
  fi
  HA_MATRIX_TMP="${ZIGBEELENS_HA_MATRIX_STATE_DIR}"
  mkdir -p "${HA_MATRIX_TMP}"
  HA_MATRIX_OWNS_TMP=false
else
  HA_MATRIX_TMP="$(mktemp -d "${TMPDIR:-/tmp}/zigbeelens-ha-matrix.XXXXXX")"
  HA_MATRIX_OWNS_TMP=true
fi
cleanup() {
  if [[ "${HA_MATRIX_OWNS_TMP}" == true ]]; then
    rm -rf "${HA_MATRIX_TMP}"
  fi
}
trap cleanup EXIT

read_lane() {
  local lane="$1"
  "${MATRIX_READER}" - "${MATRIX}" "${lane}" <<'PY'
import json
import sys
from pathlib import Path

matrix_path = Path(sys.argv[1])
lane_name = sys.argv[2]
matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
for lane in matrix["lanes"]:
    if lane["name"] == lane_name:
        print(
            "\t".join(
                (
                    lane["homeassistant"],
                    lane["python"],
                    lane["requirements"],
                )
            )
        )
        raise SystemExit(0)
raise SystemExit(f"unknown Home Assistant matrix lane: {lane_name}")
PY
}

run_lane() {
  local lane="$1"
  local lane_data
  local expected_ha
  local expected_python
  local requirements
  local python_command
  local required_scheduler_junit
  local actual_python
  local venv

  lane_data="$(read_lane "${lane}")"
  IFS=$'\t' read -r expected_ha expected_python requirements <<<"${lane_data}"

  if [[ -n "${ZIGBEELENS_HA_TEST_PYTHON:-}" ]]; then
    python_command="${ZIGBEELENS_HA_TEST_PYTHON}"
  else
    python_command="python${expected_python}"
  fi
  if ! command -v "${python_command}" >/dev/null 2>&1; then
    echo "FAIL: ${lane} lane requires Python ${expected_python}; ${python_command} was not found" >&2
    exit 1
  fi

  actual_python="$(
    "${python_command}" -c \
      'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
  )"
  if [[ "${actual_python}" != "${expected_python}" ]]; then
    echo "FAIL: ${lane} lane requires Python ${expected_python}, got ${actual_python}" >&2
    exit 1
  fi

  venv="${HA_MATRIX_TMP}/${lane}"
  echo "==> HA ${expected_ha} on Python ${expected_python} (${lane})"
  "${python_command}" -m venv "${venv}"
  "${venv}/bin/python" -m pip install \
    --disable-pip-version-check \
    -q \
    -r "${HA_DIR}/${requirements}"

  "${venv}/bin/python" - "${expected_ha}" <<'PY'
from importlib.metadata import version
import sys

expected = sys.argv[1]
actual = version("homeassistant")
if actual != expected:
    raise SystemExit(
        f"Home Assistant version mismatch: installed {actual}, expected {expected}"
    )
print(f"Exact Home Assistant version confirmed: {actual}")
PY

  required_scheduler_junit="${venv}/required-scheduler-tests.xml"
  PYTHONASYNCIODEBUG=1 \
  ZIGBEELENS_HA_TEST_COMPONENTS="${SCHEDULER_COMPONENTS}" \
  ZIGBEELENS_HA_TEST_SOURCE_COMMIT="${SCHEDULER_SOURCE_COMMIT}" \
  "${venv}/bin/python" -m pytest \
    -q \
    --junitxml="${required_scheduler_junit}" \
    "${REQUIRED_SCHEDULER_TESTS[@]}"
  "${venv}/bin/python" - "${required_scheduler_junit}" <<'PY'
import sys
from pathlib import Path
from xml.etree import ElementTree

report = Path(sys.argv[1])
root = ElementTree.parse(report).getroot()
suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
totals = {
    name: sum(int(suite.attrib.get(name, "0")) for suite in suites)
    for name in ("tests", "failures", "errors", "skipped")
}
if totals != {"tests": 3, "failures": 0, "errors": 0, "skipped": 0}:
    raise SystemExit(
        "required real-scheduler gate did not execute exactly three passing tests: "
        f"{totals}"
    )
print("Required real-scheduler tests confirmed: 3 passed, 0 skipped")
PY

  PYTHONASYNCIODEBUG=1 \
  ZIGBEELENS_HA_TEST_COMPONENTS="${SCHEDULER_COMPONENTS}" \
  ZIGBEELENS_HA_TEST_SOURCE_COMMIT="${SCHEDULER_SOURCE_COMMIT}" \
  "${venv}/bin/python" -m pytest -q "${HA_DIR}"
}

if [[ "${LANE}" == "all" ]]; then
  if [[ -n "${ZIGBEELENS_HA_TEST_PYTHON:-}" ]]; then
    echo "FAIL: ZIGBEELENS_HA_TEST_PYTHON may only be used with one exact lane" >&2
    exit 1
  fi
  run_lane minimum
  run_lane current
else
  run_lane "${LANE}"
fi
