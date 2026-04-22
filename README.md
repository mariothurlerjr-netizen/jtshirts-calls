# J T-Shirts Call Analytics

Daily RingCentral call analysis pipeline with qualitative classification and dashboard.

## Setup (one time, ~10 min)

### 1. Install Python dependencies

```bash
cd jtshirts-calls
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create `.env`

Copy `.env.example` to `.env` and fill in the credentials:

```bash
cp .env.example .env
```

Edit `.env`:
```
RC_CLIENT_ID=W8oHLv16dvaeekgCY3AwQk
RC_CLIENT_SECRET=fafrnsBcsj0bcD2Yv0eIWhA2CAAeN0bFceMmNWrxLLM7
RC_JWT=eyJraWQiOiI...(full JWT from RingCentral)
OPENAI_API_KEY=sk-proj-...(full key from OpenAI)
```

### 3. First-time bootstrap (loads all history)

```bash
python jtcalls.py bootstrap
```

This runs for 30-60 minutes:
- Fetches all call metadata since Jan 1 of current year
- Downloads MP3 recordings for calls ≥ 2 minutes
- Transcribes each via Whisper
- Classifies each call (discovery / IVR / gatekeeper / voicemail / etc.)
- Generates `reports/dashboard.html`

You can close the terminal and come back — it's safe to resume.

### 4. Open the dashboard

```bash
open reports/dashboard.html
```

## Daily operation

Every morning, run:

```bash
python jtcalls.py daily
```

Pulls only yesterday's new calls, processes them, and updates the dashboard. Takes 2–5 minutes.

**Pro tip:** Add this to your crontab to run automatically at 7 AM:
```
0 7 * * * cd /path/to/jtshirts-calls && ./.venv/bin/python jtcalls.py daily
```

## What the dashboard shows

- **Yesterday vs. 7-day average** — quick glance at whether things improved or slipped
- **Real discovery count** — calls where actual conversation happened (excludes IVR/hold)
- **Per-rep scoreboard** — dials, connection rate, discovery rate, avg quality
- **Call classification breakdown** — discovery / IVR trap / gatekeeper / voicemail / etc.
- **Top objections this week** — extracted from real discovery calls
- **Geography heatmap** — which area codes convert vs. waste dials
- **Best time to dial** — hour of day + day of week with highest discovery rate

## Directory structure

```
jtshirts-calls/
├── jtcalls.py            # Main CLI — run this
├── lib/                  # Code modules
├── data/                 # Generated; don't commit
│   ├── calls.db          # SQLite — source of truth
│   ├── recordings/       # MP3s
│   └── transcripts/      # Transcript .txt files
└── reports/              # Generated dashboards
    └── dashboard.html
```

## Troubleshooting

**"ModuleNotFoundError"** — activate the venv: `source .venv/bin/activate`

**"Auth failed"** — JWT was probably revoked. Regenerate at developers.ringcentral.com → Credentials → Create JWT.

**"Rate limited" during bootstrap** — normal. The script waits and retries automatically.

**Dashboard is blank** — bootstrap didn't finish. Run it again; it resumes from where it stopped.

## Costs

- ~$0.20–$0.50/day in OpenAI API usage (Whisper + GPT-4o-mini)
- RingCentral: free (uses existing account)
- Total: under $15/month
