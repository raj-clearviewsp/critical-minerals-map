#!/bin/sh
set -eu
cd "$(dirname "$0")/site"
exec ./node_modules/.bin/node scripts/open-preview.mjs
