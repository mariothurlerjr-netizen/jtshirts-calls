"""
Classify each call's transcript into one of:
  - real_discovery   : actual human conversation with the prospect
  - ivr_hold         : rep stuck in IVR menus, automated assistants, or hold music
  - gatekeeper       : human receptionist/assistant who blocked the rep
  - voicemail_left   : rep left a voicemail
  - quick_hangup     : prospect answered then hung up fast
  - wrong_number     : wrong person / wrong department
  - unknown          : couldn't tell

Two-stage pipeline: cheap keyword classifier first, GPT-4o-mini for ambiguous.
"""
import json
import logging
import re

import requests

from . import config

log = logging.getLogger("classify")


# ── keyword patterns (case-insensitive regex) ───────────────────────────────

IVR_SIGNALS = [
    r"\bpress\s*[1-9#*]\b",
    r"\bpress\s*(one|two|three|four|five|six|seven|eight|nine|zero|pound|star)\b",
    r"\bfor\s+\w+[\s,]+press\b",
    r"\bmain\s+menu\b",
    r"\bto\s+return\s+to\s+(this|the)\s+menu\b",
    r"\bplease\s+(hold|stay\s+on\s+the\s+line)\b",
    r"\byour\s+call\s+is\s+important\b",
    r"\bthis\s+call\s+(is|may\s+be)\s+(being\s+)?recorded\b",
    r"\bi(\s+am|'m)\s+(?:an?\s+)?(automated|digital|virtual)\s+(assistant|agent)\b",
    r"\bpowered\s+by\s+AI\b",
    r"\ball\s+agents\s+are\s+(currently\s+)?(busy|assisting)\b",
    r"\bestimated\s+wait\s+time\b",
    r"\bthank\s+you\s+for\s+(calling|holding|waiting)\b",
    r"\bexisting\s+customers?\s+press\b",
    r"\bnew\s+customers?\s+press\b",
    r"\bto\s+speak\s+(to|with)\s+a\s+(live\s+)?representative\b",
    r"\bdial\s+(by|the)\s+(name|extension)\b",
]

GATEKEEPER_SIGNALS = [
    r"\bwho(?:'?s|\s+is)\s+calling\b",
    r"\bmay\s+I\s+ask\s+who\b",
    r"\bwhat\s+(is\s+this|company\s+are\s+you)\s+(regarding|with)\b",
    r"\bthey(?:'re|\s+are)\s+not\s+(available|in|here)\b",
    r"\bcan\s+I\s+take\s+(a\s+)?message\b",
    r"\bwould\s+you\s+like\s+to\s+leave\s+a\s+message\b",
    r"\bhe's\s+not\s+available\b",
    r"\bshe's\s+not\s+available\b",
    r"\bin\s+a\s+meeting\b",
    r"\bout\s+of\s+the\s+office\b",
]

VOICEMAIL_LEFT_SIGNALS = [
    r"\bplease\s+leave\s+(a|your)\s+message\s+after\s+the\s+(tone|beep)\b",
    r"\bat\s+the\s+tone,?\s+please\s+record\b",
    r"\brecord\s+your\s+message\b",
    r"\bi'll\s+try\s+(back|again)\s+(later|tomorrow)\b",  # rep leaving msg
]

WRONG_NUMBER_SIGNALS = [
    r"\bwrong\s+number\b",
    r"\byou\s+have\s+the\s+wrong\b",
    r"\bthere's\s+no\s+one\s+here\s+by\s+that\s+name\b",
    r"\bno\s+\w+\s+here\b",  # "no Mary here"
    r"\bdoesn't\s+work\s+here\s+(anymore|any\s+more)\b",
]


def _hit(patterns, text):
    """Return count of regex hits."""
    return sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))


