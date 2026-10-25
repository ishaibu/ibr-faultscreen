"""Pre-release checks. Run from the project folder:  python tools/prerelease_check.py

Checks that the version agrees everywhere, the changelog has an entry for it,
nothing large or private is tracked by Git, no internal planning labels are
left, and the files a release needs exist.
It does not run the tests: run `pytest` separately.
"""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
problems, notes = [], []


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout


version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
notes.append(f"pyproject.toml version: {version}")

cff = (ROOT / "CITATION.cff").read_text("utf-8")
m = re.search(r"^version:\s*(\S+)", cff, re.M)
if not m or m.group(1).strip("\"'") != version:
    problems.append(f"CITATION.cff version is {m.group(1) if m else 'missing'}, expected {version}")
d = re.search(r"^date-released:\s*\"?([\d-]+)", cff, re.M)
notes.append(f"CITATION.cff date-released: {d.group(1) if d else 'missing'}")

log = (ROOT / "CHANGELOG.md").read_text("utf-8")
if f"## [{version}]" not in log:
    problems.append(f"CHANGELOG.md has no '## [{version}]' section")

for f in ("LICENSE", "README.md", ".github/workflows/release.yml",
          ".github/workflows/tests.yml", "src/ibr_faultscreen/data/criteria_prc029.yaml",
          "src/ibr_faultscreen/data/ibr_profiles.yaml"):
    if not (ROOT / f).exists():
        problems.append(f"missing {f}")

tracked = [t for t in git("ls-files").splitlines() if t]
if not tracked:
    problems.append("git ls-files returned nothing (is this the project folder?)")
# the published demo page and README images may be larger (web assets)
def size_limit(path):
    return 6_000_000 if path.startswith(("docs/demo/", "docs/images/")) else 1_000_000


big = [(t, (ROOT / t).stat().st_size) for t in tracked
       if (ROOT / t).exists() and (ROOT / t).stat().st_size > size_limit(t)]
for t, size in big:
    problems.append(f"large tracked file ({size / 1e6:.1f} MB): {t}")

ever = sorted(set(git("log", "--all", "--name-only", "--pretty=format:").split()))
risky_words = ("acciona", "ceii", "confidential", "private", ".env", "token", "secret",
               "password", ".sav", "output/", ".joblib")
# project files whose names contain a risky word for a harmless reason
allowed = {"tests/test_no_private_labels.py"}
flagged = [f for f in ever if f not in allowed and any(w in f.lower() for w in risky_words)]
if flagged:
    problems.append("files ever committed whose names need a look: " + ", ".join(flagged))
notes.append(f"files ever committed: {len(ever)}")

# internal planning labels (same scan as tests/test_no_private_labels.py)
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("labels", ROOT / "tests" / "test_no_private_labels.py")
labels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(labels)
hits = labels.find_labels(ROOT)
if hits:
    problems.append("internal planning labels found:\n      " + "\n      ".join(hits[:20]))
notes.append(f"internal planning labels: {len(hits)}")
for f in labels.private_files_present(ROOT):
    problems.append(f"maintainer-only file in the repository (keep it privately): {f}")

print("\n".join("  " + n for n in notes))
if problems:
    print("\nFIX BEFORE RELEASING:")
    print("\n".join("  - " + p for p in problems))
    sys.exit(1)
print("\nAll pre-release checks passed.")
