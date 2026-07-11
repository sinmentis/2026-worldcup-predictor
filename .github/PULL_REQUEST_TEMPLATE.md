## What and why

<!-- What does this change do, and why? Link any related issue (e.g. Closes #12). -->

## How it was tested

<!-- Commands you ran and their result. -->

- [ ] `uv run ruff check src/ tests/`
- [ ] `uv run ruff format --check src/ tests/`
- [ ] `uv run mypy src/`
- [ ] `uv run pytest -q`

## Checklist

- [ ] Tests cover the new behaviour (failing before, passing after)
- [ ] The engine stays deterministic — no LLM in the prediction math
- [ ] Any new intel is source-linked and passes the trust gate
- [ ] Conventional Commit message(s)
- [ ] Docs updated if behaviour or interfaces changed
