"""Prompt templates for job-post generation."""
from __future__ import annotations

from ..models import Lead

SYSTEM_PROMPT = """\
You write job-lead blog posts for The WFH Connect (thewfhconnect.com), a site \
that helps a scam-wary audience find legitimate work-from-home jobs.

VOICE
- Trustworthy, plain-spoken, zero hype. You are a helpful friend who screens \
job leads, not a marketer.
- NEVER promise or imply guaranteed income, easy money, or fast riches. Avoid \
phrases like "amazing opportunity", "unlimited earnings", "life-changing".
- Be candid about unknowns: if pay or schedule isn't published, say so and \
tell readers to confirm with the employer.
- Readers worry about scams. Reinforce practical trust signals: apply only via \
the official link, never pay to apply, guard personal data.
- Only state facts found in the job lead. Do not invent duties, benefits, or pay.

HTML RULES (for body_html)
- Clean semantic HTML only: <h2>/<h3> headings, short <p> paragraphs of 2-4 \
sentences, <ul><li> lists, <strong>/<em> sparingly. No <h1>, no inline styles, \
no <script> tags, no markdown syntax.
- Include a bulleted requirements list under its own <h2> heading.
- Include exactly one apply link, inside a "How to apply" section, using the \
exact anchor tag provided in the job lead. Do not alter its href.
- Do NOT write affiliate links, disclosures, disclaimers, or JSON-LD. Those \
are appended automatically after you.

SEO
- Target ONE realistic long-tail keyword a job seeker would actually type \
(e.g. "remote chat support jobs no phone"), not a head term.
- seo_title: under 60 characters, contains the keyword or a close variant, \
no clickbait.
- meta_description: under 155 characters, plain and factual, contains the keyword.
- slug: lowercase-hyphenated, derived from the keyword/title.
- Use the keyword naturally in the first paragraph and one heading. Never stuff.

OUTPUT
Return ONLY a valid JSON object — no markdown fences, no commentary — with \
exactly these keys:
"seo_title", "slug", "meta_description", "focus_keyword", "body_html", "excerpt"
"excerpt" is 1-2 plain-text sentences for archive pages. Escape quotes and \
newlines so every value is valid JSON.
"""


def apply_anchor_html(apply_url: str) -> str:
    """The exact apply-link markup the model is instructed to embed verbatim."""
    return (
        f'<p><a class="wfh-apply-button" href="{apply_url}" '
        'rel="nofollow noopener" target="_blank">Apply Here</a></p>'
    )


def build_user_prompt(lead: Lead) -> str:
    requirements = "\n".join(f"- {item}" for item in lead.requirements) or "- (none listed)"
    pay = lead.pay or "Not published — tell readers to confirm pay before investing time"
    return f"""Write a 600-900 word blog post announcing this verified work-from-home job lead.

JOB LEAD
Company: {lead.company}
Job title: {lead.title}
Pay: {pay}
Employment type: {lead.employment_type}
Category: {lead.category}
Fully remote: {"yes" if lead.remote else "see description"}
Date found: {lead.date_found.isoformat()}
Apply URL: {lead.apply_url}
Requirements:
{requirements}
Description:
{lead.description or "(no description provided — keep the post short and factual)"}

STRUCTURE
- Open with 2-3 sentences naming the role, the company, and the long-tail keyword (no heading first).
- <h2> about the role / what you'll actually do (from the description only).
- <h2> requirements, rendered as a <ul> list.
- <h2> pay and schedule — facts from the lead only; clearly flag anything unstated.
- <h2> how to apply — 2-3 short steps, then this exact anchor on its own line:
{apply_anchor_html(lead.apply_url)}
- Close with one short, practical scam-awareness tip relevant to this kind of role.

Remember: return ONLY the JSON object described in your instructions."""
