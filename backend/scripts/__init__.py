"""
Host-side one-off scripts: seeding, migration, manual model checks.

None of these run inside the Docker image — it runs only the API and the worker
— so `.dockerignore` keeps this directory out of the build context entirely.

Run them from `backend/`, which is where `config.py` and the `services` /
`agentic` / `ml` packages live:

    ..\\env\\Scripts\\python.exe scripts\\seed_admin.py

Running a file directly puts `scripts/` on sys.path rather than `backend/`, so
each script prepends the parent directory itself before importing anything from
the application.
"""
