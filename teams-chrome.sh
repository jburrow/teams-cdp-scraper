#!/usr/bin/env bash
# Start a separate Chrome profile for Teams with the DevTools port open
# on this machine only (127.0.0.1). Close this Chrome when you're done:
# while it's running, any program on your computer can control it.
exec google-chrome \
  --remote-debugging-port=9222 \
  --remote-debugging-address=127.0.0.1 \
  --user-data-dir="$HOME/.teams-chrome" \
  https://teams.microsoft.com/v2/ "$@"
