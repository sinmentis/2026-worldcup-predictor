# worldcup-predictor

MODEL_BACKEND=fallback

`penaltyblog` installs and imports on this arm64 host, but the milestone spike fails during `DixonColesGoalModel.fit()` with `ValueError: buffer source array is read-only`. Later milestones should use the documented fallback backend behind the same `GoalModel` API.
