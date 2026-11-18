#!/usr/bin/env bash
# Build the public site into site/: the landing page (website/) at the root, the MkDocs
# documentation (docs/) under /docs/. Needs the packages of requirements-docs.txt.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf site
mkdocs build --strict --site-dir site/docs
cp -R website/. site/
rm -rf site/videos/build
# Provenance records of the screenshots are for the repository, not for the site.
find site -name '*.webp.json' -delete
touch site/.nojekyll
echo "Built site/ ($(find site -type f | wc -l | tr -d ' ') files). Preview: python3 -m http.server -d site 8000"
