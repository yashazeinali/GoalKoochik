# Development first, then production

Use separate PythonAnywhere accounts and source folders for Dev and production. Each has its own `deployment_config.json` and `data/matchday.sqlite3`. Do not copy either between accounts. Dev uses fresh or synthetic test records; live player records stay in production.

## Build one release

Commit reviewed source to GitHub, then fetch that commit into a local clone. Build from its full commit ID:

```bash
python build_release.py build --repository /path/to/clone --revision FULL_COMMIT_ID --output /path/to/release.zip
python build_release.py verify /path/to/release.zip
```

The archive includes only the explicit source allowlist and a revision/checksum manifest. Databases, uploaded player images in the database, sessions, secrets, host configuration and backups are excluded. The builder reads committed Git objects, so local edits cannot silently change the artifact.

## Install in Dev

Upload the verified archive and `build_release.py` through PythonAnywhere Files. In its Bash console:

```bash
python3.12 build_release.py unpack release.zip --target /home/DEV_USERNAME/GoalKoochik-dev
cd /home/DEV_USERNAME/GoalKoochik-dev
python3.12 configure_host.py --host DEV_USERNAME.pythonanywhere.com --environment development
python3.12 -m unittest -v test_matchday.py test_hosted.py test_release.py
```

Run `configure_host.py` only when creating a fresh environment. It refuses to replace an existing private config. Preserve the owner-only setup URL; create the organizer account in the browser yourself. Dev displays a DEVELOPMENT banner including the tested commit.

Back up an existing generated WSGI file before editing it. Set `project` in PythonAnywhere's generated WSGI file to the Dev source folder and import `hosted.application` (see `pythonanywhere_wsgi.py`). Use one worker, enable Force HTTPS, and reload the Dev web app.

Check organizer setup/login, a separate test player, invitations, profiles/photo resizing, Playing/Not playing responses, guest handling, ratings privacy/coverage, team generation and publishing, history/CSV, and phone layout. Use synthetic demo records if useful. Record the tested revision and any limitations before promotion.

## Promote the tested artifact

1. Finish Dev checks and review the result. Promote the exact tested archive; do not copy Dev's database or configuration.
2. In the production account, take a consistent SQLite backup with Python's `sqlite3.Connection.backup` API. Retain the previous code release/WSGI configuration for rollback.
3. Verify and unpack the archive into the production source folder. The allowlist cannot overwrite `data/` or `deployment_config.json`. Keep its production hostname/environment configuration.
4. Run required checks using disposable test databases, reload the production web app, and check login, current/history records, key screens, and the release ID in the authenticated `/api/state` response.
5. Compare the saved live match/player records around deployment. A read-only check should not submit sample production responses or ratings.

For a code-only regression, restore the previous code archive and reload. Future database schema migrations need their own reviewed backup/restore plan; do not assume code rollback can reverse them. Keep only a small number of code releases on the 512 MB plan, and renew each free web app monthly.
