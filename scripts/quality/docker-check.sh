#!/bin/sh
# Backward-compatible default: Lite is the product-default image variant.
set -eu
exec "$(dirname "$0")/docker-check-lite.sh" "$@"
