"""Prompt templates for job-post generation."""
from __future__ import annotations

from urllib.parse import urlparse

from ..models import Lead

# ---------------------------------------------------------------------------
# Known aggregator board display names (keyed by domain suffix)
# ---------------------------------------------------------------------------

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
    """Human-readable name of the source platform for aggregator link text."""
    netloc = urlparse(lead.apply_url).netloc.lower().removeprefix("www.")
    for domain, name in _BOARD_NAMES.items():
        if netloc == domain or netloc.endswith("." + domain):
            return name
    # Fallback: capitalise the second-level domain label
    label = netloc.split(".")[-2] if "." in netloc else netloc
    return label.title() or "the job board"


SYSTEM_PROMPT = (
    "You write job-lead blog posts for The WFH Connect (thewfhconnect.com), a site "
    "that helps a scam-wary audience find legitimate work-from-home jobs.\n\n"
    "VOICE\n"
    "- Trustworthy, plain-spoken, zero hype. You are a helpful friend who screens "
    "job leads, not a marketer.\n"
    "- NEVER promise or imply guaranteed income, easy money, or fast riches. Avoid "
    'phrases like "amazing opportunity", "unlimited earnings", "life-changing".\n'
    "- Be candid about unknowns: if pay or schedule isn't published, say so and "
    "tell readers to confirm with the employer.\n"
    "- Readers worry about scams. Reinforce practical trust signals: apply only via "
    "the official link, never pay to apply, guard personal data.\n"
    "- Only state facts found in the job lead. Do not invent duties, benefits, or pay.\n\n"
    "HTML RULES (for body_html)\n"
    "- Clean semantic HTML only: <h2>/<h3> headings, short <p> paragraphs of 2-4 "
    "sentences, <ul><li> lists, <strong>/<em> sparingly. No <h1>, no inline styles, "
    "no <script> tags, no markdown syntax.\n"
    "- Include a bulleted requirements list under its own <h2> heading.\n"
    "- Include exactly one apply link, inside a 'How to apply' section, using the "
    "exact anchor tag provided in LINK FRAMING below. Do not alter its text or href.\n"
    "- Do NOT write affiliate links, disclosures, disclaimers, or JSON-LD. Those "
    "are appended automatically after you.\n\n"
    "SEO\n"
    "- Target ONE realistic long-tail keyword a job seeker would actually type "
    '(e.g. "remote chat support jobs no phone"), not a head term.\n'
    "- seo_title: under 60 characters, contains the keyword or a close variant, no clickbait.\n"
    "- meta_description: under 155 characters, plain and factual, contains the keyword.\n"
    "- slug: lowercase-hyphenated, derived from the keyword/title.\n"
    "- Use the keyword naturally in the first paragraph and one heading. Never stuff.\n\n"
    "OUTPUT\n"
    "Return ONLY a valid JSON object -- no markdown fences, no commentary -- with "
    'exactly these keys: "seo_title", "slug", "meta_description", "focus_keyword", '
    '"body_html", "excerpt". '
    '"excerpt" is 1-2 plain-text sentences for archive pages. Escape quotes and '
    "newlines so every value is valid JSON."
)


def apply_anchor_html(lead: Lead) -> str:
    """The exact apply-link markup the model is instructed to embed verbatim.

    The anchor text is chosen deterministically based on lead.is_direct:

    * is_direct=True  -> "Apply directly on their site"
    * is_direct=False -> "View this listing on <Source Board>"

    This ensures the post never claims a direct employer link when the URL
    actually points to a third-party aggregator.
    """
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


def build_user_prompt(lead: Lead) -> str:
    requirements = "\n".join(f"- {item}" for item in lead.requirements) or "- (none listed)"
    pay = lead.pay or "Not published -- tell readers to confirm pay before investing time"
    anchor = apply_anchor_html(lead)

    if lead.is_direct:
        link_framing_note = (
            "LINK TYPE: Direct employer link -- readers can apply without leaving this "
            "employer's own site.  Use the anchor below as-is and write surrounding "
            'copy that says "apply directly" or similar.'
        )
    else:
        board = _source_display_name(lead)
        link_framing_note = (
            f"LINK TYPE: Aggregator listing on {board} -- this link goes to a job board, "
            "NOT the employer's own site.  Use the anchor below as-is and write surrounding "
            f'copy that says "view the listing on {board}" -- do NOT say "apply directly".'
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
        f"Apply URL: {lead.apply_url}\n"
        f"Requirements:\n{requirements}\n"
        f"Description:\n{lead.description or '(no description provided -- keep the post short and factual)'}\n\n"
        f"LINK FRAMING\n{link_framing_note}\n"
        "Use this exact anchor in the 'How to apply' section -- copy it character-for-character:\n"
        f"{anchor}\n\n"
        "STRUCTURE\n"
        "- Open with 2-3 sentences naming the role, the company, and the long-tail keyword (no heading first).\n"
        "- <h2> about the role / what you'll actually do (from the description only).\n"
        "- <h2> requirements, rendered as a <ul> list.\n"
        "- <h2> pay and schedule -- facts from the lead only; clearly flag anything unstated.\n"
        "- <h2> how to apply -- 2-3 short steps, then the exact anchor from LINK FRAMING on its own line.\n"
        "- Close with one short, practical scam-awareness tip relevant to this kind of role.\n\n"
        "Remember: return ONLY the JSON object described in your instructions."
    )
