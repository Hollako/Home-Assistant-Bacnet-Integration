#!/usr/bin/env sh
set -eu

exec python -m bacnet_bridge.app --options /data/options.json --store /data/mappings.json
