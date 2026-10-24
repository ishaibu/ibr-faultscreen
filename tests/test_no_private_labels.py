"""No internal planning labels in the repository.

The author's private planning notes use short labels for tasks, decisions and
tests. Those notes are not part of the project, so the labels would mean nothing
to a reader. This test scans every text file and fails on any such label, so
none can slip in unnoticed. `tools/prerelease_check.py` runs the same scan.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".cff", ".ipynb", ".txt", ".cfg",
            ".ini", ".json", ".csv", ".html", ".js"}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "build", "dist", ".pytest_cache"}
# letter + digit labels such as task "week-step", decision and test numbers
PATTERNS = [
    re.compile(r"\bW\d{1,2}(-\d+)?\b"),          # task labels (week, week-step)
    re.compile(r"\bS\d\b"),                      # session labels (not scenario ids like S00012)
    re.compile(r"\bD\d{1,2}\b"),                # decision labels
    re.compile(r"\bT\d{1,2}\b"),                # test labels
    re.compile(r"\btest_t\d+_|_t\d+\b"),        # the same, inside Python names
    re.compile(r"build[ -]plan", re.I),
]


def _generated(rel):
    """Generated or local-only paths: packaging metadata, caches, result folders."""
    return bool(set(rel.parts) & SKIP_DIRS) or any(
        part.endswith((".egg-info", "_output")) or part.startswith(("dataset_", "model_"))
        for part in rel.parts)


def _tracked(root):
    """Files tracked by Git (None if Git is not available)."""
    import subprocess
    try:
        # tracked files plus new files not yet committed (but not ignored ones)
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=root, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    files = [root / f for f in out.splitlines() if f]
    return files or None


def text_files(root=ROOT):
    tracked = _tracked(root) if root == ROOT else None
    if tracked is not None:            # only what would be published
        yield from (p for p in tracked if p.is_file() and p.suffix.lower() in SUFFIXES
                    and not _generated(p.relative_to(root)))
        return
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in SUFFIXES and not _generated(p.relative_to(root)):
            yield p


def find_labels(root=ROOT):
    hits = []
    for p in text_files(root):
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for pat in PATTERNS:
                m = pat.search(line)
                if m:
                    hits.append(f"{p.relative_to(root)}:{n}: '{m.group(0)}' in: {line.strip()[:90]}")
    return hits


def test_no_private_labels():
    hits = find_labels()
    assert not hits, "Internal planning labels found:\n" + "\n".join(hits)


def test_scanner_catches_labels(tmp_path):
    (tmp_path / "a.md").write_text("Fixed in " + "W" + "3-3 (see " + "D" + "11).\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def test_" + "t7_x(): pass\n", encoding="utf-8")
    assert len(find_labels(tmp_path)) == 3
