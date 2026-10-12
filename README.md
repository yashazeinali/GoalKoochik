# GoalKoochik

A mobile-first private soccer match organizer. Coordinate attendance, collect private skill ratings, and generate balanced teams without an AI API.

## Features

- Organizer and player accounts; revocable group invitation links; guests.
- Playing / Maybe / Not playing responses with an explicit Save response button.
- Capacity and numbered waitlists; change match formats after attendance arrives.
- Suggested 2–4 team formats, including uneven rosters such as 3/4 and 4/4/5; optional extra-player substitute rotation.
- Five skill categories; configurable peer/self weighting; private submissions, organizer-only coverage metadata, and personal rating summaries.
- Goalkeeper willingness and hard role requirements; lineup alternatives, cross-team swaps, substitute selection, undo, and confirmation.
- Persistent SQLite storage; historical rating snapshots; mobile pitch/team cards.

## Run locally

Requires **Python 3.12+**. No third-party Python dependencies are needed.

```bash
python server.py --host 127.0.0.1 --port 8765
```

Open `http://localhost:8765` and create the first organizer account. On Windows, `Start Matchday.cmd` is also provided. To test from another device on a trusted local network, bind `--host 0.0.0.0` and use the laptop's current LAN address. Local HTTP is intended for testing; use test passwords.

Data is created in `data/matchday.sqlite3`. The internal storage filename is retained for compatibility. Never publish the `data/` directory.

## Tests

```bash
python -m unittest -v test_matchday.py test_hosted.py
```

The suites cover aggregation, missing/self ratings, authentication and privacy, stale writes, attendance and waitlists, resizing, uneven multi-team generation, swaps/undo, protected hosted setup, HTTPS cookies, origin checks, rate limits, and backups. Tests use disposable databases under the ignored `work/` directory.

## PythonAnywhere hosting

1. Create a free PythonAnywhere account and upload or clone this source into `/home/YOUR_USERNAME/GoalKoochik`.
2. Create a web app with **Manual configuration → Python 3.12**. Use one WSGI worker.
3. In a Bash console, run `python3.12 /home/YOUR_USERNAME/GoalKoochik/configure_host.py` and enter the actual assigned hostname.
4. This creates a private `deployment_config.json` and prints the owner-only setup URL when the database has no accounts. Do not commit this file or share that URL.
5. Copy `pythonanywhere_wsgi.py` into the generated WSGI configuration file and replace `YOUR_USERNAME` with your actual username.
6. Enable **Force HTTPS** in Web settings and reload the web app.
7. Open your private setup URL, create the organizer account, and share ordinary group invitation links from Group settings.

Free PythonAnywhere hosting uses the account's assigned `USERNAME.pythonanywhere.com` address. Renew the free web app monthly in Web settings. Custom domains require a paid plan.

The managed WSGI server runs `hosted.application`; do not run the local `server.py` HTTP server in production. No virtual environment is required because this app uses the standard library.

## Security and persistence

Hosted mode enforces the configured host and HTTPS origin, Secure/HttpOnly/SameSite session cookies, protected first-organizer setup, and authentication rate limits. Passwords use salted PBKDF2 hashes. Authorization is checked on the server.

The SQLite backup API creates one backup on the first request of each day and retains seven backups on the same hosting disk. Download backups separately to protect against loss of the hosting account. Preserve `data/` and `deployment_config.json` during updates. Never map those paths as publicly served static files.

One WSGI worker is required by the pilot's in-process locking architecture. Before scaling to multiple workers, refactor state mutations into transactionally updated database tables. Recovery, email verification, password reset, and account-deletion interfaces are not yet implemented.

Individual scores remain private. Organizers can see **who has rated whom**, category completion counts, and dates. Public peer averages are hidden below three peer submissions, although anonymity cannot be guaranteed in small groups.

## Balancing

Only selected Playing participants enter generation, exactly once. The saved roster sizes must be filled. Team sizes can differ by at most one, with 2–10 players per team and 20 total. The organizer may mark extra roster members as rotating substitutes; rotation is a designation, not an automatic timer.

Two-team splits and small multi-team configurations are exhaustively enumerated. Larger configurations examine at most 6,000 reproducibly seeded partitions. Goalkeeper/substitute assignments take the first valid role-coverage choice, so even exhaustive split enumeration does not guarantee globally optimal assignments. Bounded search failure does not prove infeasibility.

Balance compares starting-player category averages across teams. Substitutes are excluded, goalkeeper ability is constrained separately, and Defense/Stamina/Passing/Attacking contribute to the objective. The result is a balanced suggestion, not a winning probability. Energy reports are collected but do not change the balancing objective yet.

## Updating

Replace code and static files, preserve private data/configuration, reload the PythonAnywhere web app, and refresh browsers. Repository publication does not automatically deploy changes to the website.

This repository contains source and synthetic test fixtures only. It does not contain player data, live accounts, sessions, backups, invitation tokens, or production setup keys.
