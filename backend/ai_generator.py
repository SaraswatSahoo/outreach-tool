"""
Groq API email generation logic.
Constructs prompts per the spec and returns parsed JSON with subject + body.
Incorporates resume highlights for personalization.
"""

import json
import random
import re
import threading
import time

from groq import Groq

from llm import DEFAULT_TEXT_MODELS, NoModelAvailable, is_model_unavailable, model_kwargs, unavailable_message
from utils import resolve_company_name

SYSTEM_PROMPT = """You are helping a college student write personalised cold outreach emails for internship/job opportunities.
These emails will be sent directly from the student's Gmail. They must feel like real, human-written emails — not templates, not cover letters, not LinkedIn messages.

MANDATORY EMAIL STRUCTURE (follow this every time):
1. Greeting line: Start with "Hi [Name]," if name is known, or "Hi there," if unknown. Never "Dear Sir/Madam". Never skip the greeting.
2. Opening sentence: A natural, varied opener that isn't "I've been following your work". It could be:
   - A direct, confident intro ("I'm Priya, a third-year CS student at <college>, and I wanted to reach out about...")
   - A brief, genuine observation about the company (only if it feels specific and natural — not forced flattery)
   - Getting straight to the point ("I came across [Company] while researching ML startups and thought I'd reach out directly.")
   Vary this opener every single time. Never repeat the same structure across emails.
3. Middle (2–3 sentences): Briefly state what you're looking for, mention ONE specific thing from your background (a project or experience, one sentence max), and connect it to why you're reaching out to them specifically.
4. Availability + ask (1–2 sentences): Mention availability and end with a soft, specific ask. Vary the closing line — do NOT use the same sentence across emails.
5. Sign-off: "Best," followed by the student's first name on a new line.

BANNED PHRASES (never use any of these):
- "I've been following [X]'s work"
- "I am writing to express my interest"
- "I would be honored"
- "I am a passionate individual"
- "I believe my skills could be a great fit"
- "Would love to hear if there's any opportunity to contribute" (you may vary the sentiment but not reuse this exact line)
- "synergy", "leverage", "cutting-edge", "innovative solutions", "bringing to the table"
- "I hope this email finds you well"
- "Dear Sir/Madam"
- Any phrase that sounds like it was copied from a cover letter template

TONE:
- Semi-formal: professional but conversational — like a confident student emailing a founder, not a formal job application
- Direct and concise — get to the point quickly
- Warm but not sycophantic — genuine, not flattering

LENGTH: 120-170 words for the body (including greeting and sign-off). Shorter is better if the message is complete.

FACTS — NEVER INVENT:
- Only mention availability, start dates, hours per week, duration, location or relocation if they are stated in the CAMPAIGN GOAL or ADDITIONAL CONTEXT. If not stated, leave them out entirely.
- Never invent achievements, numbers, companies, or projects that aren't in the student profile or resume highlights.

OUTPUT FORMAT: Return ONLY a valid JSON object: {"subject": "...", "body": "..."}
The body must include the greeting line and sign-off. No markdown, no extra text."""


BRIEF_SYSTEM_PROMPT = """You help a student set up a cold-outreach email campaign. From their answers and profile,
write the two fields the campaign tool needs:

1. "goal": 1-3 sentences, written in the first person as an instruction to the email writer, stating what the
   student is asking for (role/field, type of opportunity), who the recipients are, and the timeframe if given.
2. "additional_context": short, specific sentences (one per line) with the extra facts the emails should use:
   the project to highlight, availability, flexibility, links, tone. Empty string if nothing was given.

RULES:
- Use ONLY facts present in the answers or profile. Never invent dates, availability, hours, locations,
  numbers, achievements or projects. If something wasn't given, leave it out.
- Be concrete and concise; no fluff, no marketing language.
- Return ONLY JSON: {"goal": "...", "additional_context": "..."}"""


