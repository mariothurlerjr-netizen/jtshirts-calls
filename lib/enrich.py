"""
Call enrichment — single-shot LLM per transcript.

Returns a rich JSON with:
- quality_score (0-100)
- reached_whom (decision_maker | gatekeeper | voicemail | ivr | wrong_number | unknown)
- was_productive (bool)
- deal_signals (BANT)
- key_moments (best_line, worst_line, pivot_moment)
- next_step_extracted + due date
- objection_handled (bool)
- coaching_feedback (3 bullets for the rep)
- summary (1-2 sentences)
- stage_hint (suggested next stage)
- objections (list of {category, verbatim, rep_response, outcome})

Uses gpt-4o-mini for cost (~$0.005/call).
"""
import json
import logging
import time

import requests

from . import config

log = logging.getLogger("enrich")

MODEL = "gpt-4o-mini"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

VALID_REACHED = {"decision_maker", "gatekeeper", "voicemail", "ivr", "wrong_number", "unknown"}

VALID_OBJECTIONS = {
    "current_vendor", "long_term_contract", "not_interested", "budget",
    "bad_timing", "send_info", "no_authority", "wrong_person",
    "size_fit", "tried_before", "other",
}

VALID_OUTCOMES = {"killed_call", "continued", "converted"}

VALID_WHY_NOT = {
    "not_asked", "blocked_by_gk", "not_decision_maker",
    "rejected_no_interest", "rejected_bad_timing", "rejected_current_vendor",
    "deferred_send_info", "booked",
}

VALID_STAGES = {
    "Cold", "Attempted", "Gatekeeper", "DM Reached",
    "Email to Send", "Meeting", "Proposal", "Won", "Lost",
}


