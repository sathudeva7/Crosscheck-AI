"""Put a finding into words a patient can read.

The model is never asked whether there is a problem. By the time anything here
runs, the database has already decided: a row in `interaction_rule` matched, or
two ATC codes shared a family. The model is handed that finished fact and asked
to say what it means in two or three sentences.

Keeping the two apart is the whole safety argument. A model asked 'are these
drugs safe together?' can invent a reassuring answer, and there is no way to
tell that it has. A model asked to explain a specific pair from a specific
source can only get the wording wrong, and the finding stands regardless.

Which is also why every type has a template. If the model is unavailable, the
warning still appears — it just reads more flatly. A safety warning that
depends on an API call succeeding is not a safety warning.
"""

import json
import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from app.checks.base import Candidate
from app.config import EXPLAIN_MODEL, MODEL_RETRIES, MODEL_TIMEOUT

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You explain a medical finding to a patient in plain English.

The finding has already been established from a drug database. Your job is only
to put it into words. Do not question it, and do not add findings of your own.

Write two or three short sentences:
1. What has been noticed, in this patient's own words and numbers — the doses,
   the dates, the readings the evidence gives you.
2. Why it matters, using only what is in the evidence: how the pair was graded,
   the family two medicines share, how far a reading sits outside its range.
3. What to do: speak to a doctor or pharmacist before changing anything.

Rules:
- Never tell the patient to stop, start or change a dose. Only a clinician can.
- Never diagnose. Describe what was found, not what is wrong with them.
- Do not state anything the evidence does not contain. If it says the overlap
  was assumed, say that the assumption was made.
- Do not explain how a medicine works in the body, or what an interaction does
  physiologically. The record does not say, and filling it in from your own
  knowledge is how this stops being about the patient reading it. 'These are
  graded a major interaction' is the evidence; 'warfarin thins the blood and
  ibuprofen irritates the stomach lining' is not.
- Use the regimens you are given. Name the dose and the date. A sentence that
  would fit any patient taking these drugs tells this one nothing they could
  not have read on the packet.
- Write dates the way a person says them: '12 March 2024', not '2024-03-12'.
  The evidence is stored for a machine; this is being read by someone worried.
- Never print our own machinery: not the severity word ('danger', 'warning'),
  not an ATC or RxCUI code, not a field name. Say 'the same family of
  antibiotics', not 'shared family code J01C'. The grade a source gives an
  interaction — 'major' from DDInter — is a fact about the pair and may be
  used; 'danger' is our internal colour for it and may not.
- No jargon unless you immediately explain it. Say 'a medicine that slows the
  heart' rather than 'a beta blocker' alone.
- Be calm. This is information, not an alarm.
- Do not repeat the title back; add to it.
"""

TEMPLATES = {
    "interaction": (
        "These two medicines are listed as interacting with each other. "
        "Interactions can change how well a medicine works or make side effects "
        "more likely. Please mention both to your doctor or pharmacist before "
        "making any change."
    ),
    "duplicate_therapy": (
        "These two medicines belong to the same family and do a similar job. "
        "Taking both can have a stronger effect than intended. This may well be "
        "deliberate — please check with your doctor or pharmacist."
    ),
    "allergy_conflict": (
        "This medicine is related to something recorded as an allergy in your "
        "records. Related medicines can sometimes cause the same reaction. "
        "Please tell your doctor or pharmacist about this allergy before taking it."
    ),
    "dosage_conflict": (
        "This medicine appears in your records at two different strengths around "
        "the same time. That is often a planned change, but it is worth checking "
        "which one you should be taking."
    ),
    "lab_trend": (
        "This test result has been moving in one direction across several tests "
        "and is now outside the usual range. A trend matters more than any single "
        "reading. Please discuss it with your doctor."
    ),
    "critical_value": (
        "This result is a long way outside the usual range. Please contact your "
        "doctor about it rather than waiting for your next appointment."
    ),
}

DISCLAIMER = (
    "This is not a diagnosis. Always check with a doctor or pharmacist before "
    "changing any medicine."
)

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = init_chat_model(
            EXPLAIN_MODEL,
            temperature=0,
            timeout=MODEL_TIMEOUT,
            max_retries=MODEL_RETRIES,
        )
    return _model


def template_for(candidate: Candidate) -> str:
    """The wording used when no model is available."""
    text = TEMPLATES.get(candidate.finding_type, DISCLAIMER)
    assumption = (candidate.evidence or {}).get("assumption")
    return f"{text} {assumption}" if assumption else text


def _facts(evidence: dict) -> str:
    """The evidence as lines the model can read.

    Nested values go out as JSON rather than as Python's own repr: a regimen
    is a small object, and `{'drug': 'warfarin'}` with its single quotes is one
    more thing to be misread. Empty values are dropped — 'assumption: None' is
    a line that says nothing and invites the model to explain the nothing.
    """
    lines = []
    for key, value in (evidence or {}).items():
        if value is None or value == [] or value == {}:
            continue
        rendered = (
            json.dumps(value, ensure_ascii=False)
            if isinstance(value, (dict, list))
            else value
        )
        lines.append(f"- {key}: {rendered}")
    return "\n".join(lines)


async def explain(candidate: Candidate) -> tuple[str, str]:
    """Write the explanation for one finding. Returns (text, source)."""
    facts = _facts(candidate.evidence)
    prompt = (
        f"Finding: {candidate.title}\n"
        f"Type: {candidate.finding_type}\n"
        f"Severity: {candidate.severity}\n\n"
        f"Established facts:\n{facts}"
    )

    try:
        response = await _get_model().ainvoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
        )
        text = (response.content or "").strip()
        if text:
            return text, "model"
    except Exception as exc:  # noqa: BLE001 - provider errors vary widely
        logger.warning("could not explain %s: %s", candidate.key, exc)

    return template_for(candidate), "template"
