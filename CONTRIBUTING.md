# Contributing

Thanks for helping. Bug reports, fixes and translations are all welcome.

## Reporting problems

Open an [issue](https://github.com/quartermaster-app/ha-quartermaster/issues/new/choose) and attach diagnostics (Settings → Devices & services → Quartermaster → three-dot menu → Download diagnostics). Diagnostics never contain your token, server address, household name or items. If you paste logs, remove addresses and tokens first.

Problems with Quartermaster itself (the app, sorting, stores) belong in the Quartermaster repository.

## Development setup

You need Python 3.14 and [uv](https://docs.astral.sh/uv/).

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements_test.txt
.venv/bin/python -m pytest --cov
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
```

Pull requests need to keep all of these passing, including 100% line and branch coverage. CI also runs hassfest and the HACS action.

Guidelines:

- The API client in `custom_components/quartermaster/api/` must not import Home Assistant. It should stay usable as a standalone library.
- Raise `ServiceValidationError` for things the user got wrong and `HomeAssistantError` for failures, always with a `translation_key` from `strings.json`.
- Every user-facing string goes in `strings.json`; copy it to `translations/en.json` (they must stay identical).
- Keep `quality_scale.yaml` honest when you change behaviour.
- Use generic example data in tests and docs ("Maple Street", "Kitchen", `example.com`), never real names, addresses or tokens.

To try it in a real Home Assistant, copy or symlink `custom_components/quartermaster` into a test instance's `config/custom_components/`.

## Releases

1. Update `version` in `custom_components/quartermaster/manifest.json` and `pyproject.toml`, and move the `Unreleased` notes in `CHANGELOG.md` under a new version heading.
2. Commit, then tag and push: `git tag v0.3.0 && git push origin main v0.3.0`.
3. The Release workflow runs the full validation, checks that the tag, manifest and changelog agree, and publishes a GitHub release with the changelog notes and `quartermaster.zip`. HACS picks up new releases automatically.
