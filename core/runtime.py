from __future__ import annotations

import threading
import time
from collections import deque

from core.brain_factory import build_brain_provider
from core.integration_contracts import BrainProvider
from core.presenter_v2 import PresenterV2
from core.presenter_v3 import PresenterV3
from core.product_store import ProductStore
from core.tiktok_monitor import TikTokMonitor
from core.voice_expression import infer_voice_style
from core.voice_factory import build_voice_service
from core.voice_service import VoiceService


class AGCNVoiceRuntime:
    """Runtime integrado da LIVE.

    O Presenter roda em worker próprio. Isso evita travar a UI enquanto
    Qwen/API/TTS trabalham.

    Regra de cadência:
    - no máximo 3 respostas consecutivas;
    - depois, 30s obrigatórios falando do produto;
    - perguntas continuam entrando na fila durante essa janela.
    """

    MAX_REACTIVE_BURST = 3
    FORCED_PRODUCT_SECONDS = 30.0
    REACTIVE_INTERRUPT_PRIORITY = 80

    def __init__(
        self,
        store: ProductStore | None = None,
        *,
        brain_provider: BrainProvider | None = None,
        brain_config: dict | None = None,
        voice_service: VoiceService | None = None,
        voice_config: dict | None = None,
    ):
        self.store = store or ProductStore()
        self.lock = threading.RLock()

        self.comments = deque(maxlen=30)
        self.comments_analyzed = 0
        self.items_queued = 0
        self.speeches_generated = 0
        self.proactive_generated = 0
        self.interruptions = 0
        self.voice_jobs = 0

        self.last_comment = None
        self.last_decision = None
        self.current_speech = None
        self.current_speech_until = 0.0
        self.reactive_streak = 0
        self.forced_product_start_at = 0.0
        self.forced_product_until = 0.0

        self.brain_provider = brain_provider
        if self.brain_provider is None and brain_config is not None:
            self.brain_provider = build_brain_provider(brain_config)

        self.voice_service = voice_service
        if self.voice_service is None and voice_config is not None:
            self.voice_service = build_voice_service(voice_config)

        active = self.store.active_for_presenter() or {}
        self.presenter = self._new_presenter(active)
        self.product_signature = self._product_signature(active)

        self.monitor = TikTokMonitor(
            event_callback=self._on_monitor_event
        )

        self.presenter_stop_event = threading.Event()
        self.presenter_thread: threading.Thread | None = None
        self.presenter_worker_error = ""
        self.brain_preflight_done = self.brain_provider is None
        self.brain_status = (
            "deterministic"
            if self.brain_provider is None
            else "pending"
        )
        self.brain_degraded_reason = ""

    def _new_presenter(self, product: dict):
        if self.brain_provider is not None:
            return PresenterV3(product, self.brain_provider)
        return PresenterV2(product)

    @staticmethod
    def _product_signature(product: dict) -> tuple:
        return (
            product.get("id"),
            product.get("updated_at"),
        )

    def _sync_active_product_locked(self):
        active = self.store.active_for_presenter() or {}
        signature = self._product_signature(active)

        if signature != self.product_signature:
            previous_id = self.presenter.product.get("id")
            active_id = active.get("id")

            if previous_id == active_id:
                self.presenter.set_product(active)
            else:
                self.presenter = self._new_presenter(active)
                self.current_speech = None
                self.current_speech_until = 0.0
                self.last_decision = None
                self.reactive_streak = 0
                self.forced_product_start_at = 0.0
                self.forced_product_until = 0.0

            self.product_signature = signature

    def _on_monitor_event(self, event_type: str, payload: dict):
        if event_type != "comment":
            return

        with self.lock:
            self._sync_active_product_locked()

            item = {
                "time": payload.get("time"),
                "user": payload.get("user"),
                "text": payload.get("text"),
            }

            self.comments.append(item)
            self.last_comment = item

            analyzed = self.presenter.ingest_comment(
                item["user"],
                item["text"],
            )

            if analyzed:
                self.comments_analyzed += 1

    def add_product(self, payload: dict) -> dict:
        product = self.store.add(**(payload or {}))
        with self.lock:
            self._sync_active_product_locked()
        return {
            "ok": True,
            "message": "Produto salvo.",
            "product": product,
        }

    def update_product(self, product_id: str, payload: dict) -> dict:
        product = self.store.update(product_id, **(payload or {}))
        with self.lock:
            self._sync_active_product_locked()
        return {
            "ok": True,
            "message": "Produto atualizado.",
            "product": product,
        }

    def activate_product(self, product_id: str) -> dict:
        product = self.store.activate(product_id)
        with self.lock:
            self._sync_active_product_locked()
        return {
            "ok": True,
            "message": "Produto ativo alterado.",
            "product": product,
        }

    def delete_product(self, product_id: str) -> dict:
        removed = self.store.delete(product_id)
        with self.lock:
            self._sync_active_product_locked()
        return {
            "ok": bool(removed),
            "message": (
                "Produto excluído."
                if removed
                else "Produto não encontrado."
            ),
        }

    def test_presenter_comment(
        self,
        user: str,
        text: str,
    ) -> dict:
        with self.lock:
            self._sync_active_product_locked()
            if not self.presenter.product.get("name"):
                return {
                    "ok": False,
                    "message": "Cadastre e ative um produto antes do teste.",
                }
            return self.presenter.test_comment(user, text)

    def test_presenter_proactive(self) -> dict:
        with self.lock:
            self._sync_active_product_locked()
            if not self.presenter.product.get("name"):
                return {
                    "ok": False,
                    "message": "Cadastre e ative um produto antes do teste.",
                }
            return self.presenter.test_proactive()

    def _ensure_presenter_worker(self) -> None:
        if self.presenter_thread and self.presenter_thread.is_alive():
            if self.presenter_stop_event.is_set():
                self.presenter_stop_event.clear()
            return

        self.presenter_stop_event.clear()
        self.presenter_thread = threading.Thread(
            target=self._presenter_loop,
            name="agcn-presenter-worker",
            daemon=True,
        )
        self.presenter_thread.start()

    def _preflight_brain(self) -> None:
        """Valida o Brain fora do lock da UI.

        Se o Brain configurado não estiver disponível (ex.: Ollama/Qwen não
        instalado), a LIVE continua no Presenter determinístico em vez de
        ficar esperando timeouts repetidos e travar o Dashboard.
        """
        if self.brain_preflight_done:
            return

        provider = self.brain_provider
        if provider is None:
            self.brain_preflight_done = True
            self.brain_status = "deterministic"
            return

        self.brain_status = "checking"
        try:
            ok, message = provider.healthcheck()
        except Exception as exc:
            ok = False
            message = f"{type(exc).__name__}: {exc}"

        if ok:
            self.brain_preflight_done = True
            self.brain_status = "ready"
            self.brain_degraded_reason = ""
            return

        close_provider = getattr(provider, "close", None)
        if callable(close_provider):
            try:
                close_provider()
            except Exception:
                pass

        with self.lock:
            active = self.store.active_for_presenter() or {}
            self.brain_provider = None
            self.presenter = PresenterV2(active)
            self.product_signature = self._product_signature(active)
            self.current_speech = None
            self.current_speech_until = 0.0
            self.last_decision = None
            self.reactive_streak = 0
            self.forced_product_start_at = 0.0
            self.forced_product_until = 0.0
            self.brain_preflight_done = True
            self.brain_status = "fallback"
            self.brain_degraded_reason = (
                str(message or "Brain configurado indisponível.")
            )

    def _presenter_loop(self) -> None:
        self._preflight_brain()
        while not self.presenter_stop_event.is_set():
            try:
                self._tick_presenter_unlocked()
                self.presenter_worker_error = ""
            except Exception as exc:
                self.presenter_worker_error = str(exc)
            self.presenter_stop_event.wait(0.20)

    def start(self, username: str) -> dict:
        if not self.store.active():
            return {
                "ok": False,
                "message": "Cadastre e ative um produto antes de iniciar.",
            }

        if self.voice_service is not None:
            self.voice_service.start()

        result = self.monitor.start(username)
        if result.get("ok", True):
            self._ensure_presenter_worker()
        elif self.voice_service is not None:
            self.voice_service.stop()
        return result

    def stop(self) -> dict:
        self.presenter_stop_event.set()
        result = self.monitor.stop()
        if self.voice_service is not None:
            self.voice_service.stop()
        return result

    def close(self) -> None:
        self.presenter_stop_event.set()
        try:
            self.monitor.stop()
        except Exception:
            pass
        if self.voice_service is not None:
            try:
                self.voice_service.stop()
            except Exception:
                pass
        if self.brain_provider is not None:
            close_brain = getattr(self.brain_provider, "close", None)
            if callable(close_brain):
                try:
                    close_brain()
                except Exception:
                    pass

    def _queue_voice(self, item: dict) -> None:
        if self.voice_service is None:
            return

        if item.get("type") == "reactive":
            priority = int(item.get("priority", 0))
            if priority >= self.REACTIVE_INTERRUPT_PRIORITY:
                interrupt = getattr(
                    self.voice_service,
                    "interrupt_proactive",
                    None,
                )
                if callable(interrupt):
                    interrupt()
                else:
                    self.voice_service.clear_pending(proactive_only=True)
            else:
                self.voice_service.clear_pending(proactive_only=True)

        voice_style = infer_voice_style(item)
        item["voice_style"] = voice_style

        self.voice_service.enqueue(
            item["speech"],
            priority=int(item.get("priority", 30)),
            metadata={
                "type": item.get("type"),
                "intent": item.get("intent"),
                "topic": item.get("topic"),
                "tactic": item.get("tactic"),
                "cta": item.get("cta"),
                "voice_style": voice_style,
                "user": item.get("user"),
                "comment": item.get("comment"),
            },
        )
        self.voice_jobs += 1

    def _product_window_active(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now

        if self.forced_product_until and now >= self.forced_product_until:
            self.reactive_streak = 0
            self.forced_product_start_at = 0.0
            self.forced_product_until = 0.0
            return False

        return bool(
            self.forced_product_start_at
            and self.forced_product_start_at <= now < self.forced_product_until
        )

    def _seconds_until_comments(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        if not self.forced_product_until:
            return 0
        if now < self.forced_product_start_at:
            return int(round(
                (self.forced_product_start_at - now)
                + self.FORCED_PRODUCT_SECONDS
            ))
        if self._product_window_active(now):
            return max(0, int(round(self.forced_product_until - now)))
        return 0

    def _schedule_product_window_after(self, speech_until: float) -> None:
        self.forced_product_start_at = speech_until
        self.forced_product_until = speech_until + self.FORCED_PRODUCT_SECONDS

    def _forced_proactive(self) -> dict | None:
        force = getattr(self.presenter, "force_proactive", None)
        if callable(force):
            return force()

        # Compatibilidade com Presenter determinístico legado.
        topic = self.presenter.planner.choose_proactive_topic(
            self.presenter.guard,
            self.presenter.memory,
        )
        decision = self.presenter.decision_engine.proactive(topic, priority=36)
        plan = self.presenter.planner.plan(
            decision,
            self.presenter.guard,
            self.presenter.memory,
        )
        renderer = getattr(self.presenter, "_render_plan", None)
        if callable(renderer):
            item = renderer(plan)
            if item:
                self.presenter.memory.remember_speech(item)
            return item
        return None

    def _tick_presenter_unlocked(self):
        with self.lock:
            self._sync_active_product_locked()

            monitor_state = self.monitor.snapshot()
            if not (
                monitor_state.get("monitoring")
                and monitor_state.get("status") == "ativo"
            ):
                return

            now = time.time()
            product_window = self._product_window_active(now)
            comments_locked = bool(self.forced_product_until)
            presenter = self.presenter

        # UNLOCKED GENERATION SEGMENT
        # We process comments and maybe proactive generation without locking the UI.
        # Note: If `_on_monitor_event` fires, it accesses `presenter` concurrently, but python appends are thread-safe,
        # and we assume `presenter` queues tolerate concurrent additions.
        if comments_locked:
            collect = getattr(
                presenter,
                "collect_pending_comments",
                None,
            )
            if callable(collect):
                collect()
            created = []
        else:
            created = presenter.process_pending_comments()

        with self.lock:
            if presenter is not self.presenter:
                return # product changed during generation!

            if created:
                self.items_queued += len(created)

            queue = self.presenter.queue_snapshot()

            now = time.time()
            if self.current_speech and now < self.current_speech_until:
                if product_window or self.reactive_streak >= self.MAX_REACTIVE_BURST:
                    return

                if (
                    queue
                    and queue[0].get("priority", 0) >= self.REACTIVE_INTERRUPT_PRIORITY
                    and (
                        self.current_speech.get("type") == "proactive"
                        or queue[0].get("priority", 0) > self.current_speech.get("priority", 0)
                    )
                ):
                    self.current_speech_until = 0.0
                    self.interruptions += 1
                else:
                    return

            self.current_speech = None

        # Re-evaluating unlocked
        if product_window:
            item = self._forced_proactive()
        else:
            proactive = presenter.maybe_proactive()
            item = presenter.next_speech()

        with self.lock:
            if presenter is not self.presenter:
                return

            if product_window:
                if item:
                    self.proactive_generated += 1
            else:
                if proactive:
                    self.proactive_generated += 1
                    self.items_queued += 1

            if not item:
                return

            self.current_speech = item
            self.speeches_generated += 1

            if item.get("type") == "reactive":
                self.last_decision = item
                self.reactive_streak += 1
            elif not product_window:
                self.reactive_streak = 0

            try:
                self._queue_voice(item)
            except Exception as exc:
                item["voice_error"] = str(exc)

            duration = min(
                12.0,
                max(2.5, len(item["speech"]) / 16.0),
            )
            now = time.time()
            self.current_speech_until = now + duration

            if (
                item.get("type") == "reactive"
                and self.reactive_streak >= self.MAX_REACTIVE_BURST
                and not self.forced_product_until
            ):
                self._schedule_product_window_after(self.current_speech_until)

    def _tick_presenter_locked(self):
        # Forward to new implementation for backwards compatibility with test files
        # Alternatively, we just use the un-locked method.
        # To avoid breaking tests that call this directly with `with runtime.lock: runtime._tick_presenter_locked()`,
        # we can just ignore the lock here if it's already held by the test thread.
        # But `with self.lock` inside `_tick_presenter_unlocked` handles reentrancy safely!
        self._tick_presenter_unlocked()

    def snapshot(self) -> dict:
        """Snapshot somente-leitura: nunca chama LLM/TTS."""
        with self.lock:
            live = self.monitor.snapshot()
            active = self.store.active() or {}
            presenter_state = self.presenter.snapshot()
            voice_state = (
                self.voice_service.snapshot()
                if self.voice_service is not None
                else None
            )

            data = {
                **live,
                "version": (
                    "0.9-background-runtime"
                    if self.brain_provider is not None
                    and self.voice_service is not None
                    else "0.8-background-brain"
                    if self.brain_provider is not None
                    else "0.7-background-presenter"
                ),
                "brain_enabled": self.brain_provider is not None,
                "brain_provider": (
                    self.brain_provider.name
                    if self.brain_provider is not None
                    else "deterministic-v2"
                ),
                "brain_status": self.brain_status,
                "brain_degraded_reason": (
                    self.brain_degraded_reason or None
                ),
                "voice_enabled": self.voice_service is not None,
                "voice": voice_state,
                "voice_jobs": self.voice_jobs,
                "presenter_worker_running": bool(
                    self.presenter_thread
                    and self.presenter_thread.is_alive()
                ),
                "presenter_worker_error": (
                    self.presenter_worker_error or None
                ),
                "products": self.store.list(),
                "active_product": active,
                "comments_analyzed": self.comments_analyzed,
                "items_queued": self.items_queued,
                "speeches_generated": self.speeches_generated,
                "proactive_generated": self.proactive_generated,
                "interruptions": self.interruptions,
                "presenter_mode": (
                    "produto"
                    if self._product_window_active()
                    else "interativo"
                ),
                "comments_paused_seconds": self._seconds_until_comments(),
                "reactive_streak": self.reactive_streak,
                "max_reactive_burst": self.MAX_REACTIVE_BURST,
                "last_comment": (
                    dict(self.last_comment)
                    if self.last_comment
                    else None
                ),
                "last_decision": (
                    dict(self.last_decision)
                    if self.last_decision
                    else None
                ),
                "queue": [
                    dict(item)
                    for item in self.presenter.queue_snapshot()
                ],
                "current_speech": (
                    dict(self.current_speech)
                    if self.current_speech
                    else None
                ),
                "comments": [
                    dict(item)
                    for item in list(self.comments)[-12:]
                ],
                "presenter": presenter_state,
            }

            data["summary"] = self._summary(data)
            return data

    @staticmethod
    def _summary(data: dict) -> str:
        product = data.get("active_product") or {}
        presenter = data.get("presenter") or {}
        memory = presenter.get("memory") or {}
        watchdog = presenter.get("watchdog") or {}
        voice = data.get("voice") or {}

        checks = {
            "LIVE conectou": bool(
                data.get("connected") or data.get("room_id")
            ),
            "Room ID recebido": bool(data.get("room_id")),
            "Produto ativo carregado": bool(product.get("name")),
            "Comentários recebidos": (
                data.get("comments_received", 0) > 0
            ),
            "Comentário analisado/classificado": (
                data.get("comments_analyzed", 0) > 0
            ),
            "Fila de resposta utilizada": (
                data.get("items_queued", 0) > 0
            ),
            "Fala sugerida gerada": (
                data.get("speeches_generated", 0) > 0
            ),
        }

        passed = sum(1 for ok in checks.values() if ok)

        lines = [
            "AGCN LIVE VOICE — TESTE PRESENTER BRAIN",
            "",
            f"LIVE: {data.get('username') or 'NÃO INICIADA'}",
            f"Status: {data.get('status', '—')}",
            f"Room ID: {data.get('room_id') or 'NÃO RECEBIDO'}",
            f"Produto ativo: {product.get('name') or 'NÃO CARREGADO'}",
            f"Brain: {data.get('brain_provider', '—')}",
            f"Voz: {voice.get('tts') or ('DESATIVADA' if not data.get('voice_enabled') else '—')}",
            f"Estilo vocal: {voice.get('current_style') or (data.get('current_speech') or {}).get('voice_style') or '—'}",
            f"Worker: {'ATIVO' if data.get('presenter_worker_running') else 'PARADO'}",
            f"Modo: {data.get('presenter_mode', '—')}",
            f"Respostas seguidas: {data.get('reactive_streak', 0)}/{data.get('max_reactive_burst', 3)}",
            f"Comentários pausados: {data.get('comments_paused_seconds', 0)}s",
            "",
            "CHECKLIST:",
        ]

        for label, ok in checks.items():
            lines.append(f"{'OK' if ok else 'PENDENTE'} - {label}")

        lines += [
            "",
            f"Viewers atuais: {data.get('viewers') if data.get('viewers') is not None else '—'}",
            f"Curtidas: {data.get('likes') if data.get('likes') is not None else '—'}",
            f"Comentários recebidos: {data.get('comments_received', 0)}",
            f"Comentários analisados: {data.get('comments_analyzed', 0)}",
            f"Itens enfileirados: {data.get('items_queued', 0)}",
            f"Falas geradas: {data.get('speeches_generated', 0)}",
            f"Falas proativas: {data.get('proactive_generated', 0)}",
            f"Jobs de voz: {data.get('voice_jobs', 0)}",
            f"Interrupções prioritárias: {data.get('interruptions', 0)}",
            f"Silêncio atual: {memory.get('seconds_since_speech', '—')}s",
            f"Watchdog: {watchdog.get('status', '—')}",
            f"Tópico atual: {memory.get('current_topic') or '—'}",
        ]

        decision = data.get("last_decision")
        if decision:
            lines += [
                "",
                f"Comentário da última decisão: {decision.get('user', '')}: {decision.get('comment', '')}",
                f"Intenção: {decision.get('label', '—')}",
                f"Prioridade: {decision.get('priority', '—')}",
                f"Plano: {' → '.join(decision.get('plan') or [])}",
            ]

        lines += [
            "",
            f"Resultado automático: {passed}/{len(checks)} verificações confirmadas.",
            f"Diagnóstico TikTok: {data.get('diagnostic', '—')}",
        ]

        if data.get("presenter_worker_error"):
            lines += [
                "",
                f"ERRO DO WORKER: {data.get('presenter_worker_error')}",
            ]
        if voice.get("last_error"):
            lines += ["", f"ERRO DE VOZ: {voice.get('last_error')}"]
        if data.get("error"):
            lines += ["", f"ERRO: {data.get('error')}"]

        return "\n".join(lines)
