#!/usr/bin/env sh
# Home Assistant add-on entrypoint. Options are read from /data/options.json
# by app.config; SUPERVISOR_TOKEN is injected by the Supervisor.
exec python -m app.main
