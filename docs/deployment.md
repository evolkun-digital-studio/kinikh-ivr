# Deployment

The maintained deployment runbook is the root [README](../README.md). It covers
requirements, VPS bootstrap, TLS, GitHub CI/CD, provider setup, acceptance checks,
backups, rollback, and alternative hosting architectures.

Use `docker/production.env.example` for production and `.env.example` for the full
configuration reference. Production is one FastAPI worker on one server; pause
incoming calls and drain active calls before restarting or migrating.
