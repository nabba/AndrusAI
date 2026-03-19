"""
vetting.py — Quality gate for local LLM output before delivery.

Only vets LOCAL Ollama output. Skips vetting for API-tier models (budget/mid/premium)
since they are already frontier quality. Default vetting model is Sonnet 4.6.
"""

import logging
from crewai import Agent, Task, Crew, Process
from app.config import get_settings
from app.llm_factory import create_vetting_llm, is_using_local, is_using_api_tier, get_last_model, get_last_tier

logger = logging.getLogger(__name__)

VETTING_PROMPTS = {
    "coding": """\
You are a senior software architect reviewing code from a local AI model.

1. CORRECTNESS: Does the code do what was asked? Logic bugs?
2. SECURITY: Injection, path traversal, unsafe operations?
3. BEST PRACTICES: Clean code, error handling, no anti-patterns?
4. COMPLETENESS: Does it fully solve the task?

If the code is good, return it with minimal changes.
If it has bugs, FIX THEM and return corrected code.
If it's poor quality, REWRITE it properly.
DO NOT add disclaimers. Return the final clean response.

USER REQUEST:
{request}

LOCAL MODEL OUTPUT:
{response}

Return the vetted response only.
""",
    "research": """\
You are a fact-checker reviewing research from a local AI model.

1. ACCURACY: Flag anything factually wrong or unverifiable
2. SOURCES: Remove hallucinated URLs
3. COMPLETENESS: Does it answer what was asked?
4. FORMAT: Clean up for Signal (concise, structured, under 1500 chars)

USER REQUEST:
{request}

LOCAL MODEL RESEARCH:
{response}

Return the vetted response only — no meta-commentary.
""",
    "writing": """\
You are an editor reviewing content from a local AI model.

1. QUALITY: Clear, professional language?
2. ACCURACY: Factual claims correct?
3. COMPLETENESS: Covers what was requested?
4. FORMAT: Appropriate for Signal (concise, well-structured, under 1500 chars)

USER REQUEST:
{request}

LOCAL MODEL CONTENT:
{response}

Return the polished version only — no disclaimers.
""",
}

DEFAULT_VETTING_PROMPT = """\
You are a quality reviewer. A local AI model produced this response.
Check for accuracy, completeness, and formatting. Fix any issues.
Return the clean response only — no disclaimers. Under 1500 chars for Signal.

USER REQUEST:
{request}

LOCAL MODEL RESPONSE:
{response}
"""


def vet_response(user_request: str, local_response: str, crew_name: str) -> str:
    """
    Pass local LLM output through Claude for quality assurance.
    Only vets LOCAL Ollama output. Skips for API-tier and premium models.
    """
    settings = get_settings()
    model_name = get_last_model() or "unknown"
    tier = get_last_tier() or "unknown"

    if not settings.vetting_enabled:
        return local_response

    # Skip for API-tier and premium — already frontier quality
    if is_using_api_tier():
        logger.debug(f"vetting: skipping for API-tier {model_name} (tier={tier})")
        return local_response

    if not is_using_local():
        return local_response

    if not local_response or len(local_response.strip()) < 10:
        return local_response

    try:
        llm = create_vetting_llm()
        prompt_template = VETTING_PROMPTS.get(crew_name, DEFAULT_VETTING_PROMPT)

        agent = Agent(
            role="Quality Reviewer",
            goal="Ensure response quality, accuracy, and security before delivery.",
            backstory=(
                "You are the final quality gate. You review output from local AI "
                "models before it reaches the user. You catch bugs, hallucinations, "
                "and security issues. Be concise — the output goes to Signal."
            ),
            llm=llm, tools=[], verbose=False,
        )

        task = Task(
            description=prompt_template.format(
                request=user_request[:800],
                response=local_response[:6000],
            ),
            expected_output="A clean, vetted response ready for the user.",
            agent=agent,
        )

        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
        vetted = str(crew.kickoff()).strip()
        if vetted and len(vetted) > 20:
            logger.info(f"vetting: {crew_name} vetted ({len(local_response)}→{len(vetted)} chars, source: {model_name})")
            return vetted

    except Exception as exc:
        logger.warning(f"vetting: failed ({exc}), returning unvetted response")

    return local_response