def classify_by_keywords(transcript: str, duration: int, result: str) -> dict | None:
    """
    Return a classification dict if we can determine with high confidence,
    else None (caller should escalate to LLM).
    """
    if not transcript or len(transcript.strip()) < 30:
        return {"classification": "unknown", "confidence": 0.3,
                "reason": "transcript too short"}

    ivr = _hit(IVR_SIGNALS, transcript)
    gate = _hit(GATEKEEPER_SIGNALS, transcript)
    vm = _hit(VOICEMAIL_LEFT_SIGNALS, transcript)
    wn = _hit(WRONG_NUMBER_SIGNALS, transcript)

    # Strong IVR signal
    if ivr >= 3:
        return {"classification": "ivr_hold", "confidence": 0.9,
                "reason": f"matched {ivr} IVR patterns"}

    # Moderate IVR + long call = almost certainly stuck in IVR
    if ivr >= 2 and duration >= 180:
        return {"classification": "ivr_hold", "confidence": 0.85,
                "reason": f"{ivr} IVR patterns in {duration}s call"}

    # Voicemail hit clearly
    if vm >= 1 and duration < 90:
        return {"classification": "voicemail_left", "confidence": 0.8,
                "reason": "voicemail prompt detected"}

    # Wrong number explicit
    if wn >= 1:
        return {"classification": "wrong_number", "confidence": 0.85,
                "reason": "explicit wrong-number phrase"}

    # Gatekeeper (short call with gatekeeper signals, no IVR)
    if gate >= 2 and ivr == 0 and duration < 180:
        return {"classification": "gatekeeper", "confidence": 0.75,
                "reason": f"{gate} gatekeeper patterns"}

    # Very short call
    if duration < 30:
        return {"classification": "quick_hangup", "confidence": 0.7,
                "reason": f"only {duration}s"}

    # Otherwise: escalate to LLM
    return None


# ── LLM classifier ───────────────────────────────────────────────────────────

LLM_PROMPT = """You are analyzing a cold call transcript from a uniform sales company (J T-Shirts) calling B2B prospects (cleaning companies, HVAC, restaurants, construction).

Classify this call into EXACTLY ONE category:

- real_discovery: Actual conversation with the right person where the rep pitched and the prospect engaged (asked questions, gave info, discussed needs). At least some substantive back-and-forth.
- ivr_hold: Rep spent most of the call navigating automated menus, talking to AI assistants, or on hold. Even if they eventually reached a human, if >50% of the call was IVR/hold, classify as ivr_hold.
- gatekeeper: Rep reached a human (receptionist, assistant) who blocked the call — "they're not available", "let me transfer you", "what is this regarding".
- voicemail_left: Rep left a voicemail. Transcript shows rep speaking to a machine.
- quick_hangup: Prospect answered and hung up quickly, or rep hung up fast.
- wrong_number: Wrong person / wrong company / wrong department clearly identified.
- unknown: Can't determine confidently.

Also extract:
- objections: list of specific objections the PROSPECT raised (if any). Examples: "we have a vendor", "not interested", "too expensive", "send info", "not looking right now", "bad timing".
- opener: the first 1-2 sentences the REP said to the prospect (not to IVR/assistant). Empty string if no human was reached.

Return ONLY valid JSON, no markdown, no explanation:
{
  "classification": "...",
  "confidence": 0.0 to 1.0,
  "reason": "brief why",
  "objections": [...],
  "opener": "..."
}

Transcript:
<<<
{TRANSCRIPT}
>>>"""


def classify_with_llm(transcript: str) -> dict:
    """Use GPT-4o-mini for ambiguous cases."""
    # Truncate very long transcripts (only need the first part to classify)
    text = transcript[:6000]
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": LLM_PROMPT.replace("{TRANSCRIPT}", text)}],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"LLM classify failed [{r.status_code}]: {r.text[:300]}")
    content = r.json()["choices"][0]["message"]["content"]
    data = json.loads(content)
    # Normalize
    valid = {"real_discovery", "ivr_hold", "gatekeeper",
             "voicemail_left", "quick_hangup", "wrong_number", "unknown"}
    if data.get("classification") not in valid:
        data["classification"] = "unknown"
    return data


# ── main entry point ─────────────────────────────────────────────────────────

def classify_call(transcript: str, duration: int, result: str) -> dict:
    """
    Return dict with keys:
        classification, confidence, reason, objections (list), opener (str)
    """
    # Try keyword-only first
    kw_result = classify_by_keywords(transcript, duration, result)
    if kw_result and kw_result["confidence"] >= 0.75:
        # High confidence keyword match — skip LLM
        kw_result["objections"] = []
        kw_result["opener"] = ""
        return kw_result

    # Escalate to LLM
    try:
        llm_result = classify_with_llm(transcript)
        return {
            "classification": llm_result.get("classification", "unknown"),
            "confidence": float(llm_result.get("confidence", 0.5)),
            "reason": llm_result.get("reason", ""),
            "objections": llm_result.get("objections", []),
            "opener": llm_result.get("opener", ""),
        }
    except Exception as e:
        log.warning("LLM classify failed: %s", e)
        # Fall back to low-confidence keyword result or unknown
        if kw_result:
            kw_result["objections"] = []
            kw_result["opener"] = ""
            return kw_result
        return {"classification": "unknown", "confidence": 0.1,
                "reason": f"LLM error: {e}",
                "objections": [], "opener": ""}
