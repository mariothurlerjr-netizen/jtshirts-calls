"""Reusable guides surfaced inside the app (CRM, Coaching, Sales Playbook)."""

GATEKEEPER_MATRIX = """
## J T-Shirts Gatekeeper Response Matrix
### Standardized responses for cold call gatekeeper scenarios

**Purpose:** this matrix gives reps a pre-approved response for every common gatekeeper scenario. When everyone uses the same phrasing, the team can measure what works, coach to a standard, and onboard new reps faster.

**Usage in CRM:** each row is a trigger–response pair. Map the trigger to a call disposition or status, surface the response as a tooltip or quick-reply when the rep logs that status.

---

#### Block 1 · Phone pick-up (first 5 seconds)

| Trigger (what the gatekeeper says) | Rep Response |
|---|---|
| "[Company Name], how can I help you?" (trained receptionist) | "Hi, this is Mario. Could you put me through to Alex, please?" |
| "Hello?" (informal pick-up, small operation) | "Hi, this is Mario from J T-Shirts. Am I catching Alex, or is this someone else?" |
| "Alex's office" / "Alex's line" | "Hi, is Alex available? This is Mario." |
| "Who's calling?" (pick-up with immediate challenge) | "This is Mario from J T-Shirts. Is Alex around?" |

---

#### Block 2 · First resistance ("what's this about?")

| Trigger | Rep Response |
|---|---|
| "What is this regarding?" | "It's about a proposal I put together for him. He'll know what it's about." |
| "Can I ask who's calling and why?" | "Sure. Mario from J T-Shirts. I'm calling with a proposal that could save the company money on uniforms this year." |
| "Is Alex expecting your call?" | "No, he's not. This is a cold call. I know he's busy, so I'll keep it to 30 seconds if you can put me through." |
| "Is this a sales call?" | "Honest answer, yes. I'm calling with a specific cost-savings proposal for [vertical] companies your size. Worth 30 seconds of Alex's time if he's around." |
| "We don't accept sales calls" | "Understood. Before I go, quick question: who handles uniform decisions on your end, and what's the best way to reach them? I won't keep calling this line." |

---

#### Block 3 · Deflection attempts

| Trigger | Rep Response |
|---|---|
| "Just send the info to info@[company]" | "Happy to. One question first: does Alex actually open things sent to info@, or do they get buried? If you give me your direct email, I can send you a short note you can forward to him with context, which gets opened." |
| "Just send me the catalog" | "Sure. Quick question before I do: when you pass things like this to Alex, do they get read or do they get lost in the pile? I want to make sure it actually gets in front of him. If you give me 30 seconds now, I can give you a 3-bullet summary you can forward with the catalog, which is what actually gets opened." |
| "Email it to me and I'll pass it along" | "Perfect. I'll send you a short email, not a catalog, so it's easy to forward. What's your direct email? And what's Alex's email so I can copy him?" |
| "Leave a message and I'll have him call you back" | "Thanks. Before I do, can I get his direct line or email? Messages through the front desk rarely come back, and I'd rather reach out directly when it's a better time for him." |
| "He's not available right now" | "No problem. What's a better time to catch him, morning or afternoon? And is this the best number, or does he have a direct line?" |
| "He's in a meeting" | "Got it. Does he usually come out of meetings at the top of the hour? I can try back at [time]. Also, what's his direct line so I don't have to go through the switchboard?" |

---

#### Block 4 · Hard blocks

| Trigger | Rep Response |
|---|---|
| "He's not interested in sales calls" | "Fair enough. Did you check with him, or is that a general policy? I'm asking because this specific proposal is about cutting uniform costs, which most owners do want to hear about. Worth a 15-second check with him?" |
| "I handle all vendor calls" | "Then you're actually the right person for this conversation. How many times have you reordered uniforms for the crew in the last 12 months?" |
| "We have a supplier and we're happy" | "Got it. Out of curiosity, how many times did you reorder with them in the last 12 months? I'm asking because most [vertical] companies are reordering 3 to 4 times a year, and there's a pattern we've been tracking that might be worth Alex knowing about." |
| "Stop calling us" | "Understood. I'll take this number off our list. Before I do, is there a better contact for future reference, or do you want us to not reach out at all?" |
| "Take us off your list" | "Done. One last thing: can I confirm the best way to remove you, just this number or the whole company? I want to make sure nobody else reaches out." |

---

#### Block 5 · Spouse or co-owner patterns

Common in small family-run operations (1–30 employees). The person who answers is often a co-decider, not a gatekeeper.

| Trigger | Rep Response |
|---|---|
| "This is his wife/husband, can I help?" | "Actually yes. You're probably the right person to start with. How many times have you reordered uniforms for the crew in the last 12 months?" |
| "I help Alex with the office side" | "Perfect, then you probably know more about the uniform program than he does. How often are you reordering?" |
| "I'm his business partner" | "Great, even better. Real quick: how many times did you reorder uniforms in the last 12 months?" |
| "I just answer the phone, I don't handle that" | "Got it, no problem. What's the best way to reach Alex directly, and what's the best time of day to catch him?" |

---

#### Block 6 · Successful transfer (what to say when they put you through)

| Trigger | Rep Response |
|---|---|
| "Hold on, I'll put you through" | "Thanks, appreciate it." (say nothing else, wait for decision-maker to pick up) |
| "Let me see if he's available" (on hold) | Wait silently. Don't fill the silence. |
| Decision-maker picks up | Go to Universal Opening: "Hi Alex, this is Mario from J T-Shirts. I'm calling with a proposal that could save your company money on uniforms this year. Got 5 minutes?" |

---

#### Block 7 · Graceful exit (when nothing works)

Use when the gatekeeper has blocked firmly and there's no path forward on this call.

| Trigger | Rep Response |
|---|---|
| Gatekeeper refuses transfer, refuses email exchange | "Alright, I hear you. Last ask: if I send a short note to info@[company], is there any chance it gets to Alex, or should I try a different channel? I'm not going to keep bothering this line." |
| Gatekeeper gives info@ but nothing else | "Thanks. One more: what's Alex's first and last name so I can make sure it gets routed to him specifically?" |
| Gatekeeper is friendly but can't help | "Appreciate you being straight with me. If anything changes down the road, or if you want a second opinion on a uniform quote, my direct line is [number]. Thanks for your time." |

---

#### Block 8 · Key info to extract on every gatekeeper call

Even if you don't get through, log these in the CRM before ending:

| Data Point | How to ask |
|---|---|
| Decision-maker full name | "Just so I spell it right, is that A-L-E-X [Last Name]?" |
| Decision-maker direct line | "What's his direct extension or mobile?" |
| Decision-maker email | "What's his email so I can follow up in writing?" |
| Best time to call | "What time of day is he usually available?" |
| Gatekeeper's name | "Sorry, I didn't catch your name?" (always ask, use on follow-up calls) |

---

#### Block 9 · Follow-up email template (send within 1 hour of any gatekeeper call)

**Subject:** "3 bullets on [vertical] uniform costs for [Company]"

```
Hi [Gatekeeper Name],

Thanks for taking my call earlier. As promised, here's the short version for [Decision-Maker Name]:

- Most [vertical] companies [Decision-Maker]'s size reorder uniforms 3 to 4 times a year
- Our [vertical] clients reorder once a year because of a fabric spec built for this kind of work
- For a crew the size of [Company], the 12-month savings is meaningful

If [Decision-Maker] wants to see the wash data and 2 client references, I can do 10 minutes Tuesday or Thursday. Direct line: [number]. Calendly: [link].

Thanks for your help.

Mario
```

---

#### Call disposition mapping (for CRM)

When logging the call in the CRM, map outcomes to these dispositions:

| Disposition | Definition | Next Action |
|---|---|---|
| **GK-Transferred** | Gatekeeper put you through | Run Universal Opening with decision-maker |
| **GK-Got-DM-Info** | Gatekeeper gave direct line/email but didn't transfer | Send forward-ready email within 1 hour |
| **GK-Info-Only** | Gatekeeper gave only generic info@ email | Send email, retry phone in 3 business days at different time |
| **GK-Hard-Block** | Gatekeeper refused everything | Move to email + LinkedIn sequence, no phone retry for 30 days |
| **GK-Co-Decider** | Spouse/partner engaged on discovery | Log them as secondary contact, book meeting with them |
| **GK-DNC** | "Take us off your list" | Flag DNC in CRM, no further contact |

---

#### Rules that apply to every gatekeeper interaction

1. **Never lie.** Not about knowing the owner, not about prior contact, not about why you're calling. Gatekeepers report back to the owner, and caught lies kill the deal for good.
2. **Never pitch the gatekeeper.** They can't buy. Pitching wastes both of your time.
3. **Tone over words.** Relaxed, confident, brief. Over-explaining signals salesperson. Brevity signals insider.
4. **Always get the gatekeeper's name.** Use it on every follow-up call. They remember.
5. **Respect always.** Even on a hard block. The rep who loses with grace today is the first call when the current supplier fails.
"""
