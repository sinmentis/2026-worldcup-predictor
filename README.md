# worldcup-predictor

MODEL_BACKEND=primary (penaltyblog)

`penaltyblog` installs, imports, and fits on this arm64 host with `penaltyblog==1.11.0`. Model code must pass writable goal arrays using `.to_numpy().copy()` for `home_goals` and `away_goals`. Calls to `dixon_coles_weights` must pass datetimes, for example with `pd.to_datetime(...)`.

## MCP server

The stdio MCP server exposes the engine through FastMCP tools:

```bash
uv --directory /home/shunlyu/work/worldcup-predictor run worldcup-mcp
```

VS Code can use the checked-in `.vscode/mcp.json`. In GitHub Copilot CLI, add the same command with `/mcp add`.
