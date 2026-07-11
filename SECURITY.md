# Security Policy

## Supported versions

This is an actively developed research project. Only the latest `main` is
supported; fixes land there.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Report a vulnerability" under the repository's **Security** tab), or contact
the maintainer via **GitHub Issues / @sinmentis** to arrange a private channel.

Please include steps to reproduce and the potential impact. We aim to
acknowledge reports within a few days.

## Scope and handling of secrets

- This project never commits secrets. API tokens (`FOOTBALL_DATA_TOKEN`,
  `ODDS_API_KEY`) live only in a local, git-ignored `.env`.
- If you find a token or credential committed anywhere in the history, treat it
  as compromised and report it privately so it can be rotated.
- The public web UI is read-only and serves data from a local SQLite database;
  report any path that lets an anonymous request write to or exfiltrate that
  database.
