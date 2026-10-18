"""Presenter V3: mesma lógica de LIVE para Qwen local e APIs.

O sistema decide quando responder, quais fatos existem e quando voltar ao
produto. O Brain apenas transforma a missão autorizada em fala natural.
"""

from __future__ import annotations

import heapq
import itertools
import time
from collections import deque

from core.brain_context_builder import build_brain_context
from core.comment_fusion import CommentFusion
from core.comment_intelligence import CommentIntelligence
from core.decision_engine import DecisionEngine
from core.integration_contracts import BrainProvider, CommentPayload
from core.memory_manager import MemoryManager
from core.sales_guard import SalesGuard
from core.silence_watchdog import SilenceWatchdog
from core.speech_planner import SpeechPlanner
from core.presenter_v2 import PresenterV2


class PresenterV3:
    MAX_REACTIVE_QUEUE = 40

    # Perguntas factuais só chegam ao Brain quando existe resposta cadastrada.
    # Se não existe fato, o comentário é silenciosamente ignorado.
    FACT_REQUIRED_INTENTS = {
        "safety_or_critical",
        "price",
        "availability",
        "compatibility",
        "technical_question",
        "shipping",
        "coupon",
        "warranty",
        "brand",
        "included_items",
        "size",
        "battery",
        "benefits",
        "usage",
        "direct_question",
    }

    def __init__(self, product: dict | None, brain: BrainProvider) -> None:
        self.product = dict(product or {})
        self.brain = brain
        self.guard = SalesGuard(self.product)
        self.memory = MemoryManager()
        self.intelligence = CommentIntelligence()
        self.fusion = CommentFusion(window_seconds=0.9)
        self.decision_engine = DecisionEngine()
        self.planner = SpeechPlanner()
        self.watchdog = SilenceWatchdog(target_seconds=2.5, hard_seconds=4.0)
        self.queue = []
        self.counter = itertools.count()
        self.plan_queue = []
        self.plan_counter = itertools.count()
        self.recent_keys = {}
        self.recent_comments = deque(maxlen=12)
        self.recent_facts = deque(maxlen=24)
        self.last_brain_error = ""

    def set_product(self, product: dict | None) -> None:
        self.product = dict(product or {})
        self.guard = SalesGuard(self.product)
        self.recent_facts.clear()
        self.queue.clear()
        self.plan_queue.clear()
        self.fusion.pending.clear()

    def ingest_comment(self, user: str, text: str) -> dict | None:
        analyzed = self.intelligence.analyze(user, text)
        if not analyzed:
            return None

        now = time.time()
        key = (
            analyzed["user"].casefold(),
            analyzed["comment"].casefold(),
            analyzed["intent"],
        )
        if now - self.recent_keys.get(key, -999) < 20:
            return None

        self.recent_keys[key] = now
        self.recent_keys = {
            k: ts for k, ts in self.recent_keys.items() if now - ts <= 90
        }
        self.recent_comments.append(
            CommentPayload(
                username=analyzed.get("user") or "",
                text=analyzed.get("comment") or "",
            )
        )
        self.fusion.add(analyzed)
        return analyzed

    def _plan_is_answerable(self, plan: dict) -> bool:
        if plan.get("type") == "proactive":
            return True
        intent = str(plan.get("intent") or "")
        if intent not in self.FACT_REQUIRED_INTENTS:
            return True
        return bool(plan.get("has_fact"))

    def _push(self, item: dict) -> None:
        heapq.heappush(
            self.queue,
            (-item["priority"], next(self.counter), item),
        )
        if len(self.queue) <= self.MAX_REACTIVE_QUEUE:
            return

        # Descarta o item menos importante quando a LIVE recebe uma avalanche
        # de perguntas. A fila nunca cresce indefinidamente.
        worst = max(
            range(len(self.queue)),
            key=lambda i: (
                self.queue[i][0],
                self.queue[i][1],
            ),
        )
        self.queue.pop(worst)
        heapq.heapify(self.queue)

    def collect_pending_comments(self) -> int:
        """Classifica/planeja comentários sem chamar o Brain.

        Durante o modo produto, isso preserva a fila sem gastar API ou gerar
        respostas que podem ficar desatualizadas antes de serem faladas.
        """
        added = 0
        for group in self.fusion.ready_groups():
            decision = self.decision_engine.decide(
                group, self.memory.snapshot()
            )
            plan = self.planner.plan(decision, self.guard, self.memory)

            if not self._plan_is_answerable(plan):
                continue

            heapq.heappush(
                self.plan_queue,
                (
                    -int(plan.get("priority", 0)),
                    next(self.plan_counter),
                    plan,
                ),
            )
            added += 1

        # Mantém somente os planos de maior prioridade.
        while len(self.plan_queue) > self.MAX_REACTIVE_QUEUE:
            worst = max(
                range(len(self.plan_queue)),
                key=lambda i: (
                    self.plan_queue[i][0],
                    self.plan_queue[i][1],
                ),
            )
            self.plan_queue.pop(worst)
            heapq.heapify(self.plan_queue)

        return added

    def process_pending_comments(
        self,
        *,
        max_generate: int = 3,
        target_ready_queue: int = 6,
    ) -> list[dict]:
        """Gera poucas respostas por vez, sob demanda.

        Comentários podem chegar em avalanche; Qwen/API só recebem os planos
        que estão próximos de serem falados.
        """
        self.collect_pending_comments()

        created = []
        budget = max(0, int(max_generate))
        target = max(1, int(target_ready_queue))

        while (
            self.plan_queue
            and len(created) < budget
            and len(self.queue) < target
        ):
            _, _, plan = heapq.heappop(self.plan_queue)
            item = self._generate_item(plan)
            if not item:
                continue
            self._push(item)
            created.append(item)

        return created

    def maybe_proactive(self) -> dict | None:
        seconds = self.memory.seconds_since_speech()
        if not self.watchdog.should_trigger(
            seconds, queue_empty=not self.queue
        ):
            return None

        topic = self.planner.choose_proactive_topic(
            self.guard, self.memory
        )
        decision = self.decision_engine.proactive(
            topic,
            priority=35
            if self.watchdog.urgency(seconds) == "hard"
            else 30,
        )
        plan = self.planner.plan(decision, self.guard, self.memory)
        item = self._generate_item(plan)
        if item:
            self._push(item)
        return item

    def force_proactive(self) -> dict | None:
        """Gera fala de produto mesmo quando há comentários esperando.

        É usado pelo runtime durante a janela obrigatória de foco no produto.
        """
        topic = self.planner.choose_proactive_topic(
            self.guard, self.memory
        )
        decision = self.decision_engine.proactive(topic, priority=36)
        plan = self.planner.plan(decision, self.guard, self.memory)
        item = self._generate_item(plan)
        if item:
            self.memory.remember_speech(item)
            for fact in item.get("used_facts") or []:
                if fact:
                    self.recent_facts.append(str(fact))
        return item

    def next_speech(self) -> dict | None:
        self.process_pending_comments()
        if not self.queue:
            self.maybe_proactive()
        if not self.queue:
            return None

        _, _, item = heapq.heappop(self.queue)
        self.memory.remember_speech(item)
        for fact in item.get("used_facts") or []:
            if fact:
                self.recent_facts.append(str(fact))
        return item

    def test_comment(self, user: str, text: str) -> dict:
        analyzed = self.intelligence.analyze(user, text)
        if not analyzed:
            return {
                "ok": True,
                "ignored": True,
                "message": "Comentário ignorado.",
                "analyzed": None,
                "speech": None,
                "memory": self.memory.snapshot(),
            }

        self.recent_comments.append(
            CommentPayload(
                username=analyzed.get("user") or "",
                text=analyzed.get("comment") or "",
            )
        )
        group = {
            "primary": analyzed,
            "items": [analyzed],
            "priority": analyzed.get("priority", 0),
            "users": [analyzed.get("user")] if analyzed.get("user") else [],
            "topics": [analyzed.get("topic")] if analyzed.get("topic") else [],
        }
        decision = self.decision_engine.decide(
            group, self.memory.snapshot()
        )
        plan = self.planner.plan(decision, self.guard, self.memory)

        if not self._plan_is_answerable(plan):
            return {
                "ok": True,
                "ignored": True,
                "analyzed": analyzed,
                "decision": decision,
                "plan": plan,
                "speech": None,
                "memory": self.memory.snapshot(),
                "brain_error": None,
            }

        item = self._generate_item(plan)
        if item:
            self.memory.remember_speech(item)
            for fact in item.get("used_facts") or []:
                self.recent_facts.append(str(fact))

        return {
            "ok": True,
            "ignored": not bool(item),
            "analyzed": analyzed,
            "decision": decision,
            "plan": plan,
            "speech": item,
            "memory": self.memory.snapshot(),
            "brain_error": self.last_brain_error or None,
        }

    def test_proactive(self) -> dict:
        topic = self.planner.choose_proactive_topic(
            self.guard, self.memory
        )
        decision = self.decision_engine.proactive(topic, priority=30)
        plan = self.planner.plan(decision, self.guard, self.memory)
        item = self._generate_item(plan)
        if item:
            self.memory.remember_speech(item)
            for fact in item.get("used_facts") or []:
                self.recent_facts.append(str(fact))

        return {
            "ok": bool(item),
            "topic": topic,
            "speech": item,
            "memory": self.memory.snapshot(),
            "brain_error": self.last_brain_error or None,
        }

    def queue_snapshot(self) -> list[dict]:
        return [item for _, _, item in sorted(self.queue)]

    def snapshot(self) -> dict:
        return {
            "memory": self.memory.snapshot(),
            "brain": {
                "name": self.brain.name,
                "last_error": self.last_brain_error or None,
            },
            "recent_facts": list(self.recent_facts)[-12:],
            "pending_comment_plans": len(self.plan_queue),
            "watchdog": {
                "target_seconds": self.watchdog.target_seconds,
                "hard_seconds": self.watchdog.hard_seconds,
                "status": self.watchdog.urgency(
                    self.memory.seconds_since_speech()
                ),
            },
        }

    def _fallback_item(self, plan: dict, reason: str = "") -> dict | None:
        """Gera uma fala determinística quando o Brain falha ou devolve saída inválida.

        O fallback nunca inventa fatos: PresenterV2 aplica o mesmo SalesGuard
        e descarta perguntas factuais sem resposta cadastrada.
        """
        fallback = PresenterV2(self.product)
        item = fallback._render_plan(plan)
        if not item:
            return None
        item.update({
            "type": plan.get("type"),
            "intent": plan.get("intent"),
            "category": plan.get("intent"),
            "topic": plan.get("topic") or item.get("topic"),
            "tactic": plan.get("tactic"),
            "priority": plan.get("priority", 0),
            "user": plan.get("user") or item.get("user"),
            "comment": plan.get("comment"),
            "cta": plan.get("cta") or item.get("cta"),
            "plan": plan.get("steps", []),
            "resume_topic": plan.get("resume_topic"),
            "used_facts": [str(plan.get("fact"))] if plan.get("fact") not in (None, "", [], {}) else [],
            "needs_fact": False,
            "brain": "fallback determinístico",
            "fallback_reason": str(reason or "Brain sem saída utilizável"),
            "created_at": time.time(),
        })
        return item

    def _generate_item(self, plan: dict) -> dict | None:
        mode = (
            "proactive"
            if plan.get("type") == "proactive"
            else "comment_reply"
        )
        context = build_brain_context(
            product=self.product,
            mode=mode,
            decision=plan,
            planner_topic=plan.get("topic") or "",
            memory_snapshot=self.memory.snapshot(),
            recent_comments=list(self.recent_comments),
            recent_facts=list(self.recent_facts),
        )

        try:
            result = self.brain.generate(context)
            self.last_brain_error = ""
        except Exception as exc:
            self.last_brain_error = str(exc)
            return self._fallback_item(plan, self.last_brain_error)

        # Um erro de classificação do modelo não pode silenciar o fluxo
        # proativo. O fallback usa somente os fatos aprovados pelo SalesGuard.
        if result.needs_fact:
            # O plano já passou pela checagem de fatos do Presenter. Se o
            # modelo pedir fato novamente, usa a resposta segura do Presenter
            # determinístico em vez de perder um comentário que era respondível.
            return self._fallback_item(
                plan,
                "Brain marcou uma fala respondível como dependente de fato",
            )

        speech = str(result.speech or "").strip()
        if not speech:
            self.last_brain_error = "Brain devolveu fala vazia"
            return self._fallback_item(plan, self.last_brain_error)

        # O TTS recebe frases completas, não texto com espaços ou quebras
        # residuais no fim. Se a saída parece truncada, usa a fala determinística.
        if speech[-1] not in ".!?…»”\"'":
            self.last_brain_error = "Brain devolveu fala sem encerramento"
            return self._fallback_item(plan, self.last_brain_error)

        return {
            "type": plan.get("type"),
            "intent": plan.get("intent"),
            "category": plan.get("intent"),
            "topic": result.topic or plan.get("topic"),
            "tactic": plan.get("tactic"),
            "label": plan.get("label"),
            "priority": plan.get("priority", 0),
            "user": plan.get("user"),
            "comment": plan.get("comment"),
            "speech": speech,
            "cta": plan.get("cta"),
            "plan": plan.get("steps", []),
            "resume_topic": (
                result.next_sales_thread or plan.get("resume_topic")
            ),
            "next_sales_thread": result.next_sales_thread,
            "used_facts": list(result.used_facts),
            "needs_fact": False,
            "brain": self.brain.name,
            "created_at": time.time(),
        }