PROMPT = """You are a senior B2B sales coach analyzing a cold call from J T-Shirts,
a company that sells custom uniforms and t-shirts via subscription to SMB service
businesses (cleaning, HVAC, restaurants, construction, security, landscaping).

The rep (name provided) made an outbound call. Analyze the transcript rigorously.
Be honest about what actually happened — do not be generous. A call that didn't
reach a decision-maker is NOT productive, even if the rep delivered a nice pitch.

══════════════════════════════════════════════════
CALL CONTEXT
══════════════════════════════════════════════════
Rep: {REP_NAME}
Call duration: {DURATION}s
Call result (RC status): {RESULT}
Prior classification: {CLASSIFICATION}
Company/callee name (from caller ID): {COMPANY}

══════════════════════════════════════════════════
TRANSCRIPT
══════════════════════════════════════════════════
{TRANSCRIPT}

══════════════════════════════════════════════════
YOUR OUTPUT — valid JSON, no markdown, follow schema EXACTLY
══════════════════════════════════════════════════

{{
  "quality_score": <int 0-100>,
  "reached_whom": "<one of: decision_maker | gatekeeper | voicemail | ivr | wrong_number | unknown>",
  "was_productive": <true|false>,
  "summary": "<1-2 sentences, factual, what happened>",
  "deal_signals": {{
    "budget":    {{"present": <bool>, "evidence": "<quote or empty>"}},
    "authority": {{"present": <bool>, "evidence": "<quote or empty>"}},
    "need":      {{"present": <bool>, "evidence": "<quote or empty>"}},
    "timeline":  {{"present": <bool>, "evidence": "<quote or empty>"}}
  }},
  "key_moments": {{
    "best_line": "<rep's strongest line, verbatim, or empty>",
    "worst_line": "<rep's weakest line, verbatim, or empty>",
    "pivot_moment": "<the turn where call shifted good/bad, or empty>"
  }},
  "meeting": {{
    "asked": <true|false>,
    "ask_phrase": "<exact rep phrase where they asked for a meeting — empty if not asked>",
    "booked": <true|false>,
    "prospect_response": "<exact prospect reaction to the ask — empty if not asked>",
    "why_not": "<one of: not_asked | blocked_by_gk | not_decision_maker | rejected_no_interest | rejected_bad_timing | rejected_current_vendor | deferred_send_info | booked>"
  }},
  "next_step_extracted": "<concrete commitment captured — e.g. 'send catalog to john@acme.com'. Empty if none>",
  "next_step_due": "<YYYY-MM-DD if mentioned, else empty>",
  "objection_handled": <true|false>,
  "coaching_feedback": [
    "<bullet 1: specific, actionable, cite what to do differently>",
    "<bullet 2>",
    "<bullet 3>"
  ],
  "stage_hint": "<one of: Cold | Attempted | Gatekeeper | DM Reached | Email to Send | Meeting | Proposal | Won | Lost>",
  "objections": [
    {{
      "category": "<one of: current_vendor | long_term_contract | not_interested | budget | bad_timing | send_info | no_authority | wrong_person | size_fit | tried_before | other>",
      "verbatim": "<exact prospect phrase, max 160 chars>",
      "rep_response": "<exact rep response, max 200 chars, empty if none>",
      "outcome": "<one of: killed_call | continued | converted>"
    }}
  ]
}}

Rules:
- If it's IVR or voicemail, set reached_whom accordingly and objections = [].
- `was_productive` is true ONLY if the rep had a substantive back-and-forth with a
  real human prospect (receptionist blocking counts as not productive).
- `quality_score` reflects REP PERFORMANCE, not the outcome. A rep can do a great
  job and still get blocked by a gatekeeper — score that 70+. A rep fumbling a DM
  conversation with a live prospect gets a low score.
- `stage_hint` should reflect the FURTHEST progression achieved in THIS call.
  First touch that went to voicemail = Attempted. Gatekeeper blocked = Gatekeeper.
  Real DM conversation = DM Reached. "Send me info" commitment = Email to Send.
  Actual meeting booked = Meeting.
- `meeting.asked` = true if rep EXPLICITLY asked for a meeting, call, demo, or
  in-person appointment (e.g. "can I set up a 15-minute call", "would next
  Tuesday work", "can I come by"). A vague "I'd like to talk more" is NOT asking.
- `meeting.booked` = true ONLY if a concrete day/time was agreed, or prospect said
  yes to a specific scheduling question. Otherwise false.
- `meeting.why_not` captures the real failure reason. Meeting conversion is the
  CORE METRIC — be precise here.
- Extract ALL distinct objections raised in the call, not just one.
- `coaching_feedback` MUST be specific ("Open with their name, not the company
  name" — not "improve your opener"). Write in English.
- Respond ONLY with valid JSON. No preamble, no code fences, no trailing text.
"""


def enrich_transcript(
    transcript: str,
    rep_name: str,
    duration: int,
    result: str,
    classification: str,
    company: str,
    max_retries: int = 5,
) -> dict:
    """
    Run one LLM call to produce a full enrichment record. Returns a dict that
    matches the schema above (may have extra normalization applied).
    """
    if not transcript or len(transcript.strip()) < 30:
        return _empty_enrichment("transcript too short")

    # Truncate very long transcripts to keep costs down
    text = transcript[:10000]

    prompt = (PROMPT
              .replace("{REP_NAME}", rep_name or "unknown")
              .replace("{DURATION}", str(duration or 0))
              .replace("{RESULT}", result or "")
              .replace("{CLASSIFICATION}", classification or "")
              .replace("{COMPANY}", company or "")
              .replace("{TRANSCRIPT}", text))

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a rigorous B2B sales coach. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    for attempt in range(max_retries):
        try:
            r = requests.post(OPENAI_URL, headers=headers, json=payload, timeout=120)
        except requests.RequestException as e:
            wait = 2 ** attempt * 3
            log.warning("  request error %s, retry in %ds", str(e)[:80], wait)
            time.sleep(wait)
            continue

        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 0)) or (2 ** attempt * 10)
            log.info("  429 rate-limit, sleep %ds (attempt %d/%d)", wait, attempt + 1, max_retries)
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            wait = 2 ** attempt * 5
            log.warning("  %d server error, retry in %ds", r.status_code, wait)
            time.sleep(wait)
            continue
        if r.status_code != 200:
            raise RuntimeError(f"enrich failed [{r.status_code}]: {r.text[:300]}")

        content = r.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        return _normalize(data)

    raise RuntimeError("enrich exhausted retries")


