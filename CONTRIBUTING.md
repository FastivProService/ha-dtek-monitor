# Contributing to DTEK Monitor

**English** · [Українська](CONTRIBUTING.uk.md) · [User guide](README.md)

Bug reports, fixes and documentation improvements are welcome in English or Ukrainian.

## Choose the right repository

DTEK website parsing, regional address lookup and HA entities belong to [DTEK Monitor](https://github.com/groove-max/ha-dtek-monitor). Telegram delivery, message templates and local-sensor power detection belong to [DTEK Telegram Bot](https://github.com/groove-max/ha-dtek-telegram-bot).

Before opening an issue, search existing issues and include versions, reproduction steps, expected/actual behavior and a short sanitized log. Do not publish bot tokens, HA tokens, exact private addresses or raw production journals. For vulnerabilities, use [Security](SECURITY.md).

## Run the checks

From the repository root, with the indicated Python version installed:

```bash
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -c "import json, subprocess, sys; subprocess.check_call([sys.executable, '-m', 'pip', 'install', *json.load(open('custom_components/dtek_monitor/manifest.json'))['requirements']])"
python -m unittest discover -s tests -v
python -m compileall custom_components tests
```

The lightweight suite skips HA-dependent tests when Home Assistant is absent. For complete verification, use a separate Python 3.14 environment, install `homeassistant==2026.3.0` plus the manifest requirements, and rerun the suite. Match `.github/workflows/tests.yml`; do not describe skipped runtime tests as passed.

Provider changes need fixtures for published, unpublished, empty, malformed and multi-group data. Keep provider/address identity and publication coverage intact. New regions must use the provider interface and explicit rollout settings, not hardcoded regional branching in HA entities.

## Test against a live installation

Use a separate HA/test destination and keep production notification delivery disabled during experiments. Never toggle electrical equipment as an incidental software test. Document which scenarios used fixtures and which were actually observed.

## Documentation and pull requests

- Keep `README.md` and `README.uk.md` aligned; detailed guides also have `.uk.md` counterparts.
- Keep the README focused on installing and using the project. Put contracts and implementation detail in the reference/contributor guides.
- Validate relative links and YAML examples. Use fictional addresses and placeholder entity IDs.
- Update the changelog for user-visible behavior changes; historical release entries stay in their original language.
- Explain the problem, resulting behavior and meaningful checks. Preserve existing user settings and identifiers unless a documented migration is necessary.
- Do not commit credentials, private diagnostics, caches, virtual environments or `graphify-out/` artifacts.

Contributions must be your own work or compatible with this project’s [license](LICENSE). Submit contributions under PolyForm Noncommercial 1.0.0 and preserve third-party notices.

## Release validation

Run the HA runtime suite on both `2026.3.0` and `2026.8.3`, as in CI. Run the browser suite separately with Playwright 1.58.0 and Chromium:

```sh
python -m unittest discover -s tests -p 'test_*card.py' -v
```

Release tags use `vMAJOR.MINOR.PATCH`, optionally followed by `aN`, `bN` or `rcN`. Validate with `python scripts/release.py v2.0.0b1`. The manifest, JavaScript module cache versions and changelog must agree. The release job marks these suffixes as prereleases and leaves the latest stable release unchanged.

The local UI demo is `examples/account-workspace/index.html`, served from the repository root with `python -m http.server 8974 --bind 127.0.0.1`. It uses fictional data; its connection screens are a mockup and never send SMS or readings.
