#!/usr/bin/env python3
"""History-wide secret scan for this repository.

Reads every added line of every commit reachable from any ref
(git log -p --all), plus every path that ever existed, and writes a JSON
report. It never prints or stores a matched value: only its kind, commit,
file, length, a 4-character prefix and the first 8 hex of its sha256.

Usage: python3 scan.py REPORT.json [REPO_DIR]
"""
import hashlib
import json
import re
import subprocess
import sys

PATTERNS = {
    "groq_key": r"gsk_[A-Za-z0-9]{20,}",
    "render_key": r"rnd_[A-Za-z0-9]{20,}",
    "github_token": r"gh[pousr]_[A-Za-z0-9]{30,}",
    "github_pat_fine": r"github_pat_[A-Za-z0-9_]{40,}",
    "openai_like": r"sk-(?:or-v1-|proj-)?[A-Za-z0-9_\-]{20,}",
    "google_api": r"AIza[0-9A-Za-z_\-]{35}",
    "aws_access": r"AKIA[0-9A-Z]{16}",
    "slack": r"xox[abprs]-[A-Za-z0-9\-]{10,}",
    "private_key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "key_literal": r"(?i)\b[A-Z0-9_]*(?:API_?KEY|SECRET|TOKEN|PASSWORD)\b[\"']?\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']",
    "header_literal": r"(?i)(?:x-api-key|authorization)[\"']?\s*[:=,]\s*[\"'](?:Bearer\s+)?[A-Za-z0-9_\-\.]{16,}[\"']",
}
COMPILED = {k: re.compile(v) for k, v in PATTERNS.items()}
SUSPICIOUS_PATH = re.compile(
    r"(?i)(^|/)(\.env(\..*)?|[^/]*\.pem|[^/]*\.key|id_[rd]sa[^/]*|credentials[^/]*"
    r"|secrets?\.[^/]*|[^/]*\.sqlite3?|[^/]*\.db|cache/.*)$"
)
SELF_DIR = ".github/secret-scan/"


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                          text=True, errors="replace", check=True).stdout


def mask(value):
    prefix = value[:4] if len(value) >= 20 else value[:2]
    return {"prefix": prefix, "len": len(value),
            "sha8": hashlib.sha256(value.encode()).hexdigest()[:8]}


def main():
    report_path = sys.argv[1]
    repo = sys.argv[2] if len(sys.argv) > 2 else "."
    head = git(repo, "rev-parse", "HEAD").strip()
    refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname:short)").split("\n")
    refs = [r for r in refs if r]
    n_commits = int(git(repo, "rev-list", "--all", "--count").strip())
    log = git(repo, "log", "-p", "--all", "--full-history", "--no-color", "--text",
              "--no-ext-diff", "--format=__COMMIT__ %H")
    findings, seen = [], set()
    commit, path = None, None
    added = 0
    for line in log.split("\n"):
        if line.startswith("__COMMIT__ "):
            commit = line.split()[1]
            continue
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        added += 1
        body = line[1:]
        for kind, rx in COMPILED.items():
            for m in rx.finditer(body):
                key = (kind, commit, path, m.group(0))
                if key in seen:
                    continue
                seen.add(key)
                f = {"kind": kind, "commit": commit[:12], "file": path,
                     "self_scanner_file": bool(path and path.startswith(SELF_DIR))}
                f.update(mask(m.group(0)))
                findings.append(f)
    paths = sorted({p for p in git(repo, "log", "--all", "--name-only", "--format=").split("\n") if p})
    suspicious = [p for p in paths if SUSPICIOUS_PATH.search(p)]
    counts = {}
    for f in findings:
        if not f["self_scanner_file"]:
            counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    report = {
        "head": head, "refs": refs, "commits_scanned": n_commits,
        "patch_bytes": len(log.encode()), "added_lines_scanned": added,
        "paths_ever": len(paths), "suspicious_paths": suspicious,
        "findings_outside_scanner": sum(counts.values()), "counts_by_kind": counts,
        "findings": findings, "patterns": PATTERNS,
    }
    with open(report_path, "w") as fh:
        json.dump(report, fh, indent=1, sort_keys=True)
    print("commits=%d added_lines=%d paths_ever=%d findings_outside_scanner=%d suspicious_paths=%d"
          % (n_commits, added, len(paths), report["findings_outside_scanner"], len(suspicious)))


if __name__ == "__main__":
    main()
