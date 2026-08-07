#!/bin/sh
set -eu

REPO_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
cd "$REPO_DIR"

echo "Architecture: $(uname -m)"
echo "Time: $(date --iso-8601=seconds)"
echo "Timezone: $(timedatectl show -p Timezone --value 2>/dev/null || echo unknown)"
docker version --format 'Docker server: {{.Server.Version}} {{.Server.Os}}/{{.Server.Arch}}'
docker compose version
docker compose config --quiet
test -f .env
test -f data/va_path.csv
echo "Configuration files: OK"
docker compose run --rm trader python main.py status
docker compose run --rm trader python main.py dry-run