def generate_brief(profile, resume_parsed, answers, api_key, models=None):
    """Turn the brief-builder answers into the campaign's goal and additional context."""
    from llm import json_chat

    projects = [p.get("title") for p in (resume_parsed or {}).get("projects", []) if p.get("title")]
    lines = [f"- {key.replace('_', ' ').capitalize()}: {value}" for key, value in answers.items() if value]
    user_prompt = f"""STUDENT PROFILE:
- Name: {profile.get("name", "")}
- College: {profile.get("college", "")}, {profile.get("branch", "")}, {profile.get("year", "")}
- Skills: {profile.get("skills", "")}
- Resume projects: {", ".join(projects) or "none listed"}

THEIR ANSWERS:
{chr(10).join(lines) or "- (none)"}"""
    result = json_chat(
        api_key,
        models or DEFAULT_TEXT_MODELS,
        [{"role": "system", "content": BRIEF_SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
        temperature=0.4,
    )
    goal = str(result.get("goal") or "").strip()
    if not goal:
        raise ValueError("The AI returned an empty goal — try again")
    return {"goal": goal, "additional_context": str(result.get("additional_context") or "").strip()}


def build_resume_highlights(resume_parsed: dict) -> str:
    """
    Construct a concise resume highlights block for injection into the AI prompt.
    Picks the most relevant projects and one experience entry.
    """
    if not resume_parsed:
        return "No resume data available."

    lines = []

    projects = resume_parsed.get("projects", [])
    projects = projects.copy()
    random.shuffle(projects)

    if projects:
        lines.append("""PROJECTS (listed in random order — evaluate ALL of them before choosing):
Selection rule: Choose the project whose domain and tech stack most closely match this recipient's company.
- Infer what the company does from its name/domain and the campaign goal
- Prefer the project with the strongest overlap in industry (e.g. healthcare, fintech, e-commerce, research) and technology
- If unclear → pick whichever has the most tech overlap with the campaign goal
Do NOT default to the first project listed. Actively reason about fit.""")
        for p in projects[:4]:  # max 4 projects passed to AI
            title = p.get("title", "")
            desc = p.get("description", "")
            if title:
                lines.append(f"  - {title}: {desc}")

    experience = resume_parsed.get("experience", [])
    if experience:
        lines.append("EXPERIENCE / RESEARCH:")
        for e in experience[:3]:  # max 3 entries
            role = e.get("role", "")
            org = e.get("organization", "")
            desc = e.get("description", "")
            if role:
                lines.append(f"  - {role} at {org}: {desc}")

    achievements = resume_parsed.get("achievements", [])
    if achievements:
        lines.append("NOTABLE ACHIEVEMENTS:")
        for a in achievements[:3]:
            lines.append(f"  - {a}")

    return "\n".join(lines) if lines else "No structured resume data extracted."


def generate_email(
    profile, recipient, campaign_goal, additional_context, api_key, resume_parsed=None, fallback_keys=(), models=None
):
    """
    Generate a unique cold outreach email for a single recipient.
    """
    domain = recipient["email"].split("@")[1]
    company = (
        recipient.get("company")
        or resolve_company_name(domain)
        or "an unknown company (personal email address — do not guess a company name)"
    )
    recipient_name = recipient.get("name") or "Unknown (use \"Hi there,\")"
    extra_recipient_lines = ""
    if recipient.get("role"):
        extra_recipient_lines += f"\n- Role/Title: {recipient['role']}"
    if recipient.get("notes"):
        extra_recipient_lines += (
            f"\n- Personal notes (use these to make the email specific to this person; "
            f"weave in naturally, don't quote verbatim): {recipient['notes']}"
        )

    resume_block = build_resume_highlights(resume_parsed) if resume_parsed else "Not available."

    user_prompt = f"""Generate a cold outreach email from the following student to the recipient at {company}.

STUDENT PROFILE:
- Name: {profile.get('name', '')}
- College: {profile.get('college', '')}, {profile.get('branch', '')}, {profile.get('year', '')}
- CGPA: {profile.get('cgpa') or 'Not specified'}
- Skills: {profile.get('skills', '')}
- Bio: {profile.get('bio', '')}
- GitHub: {profile.get('github') or 'Not provided'}
- LinkedIn: {profile.get('linkedin') or 'Not provided'}

RESUME HIGHLIGHTS (use to personalise the email — pick 1–2 specific things to naturally mention):
{resume_block}

RECIPIENT:
- Email: {recipient['email']}
- Name: {recipient_name}
- Company: {company}{extra_recipient_lines}

CAMPAIGN GOAL:
{campaign_goal}

ADDITIONAL CONTEXT:
{additional_context or 'None'}

RESUME HIGHLIGHT RULES:
- Mention exactly ONE project or experience from RESUME HIGHLIGHTS — pick the most relevant to this company's domain
- Keep it to one natural sentence (e.g., "I recently built X that does Y using Z" — not a full description)
- The goal is to intrigue them enough to open the resume, not to summarise it
- Do NOT mention multiple projects
- Do NOT copy the project description verbatim — rephrase naturally

EMAIL STRUCTURE RULES:
- Line 1 must be the greeting: "Hi [Name]," or "Hi there,"
- Vary the opening sentence — do not start with the same phrase as other emails
- Vary the closing ask — do not use identical wording across emails
- The email must read like a natural message — not a list of credentials
- Give proper spacing of 1 blank line between paragraphs. Don't dump every information into 1 single paragraph.

SIGN-OFF:
End the email body with exactly this format (on its own lines, after the main content):

Best,
{profile.get('name', '').split()[0] if profile.get('name') else 'Student'}

Return ONLY valid JSON: {{"subject": "...", "body": "..."}}"""

    return _call_groq(
        SYSTEM_PROMPT,
        user_prompt,
        api_key,
        fallback_keys=fallback_keys,
        models=models,
        validator=lambda r: validate_email_output(r, max_words=200),
    )


def generate_contextual_followup(
    profile,
    original_subject,
    original_body,
    recipient_email,
    recipient_name,
    reply_status,
    reply_content,
    check_back_date,
    global_context,
    api_key,
    resume_parsed=None,
    step=1,
    max_steps=1,
    previous_followups=None,
    company=None,
    fallback_keys=(),
    models=None,
):
    """
    Generate a context-aware follow-up email based on the recipient's reply status.
    Each status produces a fundamentally different tone and intent.
    `step` is 1 for the first follow-up, 2 for the second, ...; earlier
    follow-up bodies are passed so the new one doesn't repeat them.
    """

    domain = recipient_email.split("@")[1]
    company = company or resolve_company_name(domain) or "unknown (personal email address)"
    first_name = profile.get('name', '').split()[0] if profile.get('name') else 'Student'

    greeting = f"Hi {recipient_name.split()[0].capitalize()}," if recipient_name else "Hi there,"

    # Build status-specific instruction block
    status_instructions = _build_status_instructions(reply_status, reply_content, check_back_date, company)

    resume_block = build_resume_highlights(resume_parsed) if resume_parsed else "Not available."

    system_prompt = f"""You are helping a college student write a follow-up email for an internship/job outreach.
The follow-up must be tailored precisely to the situation described below — tone, length, and intent
must match the recipient's reply status exactly.

GENERAL RULES:
- Sound completely human — not automated, not templated
- Never start with "I hope this email finds you well"
- Never use "Dear Sir/Madam"
- Use semi-formal tone throughout
- Start with the exact greeting provided
- Never invent availability, dates, hours, locations or achievements that aren't given below
- End with: Best,\\n{first_name}
- Return ONLY valid JSON: {{"subject": "Re: {original_subject}", "body": "..."}}"""

    user_prompt = f"""Write a follow-up email for this specific situation.

SITUATION:
{status_instructions}

ORIGINAL EMAIL:
Subject: {original_subject}
Body: {original_body}
{_sequence_block(step, max_steps, previous_followups)}
STUDENT PROFILE:
- Name: {profile.get('name', '')}
- College: {profile.get('college', '')}, {profile.get('year', '')}
- Skills: {profile.get('skills', '')}
- GitHub: {profile.get('github') or 'Not provided'}

RESUME HIGHLIGHTS (use only if directly relevant):
{resume_block}

RECIPIENT:
- Email: {recipient_email}
- Name: {recipient_name or 'Unknown'}
- Company: {company}

GREETING TO USE (first line, mandatory):
{greeting}

ADDITIONAL CONTEXT FROM USER:
{global_context or 'None provided.'}

SIGN-OFF:
End with exactly:
Best,
{first_name}

Return ONLY valid JSON: {{"subject": "Re: {original_subject}", "body": "..."}}"""

    return _call_groq(
        system_prompt,
        user_prompt,
        api_key,
        fallback_keys=fallback_keys,
        models=models,
        validator=lambda r: validate_email_output(r, max_words=180),
    )


def _sequence_block(step, max_steps, previous_followups):
    """Extra instructions for the 2nd+ follow-up in a thread."""
    if step <= 1 and not previous_followups:
        return ""
    lines = [f"\nTHIS IS FOLLOW-UP #{step} in this thread."]
    for i, body in enumerate(previous_followups or [], start=1):
        lines.append(f"FOLLOW-UP #{i} ALREADY SENT (do NOT repeat its wording, opener or ask):\n{body}")
    if step >= max_steps:
        lines.append(
            "This is the FINAL follow-up: keep it very short (40-70 words), say you won't keep "
            "following up, and leave the door open warmly. No guilt-tripping."
        )
    else:
        lines.append(
            "Keep it shorter than the previous follow-up and take a fresh angle "
            "(e.g. one new relevant detail, or a simpler yes/no question)."
        )
    return "\n".join(lines) + "\n"


def _build_status_instructions(reply_status, reply_content, check_back_date, company):
    """
    Returns a detailed, status-specific instruction block for the AI prompt.
    This is the core logic that makes each follow-up fundamentally different.
    """

    reply_section = f"\nTHEIR ACTUAL REPLY:\n\"\"\"\n{reply_content}\n\"\"\"\n" if reply_content else ""

    if reply_status == "no_reply":
        return f"""STATUS: No Reply — the recipient has not responded to the original email.

TONE & INTENT:
- Brief and non-pushy — 80 to 110 words total
- Acknowledge they are likely busy
- Do not repeat the full pitch from the original email
- A single soft nudge — "wanted to make sure this didn't get buried"
- End with a low-pressure, open-ended question
- Do NOT sound desperate or apologetic{reply_section}"""

    elif reply_status == "check_back":
        date_line = f"- They asked you to follow up around: {check_back_date}" if check_back_date else ""
        return f"""STATUS: Check Back — the recipient replied and asked you to reach out again later.

TONE & INTENT:
- Warm and conversational — you already have a micro-relationship since they replied
- Open by referencing THEIR suggestion to follow up (e.g., "You had mentioned reaching out again — following up as suggested")
- Do NOT re-introduce yourself fully — they remember you
- Keep it brief — 90 to 130 words
- If the student's availability is mentioned in the context below, restate it in one line
- End with a soft, specific ask — a quick call, a 15-min chat, etc.
{date_line}{reply_section}"""

    elif reply_status == "interested":
        return f"""STATUS: Interested — the recipient replied positively and wants more information or a call.

TONE & INTENT:
- Warm, confident, and direct — do not undersell yourself
- Directly address whatever they asked or expressed interest in
- If they asked for GitHub/portfolio, acknowledge that naturally
- Propose a concrete next step — suggest a specific timeframe for a call or meeting
- 100 to 150 words
- This is NOT a nudge — it is an advancement email. Move the conversation forward.{reply_section}"""

    elif reply_status == "no_openings":
        return f"""STATUS: No Openings — the recipient explicitly said there are no positions available right now.

TONE & INTENT:
- Short, gracious, zero pressure — 60 to 90 words
- Thank them genuinely for taking the time to reply (most people don't)
- Express that you understand completely
- Ask warmly if they could keep your profile in mind for future openings
- OR ask if they know anyone else in their network who might be looking for interns
- Leave a strongly positive final impression — these people move companies and open roles later
- Do NOT sound disappointed or pushy{reply_section}"""

    else:
        # Fallback for any unexpected status
        return f"""STATUS: General follow-up.
TONE: Semi-formal, brief, warm. 80 to 120 words.{reply_section}"""


class RateLimitExhausted(Exception):
    """Every model in the fallback chain is rate-limited on every configured key."""


def _is_rate_limit(err_str):
    return any(
        k in err_str
        for k in (
            "TOKENS PER DAY",
            "REQUESTS PER DAY",
            "TOKENS PER MINUTE",
            "RATE_LIMIT",
            "RATE LIMIT",
            "429",
        )
    )


# Phrases the prompts forbid; a draft containing one is regenerated
BANNED_PHRASES = (
    "i hope this email finds you well",
    "i am writing to express my interest",
    "i would be honored",
    "i am a passionate individual",
    "i believe my skills could be a great fit",
    "dear sir/madam",
    "dear sir or madam",
    "bringing to the table",
    "cutting-edge",
    "synergy",
)

# Unfilled template placeholders such as "[Name]" or "[Company Name]"
_PLACEHOLDER_RE = re.compile(r"\[(?:[^\]]*\b(?:name|company|your|recipient|insert|position|role)\b[^\]]*)\]", re.I)


def validate_email_output(result, max_words):
    """Return a description of the first quality problem in a generated email, or None."""
    subject, body = result.get("subject", ""), result.get("body", "")
    if not str(subject).strip() or not str(body).strip():
        return "empty subject or body"
    text = f"{subject}\n{body}"
    match = _PLACEHOLDER_RE.search(text)
    if match:
        return f"unfilled placeholder {match.group(0)}"
    lowered = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in lowered:
            return f"banned phrase '{phrase}'"
    words = len(str(body).split())
    if words > max_words:
        return f"too long ({words} words)"
    return None


# (api_key, model) -> monotonic time until which it's known to be rate-limited.
# Shared by all worker threads so they stop hammering a limited key.
_cooldowns = {}
_cooldowns_lock = threading.Lock()
MINUTE_COOLDOWN = 60
DAY_COOLDOWN = 60 * 60


def _cooling_down(key, model):
    with _cooldowns_lock:
        return _cooldowns.get((key, model), 0) > time.monotonic()


def _start_cooldown(key, model, err_str):
    seconds = DAY_COOLDOWN if "PER DAY" in err_str else MINUTE_COOLDOWN
    with _cooldowns_lock:
        _cooldowns[(key, model)] = time.monotonic() + seconds


def _call_groq(system_prompt, user_prompt, api_key, retries=4, validator=None, fallback_keys=(), models=None):
    """
    Call the Groq API and parse the JSON response.
    - Bad/malformed JSON or a draft failing `validator`: retry (up to `retries` attempts)
    - 503/overloaded: exponential backoff, then retry
    - Rate limit: try the same model on each of the other keys first, then fall
      back to the next model. Limited keys are skipped by every thread for a
      cooldown period. If everything is limited, raise RateLimitExhausted.
    - Model retired / not available: skip to the next model. If no model is
      usable, raise NoModelAvailable with a hint to update Settings.
    """
    models = models or DEFAULT_TEXT_MODELS
    keys = [api_key] + [k for k in fallback_keys if k and k != api_key]
    last_error = None
    unavailable = []

    for model in models:
        model_gone = False
        for key in keys:
            if model_gone:
                break
            if _cooling_down(key, model):
                last_error = last_error or f"{model} rate-limited on all keys"
                continue
            client = Groq(api_key=key)
            for attempt in range(retries):
                try:
                    # Add random seed variation for uniqueness
                    seed_note = f"\n[Variation seed: {random.randint(1000, 9999)}]"

                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt + seed_note},
                        ],
                        temperature=0.9,
                        response_format={"type": "json_object"},
                        **model_kwargs(model),
                    )

                    text = response.choices[0].message.content.strip()
                    result = json.loads(text)

                    if "subject" not in result or "body" not in result:
                        raise ValueError("Response missing 'subject' or 'body' keys")

                    # Quality check: regenerate drafts that break the rules, but on the
                    # last attempt accept the result rather than failing the recipient.
                    issue = validator(result) if validator else None
                    if issue and attempt < retries - 1:
                        print(f"Regenerating draft ({issue})")
                        last_error = issue
                        continue

                    return result

                except (json.JSONDecodeError, ValueError) as e:
                    last_error = e
                    continue

                except Exception as e:
                    last_error = e
                    err_str = str(e).upper()

                    if is_model_unavailable(err_str):
                        print(f"Model {model} is unavailable (retired?); trying the next model...")
                        unavailable.append(model)
                        model_gone = True
                        break  # next model — no key can use it
                    elif _is_rate_limit(err_str):
                        _start_cooldown(key, model, err_str)
                        print(f"Rate limit hit on {model} for key ...{key[-4:]}; trying another key/model...")
                        break  # next key (then next model)
                    elif "503" in err_str or "UNAVAILABLE" in err_str or "OVERLOADED" in err_str:
                        wait = 5 * (2**attempt)  # 5s, 10s, 20s, 40s
                        print(
                            f"Groq API error ({e}) on model {model} — retrying in {wait}s (attempt {attempt + 1}/{retries})"
                        )
                        time.sleep(wait)
                    else:
                        raise Exception(f"Groq API error: {e}") from e
            else:
                # Retries exhausted on this key/model without a rate limit
                raise Exception(f"Groq request failed after {retries} attempts on {model}. Last error: {last_error}")

    if len(unavailable) == len(models):
        raise NoModelAvailable(unavailable_message(models))
    raise RateLimitExhausted(f"All keys and models are rate-limited: {last_error}")
