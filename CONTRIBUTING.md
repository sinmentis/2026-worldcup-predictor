# Contributing

Thanks for your interest in the World Cup Predictor. This is a research and
technical-showcase project; contributions that improve the model, the tooling,
or the docs are welcome.

## Ground rules

- **The engine is deterministic; the LLM only handles language.** Prediction math
  (goal model, simulation, calibration, value bets) must never depend on an LLM.
  LLMs read news and write structured, source-linked intel through the MCP server
  and nothing else. Keep that boundary.
- **No fabricated data.** Intel must carry a real source URL and clear the trust
  gate. Never invent stats, results, or player statuses.
- **Everything auditable.** Predictions, value bets and paper-trading entries are
  reproducible from the SQLite database. Don't add hidden state.

## Development setup

This project uses [uv](https://github.com/astral-sh/uv) (not pip).

```bash
uv sync                       # create the venv and install deps
cp .env.example .env          # add API tokens if you have them (optional for tests)
uv run pytest -q              # run the test suite
```

## Before you open a pull request

Run the full quality bar — CI runs exactly these and must pass:

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run mypy src/              # strict mode
uv run pytest -q
```

- **Tests first.** New behaviour needs a test that fails before your change and
  passes after. Tests must assert real behaviour, not mocks-of-mocks.
- **Small, focused commits.** Use [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `ci:`).
- **Keep files focused.** Prefer small modules with one clear responsibility.

## Reporting bugs and ideas

Open an issue with one of the templates. For anything security-related, see
[SECURITY.md](SECURITY.md) instead of filing a public issue.
