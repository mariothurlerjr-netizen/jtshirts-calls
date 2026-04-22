#!/bin/bash
cd /Users/mariothurlerjr/Downloads/jtshirts-calls
source .venv/bin/activate
python jtcalls.py daily
git add data/calls.db
git commit -m "Daily update $(date +%Y-%m-%d)"
git push
