# GoalKoochik development and release workflow

- Use the development PythonAnywhere account/site for changes and manual testing first. Production is the live squad site.
- Dev and production must have separate SQLite databases, private hosting configurations, sessions, invitations, and organizer accounts. Use synthetic test data in Dev by default.
- Build releases from an exact Git commit with `build_release.py`. After testing that release in Dev, promote the same verified archive to production; do not rebuild from a changed working directory.
- Preserve `data/` and `deployment_config.json` during code updates. Back up the production database before promotion. Never upload private data/configuration to GitHub or include it in release archives.
- Clearly label Dev with `environment=development`. Production configuration uses `environment=production`; existing configuration without the field remains production for compatibility.
- Follow DEPLOYMENT.md. Report which environment and source revision were changed and which checks passed. A Dev deployment is not a production deployment.
