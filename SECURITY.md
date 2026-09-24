# Security policy

## Supported versions

Only the latest commit on `main` is supported. There are no released builds.

## Reporting a vulnerability

Please do not open a public issue for security problems.

Report them privately through GitHub: open the repository's **Security** tab
and choose **Report a vulnerability**. If that option is unavailable, email
tweckenmann0711@gmail.com instead.

Include what you found, how to reproduce it, and which platform you used. I'll
reply as soon as I can; this is a personal project, so there is no guaranteed
response time.

## Scope notes

- The app starts a local Python worker that listens on `127.0.0.1` only and
  requires a per-launch session token for every request.
- Model files are downloaded from Hugging Face at pinned revisions and verified
  against SHA-256 hashes before use.
- Invoice analysis runs locally. Extracted fields and model-call details stay
  in the app session; rename history is stored locally (see the README's
  'Local processing and storage').

Issues in third-party dependencies or models belong with their upstream projects,
but a report is still welcome if they affect this app in a specific way.
