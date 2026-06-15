"""Prompt templates for job-post generation."""
from __future__ import annotations

from urllib.parse import urlparse

from ..models import Lead

_BOARD_NAMES: dict[str, str] = {
    "remoteok.com": "RemoteOK",
    "weworkremotely.com": "We Work Remotely",
    "remotive.com": "Remotive",
    "himalayas.app": "Himalayas",
    "linkedin.com": "LinkedIn",
    "indeed.com": "Indeed",
    "flexjobs.com": "FlexJobs",
    "remote.co": "Remote.co",
    "glassdoor.com": "Glassdoor",
    "monster.com": "Monster",
    "ziprecruiter.com": "ZipRecruiter",
    "jooble.org": "Jooble",
    "talroo.com": "Talroo",
    "appcast.io": "Appcast",
}


def _source_display_name(lead: Lead) -> str:
    netloc = urlparse(lead.apply_url).netloc.lower().removeprefix("www.")
    for domain, name in _BOARD_NAMES.items():
        if netloc == domain or netloc.endswith("." + domain):
            return name
    label = netloc.split(".")[-2] if "." in netloc else netloc
    return label.title() or "the job board"


SYSTEM_PROMPT = (
    "You write job-lead blog posts for The WFH Connect (thewfhconnect.com), a site "
    "that helps a scam-wary audience find legitimate work-from-home jobs.\n\n"
    "VOICE\n"
    "- Trustworthy, plain-spoken, zero hype. You are a helpful friend who screens "
    "job leads, not a marketer.\n"
    "- NEVER promise or imply guaranteed income, easy money, or fast riches.\n"
    "- Be candid about unknowns: if pay or schedule isn't published, say so.\n"
    "- Readers worry about scams. Reinforce practical trust signals: apply only via "
    "the official link, never pay to apply, guard personal data.\n"
    "- Only state facts found in the job lead. Do not invent duties, benefits, or pay.\n\n"
    "HTML RULES (for body_html)\n"
    "- Clean semantic HTML only: <h2>/<h3>, short <p> paragraphs, <ul><li> lists, "
    "<strong>/<em> sparingly. No <h1>, no inline styles, no <script>, no markdown.\n"
    "- Include a bulleted requirements list under its own <h2> heading. "
    "If requirements or qualifications are present in the description, list them. "
    "Only write 'no requirements were listed' when the DESCRIPTION field is also "
    "empty — never omit requirements that are clearly stated in the description.\n"
    "- Include exactly one apply link in the 'How to apply' section using the "
    "exact anchor tag from LINK FRAMING. Do not alter its text or href.\n"
    "- Do NOT write affiliate links, disclaimers, or JSON-LD.\n\n"
    "SEO\n"
    "- Target ONE realistic long-tail keyword a job seeker would type.\n"
    "- seo_title: under 60 chars, contains keyword, no clickbait.\n"
    "- meta_description: under 155 chars, plain and factual.\n"
    "- slug: lowercase-hyphenated.\n\n"
    "OUTPUT\n"
    "Return ONLY a valid JSON object with exactly these keys: "
    '"seo_title", "slug", "meta_description", "focus_keyword", "body_html", "excerpt". '
    "No markdown fences. No commentary."
)


def apply_anchor_html(lead: Lead) -> str:
    """Deterministic apply-link markup based on lead.is_direct."""
    if lead.is_direct:
        link_text = "Apply directly on their site"
    else:
        board = _source_display_name(lead)
        link_text = f"View this listing on {board}"
    return (
        '<p><a class="wfh-apply-button" href="'
        + lead.apply_url
        + '" rel="nofollow noopener" target="_blank">'
        + link_text
        + "</a></p>"
    )


def _format_requirements(lead: Lead) -> str:
    """Build the requirements block for the prompt.

    Priority:
    1. Use lead.requirements list if populated (pre-parsed bullet items).
    2. If list is empty but description is non-empty, instruct the LLM to
       extract requirements from the description — never falsely claim none exist.
    3. Only claim "none listed" when BOTH requirements and description are empty.
    """
    if lead.requirements:
        return "\n".join(f"- {item}" for item in lead.requirements)
    if lead.description.strip():
        return (
            "- (The requirements are embedded in the description below. "
            "Extract and list them as bullet points — do NOT write "
            "'no requirements were listed'.)"
        )
    return "- (none listed in this posting — state that honestly in the post)"


def build_user_prompt(lead: Lead) -> str:
    requirements = _format_requirements(lead)
    pay = lead.pay or "Not published — tell readers to confirm pay before investing time"
    anchor = apply_anchor_html(lead)

    if lead.is_direct:
        link_framing_note = (
            "LINK TYPE: Direct employer link. Write copy that says 'apply directly' or similar."
        )
    else:
        board = _source_display_name(lead)
        link_framing_note = (
            f"LINK TYPE: Aggregator listing on {board}. "
            f"Write copy that says 'view the listing on {board}' — do NOT say 'apply directly'."
        )

    return (
        "Write a 600-900 word blog post announcing this verified work-from-home job lead.\n\n"
        "JOB LEAD\n"
        f"Company: {lead.company}\n"
        f"Job title: {lead.title}\n"
        f"Pay: {pay}\n"
        f"Employment type: {lead.employment_type}\n"
        f"Category: {lead.category}\n"
        f'Fully remote: {"yes" if lead.remote else "see description"}\n'
        f"Date found: {lead.date_found.isoformat()}\n"
        f"Requirements:\n{requirements}\n"
        f"Description:\n{lead.description or '(no description provided — keep the post short and factual)'}\n\n"
        f"LINK FRAMING\n{link_framing_note}\n"
        "Use this exact anchor in the 'How to apply' section:\n"
        f"{anchor}\n\n"
        "STRUCTURE\n"
        "- Open with 2-3 sentences naming the role, company, and long-tail keyword.\n"
        "- <h2> about the role / what you'll do (from description only).\n"
        "- <h2> Requirements — list ALL requirements found in the description as <ul><li> items. "
        "Only omit this section if no requirements appear anywhere in the description.\n"
        "- <h2> Pay and schedule — facts only; flag anything unstated.\n"
        "- <h2> How to apply — 2-3 steps, then the exact anchor on its own line.\n"
        "- Close with one practical scam-awareness tip.\n\n"
        "Return ONLY the JSON object."
    )
