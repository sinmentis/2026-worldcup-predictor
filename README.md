# worldcup-predictor

MODEL_BACKEND=primary (penaltyblog)

`penaltyblog` installs, imports, and fits on this arm64 host with `penaltyblog==1.11.0`. Model code must pass writable goal arrays using `.to_numpy().copy()` for `home_goals` and `away_goals`. Calls to `dixon_coles_weights` must pass datetimes, for example with `pd.to_datetime(...)`.
