"""
Pitch Analyzer — reads all real_discovery transcripts and asks GPT-4o to:
1. Identify winning patterns (opener, qualification, pitch structure, objection handling)
2. Identify losing patterns (what killed momentum in shorter discoveries)
3. Produce a concrete, ready-to-use cold-call script for J T-Shirts outbound

Usage:
    python pitch_analyzer.py              # analyze and print
    python pitch_analyzer.py --save       # also write reports/pitch_analysis.md
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import requests

from lib import config

MODEL = "gpt-4o-mini"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DB_PATH = Path("data/calls.db")
REPORT_PATH = Path("reports/pitch_analysis.md")


def load_discovery_calls() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT id, from_name, duration, opener_extract, objections_found,
               transcript, start_time
        FROM calls
        WHERE classification = 'real_discovery'
          AND transcript IS NOT NULL AND transcript != ''
        ORDER BY duration DESC
        """
    )
    calls = [dict(r) for r in cur.fetchall()]
    conn.close()
    return calls


def load_failed_openers(limit: int = 20) -> list[dict]:
    """Openers from calls that died quickly — what NOT to do."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT from_name, duration, opener_extract, classification
        FROM calls
        WHERE classification IN ('gatekeeper','ivr_hold')
          AND opener_extract IS NOT NULL AND opener_extract != ''
        ORDER BY duration ASC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(r) for r in cur.fetchall()]


def build_prompt(wins: list[dict], fails: list[dict]) -> str:
    wins_text = "\n\n".join(
        f"--- CALL {i+1} · rep={c['from_name']} · duration={c['duration']}s · "
        f"objections={c['objections_found'] or '[]'} ---\n"
        f"OPENER: {c['opener_extract']}\n\n"
        f"TRANSCRIPT:\n{c['transcript']}"
        for i, c in enumerate(wins)
    )

    fails_text = "\n".join(
        f"- [{c['classification']} · {c['duration']}s · {c['from_name']}] {c['opener_extract']}"
        for c in fails
    )

    return f"""You are a senior B2B sales coach specializing in cold-calling SMB services
(cleaning, HVAC, construction, restaurant) to sell uniform subscriptions.

You are analyzing real call transcripts from J T-Shirts sales team. Current
performance is brutal: **1.85% discovery rate** (29 real conversations out of
1,565 dials). The goal is to extract what works from the 29 winners and
produce a concrete, battle-tested cold-call script.

════════════════════════════════════════════════════════════════════
WINNING TRANSCRIPTS (calls that reached real discovery)
════════════════════════════════════════════════════════════════════
{wins_text}

════════════════════════════════════════════════════════════════════
FAILED OPENERS (calls killed by gatekeeper or IVR — do NOT repeat these patterns)
════════════════════════════════════════════════════════════════════
{fails_text}

════════════════════════════════════════════════════════════════════
YOUR ANALYSIS — be specific, data-driven, actionable.
Output in Brazilian Portuguese. Use markdown.
════════════════════════════════════════════════════════════════════

Structure your response EXACTLY as follows:

# Análise de Pitch · J T-Shirts Outbound

## 1. Padrões vencedores (o que funcionou)
For each pattern, cite the specific call IDs and direct quotes. Cover:
- Melhor estrutura de abertura (segundos 0-15)
- Pergunta de qualificação mais efetiva
- Como reps contornaram objeções com sucesso
- Transições que mantiveram a conversa viva

## 2. Padrões perdedores (o que matou a call)
What specific phrases/approaches got cut off fast? Use real examples.

## 3. SCRIPT VENCEDOR · Versão 1.0
A concrete, ready-to-read script with:

**Abertura (0-5s)** — the exact words to say
**Qualificação (5-20s)** — 1-2 questions to ask before any pitch
**Hook de valor (20-45s)** — if qualified, the pitch sentence
**Respostas às 3 objeções mais comuns** — word-for-word, using the "Acknowledge → Reframe → Ask" pattern
**Fechamento** — next-step ask (meeting, email, sample)

Script should be in ENGLISH (calls are in English) but all commentary in Portuguese.

## 4. Métricas pra acompanhar
Top 3 KPIs to track whether the new script improves results.

## 5. Aviso estatístico
Explicitly call out: we're learning from 29 calls. What's signal vs noise?
What should be A/B tested before declaring victory?
"""


def analyze(wins: list[dict], fails: list[dict]) -> str:
    prompt = build_prompt(wins, fails)

    print(f"→ Analisando {len(wins)} discoveries + {len(fails)} exemplos negativos...")
    print(f"→ Prompt size: {len(prompt):,} chars (~{len(prompt)//4:,} tokens)")

    import time
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a sales coach. Be rigorous, specific, and honest about sample size."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    for attempt in range(6):
        r = requests.post(OPENAI_URL, headers=headers, json=payload, timeout=300)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 0)) or (2 ** attempt * 15)
            print(f"  429 rate-limited, sleeping {wait}s (attempt {attempt+1}/6) body={r.text[:200]}")
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            wait = 2 ** attempt * 5
            print(f"  {r.status_code}, retrying in {wait}s...")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    r.raise_for_status()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--save", action="store_true", help="Write markdown report to reports/")
    args = p.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"DB not found at {DB_PATH}")

    wins = load_discovery_calls()
    fails = load_failed_openers()

    if not wins:
        sys.exit("No real_discovery calls with transcripts found.")

    print(f"Loaded {len(wins)} winning calls, {len(fails)} failed openers.")
    analysis = analyze(wins, fails)

    print("\n" + "═" * 72 + "\n")
    print(analysis)

    if args.save:
        REPORT_PATH.parent.mkdir(exist_ok=True)
        header = f"<!-- Generated: {datetime.utcnow().isoformat()}Z · model={MODEL} · wins={len(wins)} fails={len(fails)} -->\n\n"
        REPORT_PATH.write_text(header + analysis)
        print(f"\n→ Saved to {REPORT_PATH}")


if __name__ == "__main__":
    main()
