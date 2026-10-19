"""Direção expressiva compartilhada entre motores de voz do AGCN.

O Presenter decide O QUE dizer. Esta camada decide COMO dizer, usando apenas
metadados já aprovados (tipo de fala, intenção, tópico e tática comercial).
Ela não altera o texto nem cria fatos.
"""

from __future__ import annotations

from typing import Any


STYLE_LIBRARY = {
    "neutral": (
        "Neutral, balanced Brazilian Portuguese delivery. Clear articulation, "
        "natural conversational pace, restrained pitch variation, no forced "
        "excitement and no theatrical emphasis."
    ),
    "sales_energy": (
        "Energetic and natural live-commerce delivery. Bright, confident, "
        "engaging, quick rhythm, crisp articulation and short pauses. "
        "Sound spontaneous, not like reading a script."
    ),
    "celebratory": (
        "Sound genuinely happy and celebratory, with a smile in the voice. "
        "Keep it quick and natural; do not overact."
    ),
    "excited": (
        "Sound visibly excited and enthusiastic, with bright pitch movement "
        "and lively energy. Keep diction clear and believable."
    ),
    "suspense_reveal": (
        "Lower the energy briefly and create suspense, almost like a controlled "
        "whisper, then lift the energy on the reveal. Stay intelligible."
    ),
    "urgent_grounded": (
        "Create controlled urgency and momentum. Slightly increase intensity "
        "and pace, with confident emphasis. Never sound panicked or theatrical."
    ),
    "price_confident": (
        "Deliver price and value with confident emphasis and a persuasive "
        "sales rhythm. Make the value feel clear without shouting."
    ),
    "reassuring": (
        "Warm, calm and reassuring. Slow down slightly on the key reassurance, "
        "then return to an energetic live-commerce rhythm."
    ),
    "empathetic_solution": (
        "Start with an empathetic, understanding tone, then become brighter "
        "and more confident when presenting the solution or benefit."
    ),
    "vivid_desire": (
        "Warm, vivid and inviting. Make the listener imagine using the product. "
        "Use expressive intonation while keeping a natural sales rhythm."
    ),
    "clear_answer": (
        "Answer directly and clearly with confident, friendly delivery. "
        "Use a short natural pause after the answer and avoid sounding robotic."
    ),
    "welcoming": (
        "Welcoming, upbeat and friendly, as if greeting people who just joined "
        "the live. Keep the energy high and conversational."
    ),
}


def infer_voice_style(metadata: dict[str, Any] | None) -> str:
    data = dict(metadata or {})
    explicit = str(data.get("voice_style") or "").strip().casefold()
    if explicit in STYLE_LIBRARY:
        return explicit

    intent = str(data.get("intent") or "").strip().casefold()
    topic = str(data.get("topic") or "").strip().casefold()
    tactic = str(data.get("tactic") or "").strip().casefold()

    if intent == "purchase_confirmation" or tactic == "social_proof" or topic == "social_proof":
        return "celebratory"

    if tactic == "grounded_scarcity" or topic == "scarcity":
        return "urgent_grounded"

    if tactic == "price_anchor" or topic == "price_value" or intent == "price":
        return "price_confident"

    if intent == "objection" or tactic in {"risk_reversal", "objection_preempt"} or topic == "trust":
        return "reassuring"

    if tactic == "pain_relief" or topic == "pain_solution":
        return "empathetic_solution"

    if tactic in {"desire_visualization", "use_case"} or topic in {"description", "usage"}:
        return "vivid_desire"

    if topic in {"newcomer_recap", "product_recap"}:
        return "welcoming"

    if str(data.get("type") or "").casefold() == "reactive":
        return "clear_answer"

    return "sales_energy"


def build_voice_instructions(
    base_instructions: str,
    metadata: dict[str, Any] | None,
    *,
    strength: float = 1.0,
) -> tuple[str, str]:
    """Retorna (style_id, instrução final) para o motor expressivo."""
    style_id = infer_voice_style(metadata)
    style = STYLE_LIBRARY[style_id]
    strength = max(0.0, min(1.5, float(strength)))

    if strength <= 0.05:
        return style_id, str(base_instructions or "").strip()

    intensity = (
        "Use the style subtly."
        if strength < 0.65
        else "Use the style clearly but naturally."
        if strength < 1.15
        else "Use the style strongly while staying believable and human."
    )

    parts = [
        str(base_instructions or "").strip(),
        style,
        intensity,
        (
            "Brazilian Portuguese. Preserve the exact meaning and wording of "
            "the supplied speech. Do not add new facts or extra sentences."
        ),
    ]
    return style_id, " ".join(part for part in parts if part)
