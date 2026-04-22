# J T-Shirts Call Analytics — Claude Code Instructions

## What this is

Daily analytics pipeline that pulls sales calls from RingCentral, transcribes via OpenAI Whisper, classifies each call (real discovery / IVR trap / gatekeeper / voicemail / etc.), and serves a **public Streamlit dashboard** (no login required — anyone with the URL can see it).

## How you (Claude Code) should help Mario

Mario is the CRO, technically literate but not a developer. He'll say things like:
- "roda o bootstrap" → `python jtcalls.py bootstrap`
- "atualiza os dados" → `python jtcalls.py daily`
- "abre o dashboard" → `streamlit run app.py`
- "deploya" → follow Deploy section below
- "tá dando erro X" → debug autonomously, don't ask obvious questions

When errors appear, fix them without asking. Missing deps → install. Missing DB → run bootstrap. Missing column → run migration in `lib/db.py`.

## Project structure

```
jtshirts-calls/
├── CLAUDE.md, README.md
├── .env (user creates), .env.example
├── .gitignore, requirements.txt
├── jtcalls.py              # CLI: fetch/download/transcribe/classify/dashboard
├── app.py                  # Streamlit dashboard (public, no auth)
└── lib/
    ├── config.py           # loads .env + sets paths
    ├── rc.py               # RingCentral JWT client w/ rate-limit handling
    ├── db.py               # SQLite schema + idempotent migrations
    ├── whisper.py          # OpenAI transcription
    └── classify.py         # 2-stage classifier (keyword + GPT-4o-mini)
```

`data/` and `reports/` are auto-created and gitignored.

## First-time setup (walk Mario through these steps)

```bash
cd jtshirts-calls
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# ← Mario fills in the 4 credentials in .env
python jtcalls.py bootstrap
```

Bootstrap takes 30-60 min. Safe to interrupt and resume.

After bootstrap: `streamlit run app.py` → opens dashboard at http://localhost:8501.

## Daily operation

Every morning: `python jtcalls.py daily`. Takes 2-5 min.

Optional cron (runs automatically at 7am):
```
0 7 * * * cd /path/to/jtshirts-calls && ./.venv/bin/python jtcalls.py daily
```

## Deploying to Streamlit Cloud (public URL, free)

When Mario says "deploya" or "quero colocar no ar":

### Step 1: Push to GitHub
Mario needs a GitHub account (free). You create the repo and push:
```bash
cd jtshirts-calls
git init
git add .
git commit -m "Initial"
# Create the repo at https://github.com/new (public or private — both work)
git remote add origin https://github.com/MARIO_USERNAME/jtshirts-calls.git
git branch -M main
git push -u origin main
```

**CRITICAL:** `.env` and MP3 recordings must stay local. Already in `.gitignore`.

### Step 2: Handle the data persistence issue

**Streamlit Cloud doesn't keep files between deploys.** Two solutions:

**→ RECOMMENDED (simplest):** Mario runs `jtcalls.py daily` on his Mac, then commits the updated `data/calls.db` to the repo. Streamlit Cloud auto-redeploys with fresh data.

To enable this, edit `.gitignore` to allow `data/calls.db` but still exclude `data/recordings/` (which are GB of MP3s). Replace `data/` with these 3 lines:
```
data/recordings/
data/transcripts/
!data/calls.db
```

### Step 3: Deploy on Streamlit Cloud
1. Go to https://share.streamlit.io → sign in with GitHub
2. "New app" → pick the repo, branch `main`, main file `app.py`
3. "Advanced settings" → "Secrets" → paste (TOML format):
   ```toml
   RC_CLIENT_ID = "..."
   RC_CLIENT_SECRET = "..."
   RC_JWT = "..."
   OPENAI_API_KEY = "..."
   ```
4. Click Deploy. ~2 min.
5. You get URL like `jtshirts-calls.streamlit.app`. That's the public URL Mario shares with gestores.

**No login needed.** Streamlit Cloud apps are public by default.

### Step 4: Daily refresh workflow
```bash
python jtcalls.py daily        # pull new calls, transcribe, classify
git add data/calls.db
git commit -m "Daily update $(date +%Y-%m-%d)"
git push
# Streamlit Cloud redeploys automatically in ~30s
```

You can wrap this in a single script if Mario asks.

## CLI cheatsheet

| Command | Purpose |
|---|---|
| `python jtcalls.py bootstrap` | First-time full load |
| `python jtcalls.py daily` | Every-morning incremental |
| `python jtcalls.py fetch --days N` | Just pull last N days metadata |
| `python jtcalls.py download` | Download MP3s only |
| `python jtcalls.py transcribe` | Whisper only |
| `python jtcalls.py classify` | Classify only |
| `python jtcalls.py classify --reclassify` | Re-classify everything |
| `python jtcalls.py dashboard` | Launch Streamlit locally |
| `streamlit run app.py` | Same — launch Streamlit locally |

## Principles when editing

1. **Idempotency.** Every stage resumes. Never re-download existing MP3s, re-transcribe existing text, re-classify unchanged calls.
2. **SQLite is source of truth.** `lib/db.py` has `_migrate()` — add columns there if schema evolves. Safe to run repeatedly.
3. **Rate limits:** RC recording downloads throttle at 7s interval (see `RECORDING_THROTTLE_SECONDS` in `rc.py`). Don't parallelize.
4. **Classification is 2-stage:** cheap keyword match first, GPT-4o-mini only for ambiguous. Keeps cost ~$0.0001/call.
5. **Dashboard reads, never writes.** All writes go through `jtcalls.py`.

## Common issues

**`ModuleNotFoundError`** → `source .venv/bin/activate && pip install -r requirements.txt`

**`no such column: classification`** → DB from old schema. `_migrate()` in `lib/db.py` should auto-fix. If not, just re-open the DB (`python jtcalls.py fetch --days 1` calls migrate).

**"Rate limited" during bootstrap** → normal, it waits and retries.

**Streamlit "No database found"** → DB not built yet. Run `python jtcalls.py bootstrap`.

**Public dashboard shows stale data** → cache is 5 min TTL. For immediate refresh: run `daily` locally, commit DB, push.

**RC auth fails** → JWT revoked. Regenerate at developers.ringcentral.com → Credentials.

## Cost

- RingCentral: free (existing account)
- OpenAI: ~$0.20–$0.50/day
- Streamlit Cloud: free
- GitHub: free
- **Total: under $15/month**

## J T-Shirts context (for dashboard relevance)

- Sells custom uniforms via subscription + one-time
- 2-3 active SDRs, ~60-70 outbound calls/day
- 82% of dials connect, only ~12% pass 2 min → opener is the bottleneck
- Cleveland (216) has 15% wrong-number rate; Dallas/Phoenix convert 2-3x better
- Long calls are often IVR traps, not real discovery — that's why the classifier exists