def _empty_enrichment(reason: str) -> dict:
    return {
        "quality_score": 0,
        "reached_whom": "unknown",
        "was_productive": False,
        "summary": reason,
        "deal_signals": {
            "budget": {"present": False, "evidence": ""},
            "authority": {"present": False, "evidence": ""},
            "need": {"present": False, "evidence": ""},
            "timeline": {"present": False, "evidence": ""},
        },
        "key_moments": {"best_line": "", "worst_line": "", "pivot_moment": ""},
        "meeting": {
            "asked": False, "ask_phrase": "", "booked": False,
            "prospect_response": "", "why_not": "not_asked",
        },
        "next_step_extracted": "",
        "next_step_due": "",
        "objection_handled": False,
        "coaching_feedback": [],
        "stage_hint": "Attempted",
        "objections": [],
    }


def _normalize(data: dict) -> dict:
    """Clamp / default fields so downstream code never crashes on bad LLM output."""
    out = _empty_enrichment("")

    # Score
    try:
        out["quality_score"] = max(0, min(100, int(data.get("quality_score", 0))))
    except (TypeError, ValueError):
        out["quality_score"] = 0

    # Reached whom
    rw = data.get("reached_whom", "unknown")
    out["reached_whom"] = rw if rw in VALID_REACHED else "unknown"

    out["was_productive"] = bool(data.get("was_productive", False))
    out["summary"] = str(data.get("summary", ""))[:500]

    # Deal signals
    ds = data.get("deal_signals") or {}
    for k in ("budget", "authority", "need", "timeline"):
        v = ds.get(k) or {}
        out["deal_signals"][k] = {
            "present": bool(v.get("present", False)),
            "evidence": str(v.get("evidence", ""))[:200],
        }

    # Key moments
    km = data.get("key_moments") or {}
    out["key_moments"] = {
        "best_line": str(km.get("best_line", ""))[:300],
        "worst_line": str(km.get("worst_line", ""))[:300],
        "pivot_moment": str(km.get("pivot_moment", ""))[:300],
    }

    # Meeting
    mt = data.get("meeting") or {}
    why = mt.get("why_not", "not_asked")
    if why not in VALID_WHY_NOT:
        why = "not_asked"
    out["meeting"] = {
        "asked": bool(mt.get("asked", False)),
        "ask_phrase": str(mt.get("ask_phrase", ""))[:300],
        "booked": bool(mt.get("booked", False)),
        "prospect_response": str(mt.get("prospect_response", ""))[:300],
        "why_not": why,
    }

    out["next_step_extracted"] = str(data.get("next_step_extracted", ""))[:300]
    out["next_step_due"] = str(data.get("next_step_due", ""))[:30]
    out["objection_handled"] = bool(data.get("objection_handled", False))

    cf = data.get("coaching_feedback") or []
    if isinstance(cf, list):
        out["coaching_feedback"] = [str(b)[:300] for b in cf[:3] if str(b).strip()]

    sh = data.get("stage_hint", "Attempted")
    out["stage_hint"] = sh if sh in VALID_STAGES else "Attempted"

    # Objections
    objs = data.get("objections") or []
    clean = []
    if isinstance(objs, list):
        for o in objs:
            if not isinstance(o, dict):
                continue
            cat = o.get("category", "other")
            if cat not in VALID_OBJECTIONS:
                cat = "other"
            outcome = o.get("outcome", "killed_call")
            if outcome not in VALID_OUTCOMES:
                outcome = "killed_call"
            clean.append({
                "category": cat,
                "verbatim": str(o.get("verbatim", ""))[:160],
                "rep_response": str(o.get("rep_response", ""))[:200],
                "outcome": outcome,
            })
    out["objections"] = clean

    return out
