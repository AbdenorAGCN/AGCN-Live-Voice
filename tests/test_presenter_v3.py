from core.integration_contracts import BrainResult
from core.presenter_v3 import PresenterV3


class FakeBrain:
    name = "fake-brain"

    def __init__(self):
        self.contexts = []

    def healthcheck(self):
        return True, "ok"

    def generate(self, context):
        self.contexts.append(context)
        if context.mode == "comment_reply":
            price_fact = next(
                x for x in context.allowed_facts
                if x.startswith("preço atual:")
            )
            return BrainResult(
                speech="Maria, está R$ 199,90 agora. E eu já continuo te mostrando.",
                topic="price",
                used_facts=[price_fact],
                needs_fact=False,
                next_sales_thread="benefits",
            )
        benefit = next(
            x for x in context.allowed_facts
            if x.startswith("benefícios:")
        )
        return BrainResult(
            speech="Olha esse ponto: tela nítida e bateria duradoura.",
            topic="benefits",
            used_facts=[benefit],
            needs_fact=False,
            next_sales_thread="usage",
        )


def product():
    return {
        "name": "Smart Band X",
        "description": "Pulseira inteligente.",
        "key_benefits": "Tela nítida e bateria duradoura",
        "current_price": 199.90,
        "regular_price": 249.90,
    }


def test_comment_goes_through_decision_plan_context_and_brain():
    brain = FakeBrain()
    presenter = PresenterV3(product(), brain)

    result = presenter.test_comment("Maria", "quanto custa?")
    assert result["ok"] is True
    assert result["speech"]["speech"].startswith("Maria")
    assert result["speech"]["used_facts"] == ["preço atual: R$ 199,90"]

    context = brain.contexts[-1]
    assert context.mode == "comment_reply"
    assert context.comment.username == "Maria"
    assert context.decision["intent"] == "price"
    assert context.live_conditions["current_price"] == 199.90


def test_low_value_engagement_is_ignored_without_brain_call():
    brain = FakeBrain()
    presenter = PresenterV3(product(), brain)

    result = presenter.test_comment("Gabriel", "top")

    assert result["ignored"] is True
    assert result["speech"] is None
    assert brain.contexts == []


def test_proactive_uses_registered_product_as_source_of_truth():
    brain = FakeBrain()
    presenter = PresenterV3(product(), brain)

    result = presenter.test_proactive()
    assert result["ok"] is True

    context = brain.contexts[-1]
    assert context.mode == "proactive"
    assert context.product["name"] == "Smart Band X"
    assert "benefícios: Tela nítida e bateria duradoura" in context.allowed_facts



class FailingBrain:
    name = "failing-brain"

    def healthcheck(self):
        return True, "ok"

    def generate(self, context):
        raise RuntimeError("local Brain generation failed")


class IncompleteBrain:
    name = "incomplete-brain"

    def healthcheck(self):
        return True, "ok"

    def generate(self, context):
        return BrainResult(
            speech="Olha só a bateria",
            topic=context.planner_topic,
            used_facts=[],
            needs_fact=False,
        )


def test_proactive_still_speaks_when_brain_generation_fails():
    presenter = PresenterV3(product(), FailingBrain())

    result = presenter.test_proactive()

    assert result["ok"] is True
    assert result["speech"]["speech"].strip()[-1] in ".!?…"
    assert result["speech"]["brain"] == "fallback determinístico"
    assert "failed" in result["speech"]["fallback_reason"]


def test_incomplete_brain_speech_is_replaced_by_complete_fallback():
    presenter = PresenterV3(product(), IncompleteBrain())

    result = presenter.test_proactive()

    assert result["ok"] is True
    assert result["speech"]["speech"].strip()[-1] in ".!?…"
    assert result["speech"]["brain"] == "fallback determinístico"
    assert "encerramento" in result["speech"]["fallback_reason"]


def test_comment_with_known_fact_uses_fallback_when_brain_fails():
    presenter = PresenterV3(product(), FailingBrain())

    result = presenter.test_comment("Maria", "quanto custa?")

    assert result["ok"] is True
    assert result["speech"]["speech"]
    assert result["speech"]["brain"] == "fallback determinístico"
