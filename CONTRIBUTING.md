# Contributing to IBR-FaultScreen

Thank you for your interest. Questions, bug reports, ideas and pull requests are welcome.

## Asking a question or reporting a bug

Open an [issue](https://github.com/ishaibu/ibr-faultscreen/issues/new/choose) and pick a
template. Please include the exact command, the full error text, and the output of
`ibr-faultscreen --version` and `python --version`.

**Never attach confidential network models** (for example a transmission provider's PSS/E
case). Describe the problem, or reproduce it on the bundled IEEE 39-bus case.

## Working on the code

```
git clone https://github.com/ishaibu/ibr-faultscreen
cd ibr-faultscreen
python -m venv .venv
.venv\Scripts\activate             # Windows (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
pytest
```

- Every new rule or formula needs a test, and a hand check where possible
  (`docs/verification.md`).
- Keep results honest: a run that cannot be solved is never reported as OK.
- `python tools/prerelease_check.py` must pass before a release.
- By contributing you agree that your contribution is licensed under GPL-3.0-or-later.
