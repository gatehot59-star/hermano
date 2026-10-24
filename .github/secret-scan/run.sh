#!/usr/bin/env bash
# Secret scan with two instruments (gitleaks and scan.py) and two controls.
# The controls prove each instrument CAN give red (positive) and does not
# flag an env read (negative). No matched value is ever printed.
set -uo pipefail
REPO="$(pwd)"
OUT="$REPO/secret-scan"
GL="${GITLEAKS:-/tmp/gitleaks}"
SCAN="$REPO/.github/secret-scan/scan.py"
mkdir -p "$OUT"
SUM="$OUT/summary.txt"
: > "$SUM"
log() { echo "$*" | tee -a "$SUM"; }

run_gitleaks() {  # $1 repo, $2 report
  if [ -x "$GL" ]; then
    "$GL" detect --source "$1" --log-opts="--all" --redact --no-banner \
      --report-format json --report-path "$2" >/dev/null 2>&1
    echo $?
  else
    echo "absent"
  fi
}
count_json() { python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$1" 2>/dev/null || echo "n/a"; }

make_ctrl() {  # $1 dir, $2 pos|neg
  rm -rf "$1"; mkdir -p "$1"
  git -C "$1" init -q
  git -C "$1" config user.email ctrl@example.invalid
  git -C "$1" config user.name ctrl
  python3 - "$1" "$2" <<'PY'
import secrets, string, sys
d, mode = sys.argv[1], sys.argv[2]
a = string.ascii_letters + string.digits
if mode == "pos":
    body = ('GITHUB_TOKEN = "ghp_' + ''.join(secrets.choice(a) for _ in range(36)) + '"\n'
            'GROQ = "gsk_' + ''.join(secrets.choice(a) for _ in range(52)) + '"\n'
            'QLOO_API_KEY = "' + secrets.token_hex(20) + '"\n')
else:
    body = ('import os\nQLOO_API_KEY = os.environ.get("QLOO_API_KEY", "")\n'
            'GROQ_API_KEY = os.environ["GROQ_API_KEY"]\nprint("hello")\n')
open(d + "/config.py", "w").write(body)
PY
  git -C "$1" add -A
  git -C "$1" commit -qm "control $2"
}

log "utc=$(date -u +%FT%TZ) runner=${RUNNER_OS:-local}/${RUNNER_ARCH:-?} image=${ImageOS:-?}"
log "gitleaks=$([ -x "$GL" ] && "$GL" version 2>/dev/null || echo absent) python=$(python3 -V 2>&1)"

for mode in pos neg; do
  D="/tmp/ctrl-$mode"
  make_ctrl "$D" "$mode"
  rc=$(run_gitleaks "$D" "$OUT/control-$mode-gitleaks.json")
  n=$( [ "$rc" = absent ] && echo n/a || count_json "$OUT/control-$mode-gitleaks.json")
  s=$(python3 "$SCAN" "$OUT/control-$mode-scan.json" "$D" | sed 's/^/  /')
  log "control_$mode gitleaks_rc=$rc gitleaks_findings=$n"
  log "control_$mode scan.py:$s"
done

log "repo_head=$(git -C "$REPO" rev-parse HEAD) commits_all_refs=$(git -C "$REPO" rev-list --all --count)"
log "refs: $(git -C "$REPO" for-each-ref --format='%(refname:short)' | tr '\n' ' ')"
rc=$(run_gitleaks "$REPO" "$OUT/history-gitleaks.json")
n=$( [ "$rc" = absent ] && echo n/a || count_json "$OUT/history-gitleaks.json")
log "history gitleaks_rc=$rc gitleaks_findings=$n"
s=$(python3 "$SCAN" "$OUT/history-scan.json" "$REPO" | sed 's/^/  /')
log "history scan.py:$s"
python3 - "$OUT/history-scan.json" "$OUT/history-gitleaks.json" <<'PY' | tee -a "$SUM"
import json, sys
s = json.load(open(sys.argv[1]))
print("scan.py counts_by_kind=%s suspicious_paths=%s" % (s["counts_by_kind"], s["suspicious_paths"]))
for f in s["findings"]:
    print("  scan.py %s %s %s self=%s prefix=%s len=%d sha8=%s" % (
        f["kind"], f["commit"], f["file"], f["self_scanner_file"], f["prefix"], f["len"], f["sha8"]))
try:
    g = json.load(open(sys.argv[2]))
except Exception:
    g = []
for f in g:
    print("  gitleaks %s %s %s:%s" % (f.get("RuleID"), str(f.get("Commit"))[:12], f.get("File"), f.get("StartLine")))
PY
log "done"
exit 0
