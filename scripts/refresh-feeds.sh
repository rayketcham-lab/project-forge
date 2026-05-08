#!/bin/bash
# Refresh external feed caches (NVD, arXiv, IETF). Cron-friendly: writes
# JSON caches under data/feeds/ that the generation runner reads on each
# cycle. Each feed degrades to empty list on network failure (see logs).
#
# Recommended: daily at off-peak (e.g. 04:30 UTC).
set -euo pipefail

cd /opt/project-forge

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

export FORGE_DB_PATH="${FORGE_DB_PATH:-/opt/project-forge/data/forge.db}"

echo "$(date): refreshing external feeds..."
python3 -c "
from datetime import timedelta
from pathlib import Path
from project_forge.feeds import nvd, arxiv, ietf
from project_forge.feeds.cache import FeedCache
from project_forge.config import settings

base = Path(settings.db_path).parent / 'feeds'
base.mkdir(parents=True, exist_ok=True)

nvd_items = nvd.fetch(cache=FeedCache(base / 'nvd.json', ttl=timedelta(hours=12)), days=7)
print(f'  NVD: {len(nvd_items)} items')

arxiv_items = arxiv.fetch(cache=FeedCache(base / 'arxiv.json', ttl=timedelta(hours=48)),
                           category='cs.CR', max_results=25)
print(f'  arXiv cs.CR: {len(arxiv_items)} items')

ietf_items = ietf.fetch(cache=FeedCache(base / 'ietf.json', ttl=timedelta(hours=24)))
print(f'  IETF: {len(ietf_items)} items')
"
