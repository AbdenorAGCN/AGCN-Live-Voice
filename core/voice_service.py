"""Fila de voz: texto aprovado -> TTS -> dispositivo de áudio."""

from __future__ import annotations

import itertools
import queue
import threading
from dataclasses import dataclass, field
from typing import Any

from core.integration_contracts import AudioSink, TTSProvider
from core.speech_text import normalize_ptbr_for_tts


@dataclass(order=True)
class VoiceJob:
    sort_key: tuple[int, int]
    text: str = field(compare=False)
    voice: str | None = field(default=None, compare=False)
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)


class VoiceService:
    def __init__(
        self,
        tts: TTSProvider,
        sink: AudioSink,
        *,
        default_voice: str | None = None,
        style_selection: str = "auto",
        max_queue: int = 30,
    ) -> None:
        self.tts = tts
        self.sink = sink
        self.default_voice = default_voice
        self.style_selection = str(style_selection or "auto").strip().casefold()
        self.queue: queue.PriorityQueue[VoiceJob] = queue.PriorityQueue(
            maxsize=max_queue
        )
        self.counter = itertools.count()
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.current_job: VoiceJob | None = None
        self.last_error = ""

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            if self.stop_event.is_set():
                self.stop_event.clear()
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._worker,
            name="agcn-voice-worker",
            daemon=True,
        )
        self.thread.start()

    def enqueue(
        self,
        text: str,
        *,
        priority: int = 30,
        voice: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceJob:
        text = str(text or "").strip()
        if not text:
            raise ValueError("texto vazio para fila de voz")

        job = VoiceJob(
            sort_key=(-int(priority), next(self.counter)),
            text=text,
            voice=voice,
            metadata=dict(metadata or {}),
        )
        try:
            self.queue.put_nowait(job)
        except queue.Full:
            # Quando a fila está cheia, elimina falas proativas ainda não
            # iniciadas antes de rejeitar uma resposta a comentário.
            self.clear_pending(proactive_only=True)
            try:
                self.queue.put_nowait(job)
            except queue.Full as exc:
                raise RuntimeError(
                    "Fila de voz cheia; a fala atual será concluída antes de novas falas."
                ) from exc
        return job

    def clear_pending(self, *, proactive_only: bool = False) -> int:
        kept = []
        removed = 0
        while True:
            try:
                job = self.queue.get_nowait()
            except queue.Empty:
                break

            is_proactive = job.metadata.get("type") == "proactive"
            if proactive_only and not is_proactive:
                kept.append(job)
            else:
                removed += 1
            self.queue.task_done()

        for job in kept:
            self.queue.put_nowait(job)
        return removed

    def interrupt_proactive(self) -> bool:
        """Descarta somente falas proativas pendentes, sem cortar o áudio atual.

        A reprodução é serial: uma resposta prioritária aguarda a frase atual
        terminar, evitando sílabas cortadas e falas que começam pela metade.
        """
        current = self.current_job
        current_is_proactive = bool(
            current and current.metadata.get("type") == "proactive"
        )
        removed = self.clear_pending(proactive_only=True)
        return current_is_proactive or bool(removed)

    def stop(self) -> None:
        self.stop_event.set()
        try:
            self.sink.stop()
        except Exception:
            pass
        close_tts = getattr(self.tts, "close", None)
        if callable(close_tts):
            try:
                close_tts()
            except Exception:
                pass

    def snapshot(self) -> dict:
        return {
            "running": bool(self.thread and self.thread.is_alive()),
            "queued": self.queue.qsize(),
            "current": (
                {
                    "text": self.current_job.text,
                    "metadata": dict(self.current_job.metadata),
                }
                if self.current_job
                else None
            ),
            "tts": self.tts.name,
            "style_selection": self.style_selection,
            "current_style": (
                self.current_job.metadata.get("voice_style")
                if self.current_job
                else None
            ),
            "active_provider": (
                getattr(self.tts, "last_provider", "")
                or self.tts.name
            ),
            "last_error": self.last_error or None,
        }

    def _worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                job = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue

            self.current_job = job
            try:
                metadata = dict(job.metadata)
                if self.style_selection not in {"", "auto"}:
                    metadata["voice_style"] = self.style_selection
                    job.metadata["voice_style"] = self.style_selection

                configure = getattr(self.tts, "configure_for_job", None)
                if callable(configure):
                    configure(metadata)
                spoken_text = normalize_ptbr_for_tts(job.text)
                chunk = self.tts.synthesize(
                    spoken_text,
                    voice=job.voice or self.default_voice,
                )
                if self.stop_event.is_set():
                    continue
                self.sink.play(chunk)
                self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)
            finally:
                self.current_job = None
                self.queue.task_done()
