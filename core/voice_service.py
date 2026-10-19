"""Fila de voz com síntese antecipada e reprodução serial.

O áudio seguinte é preparado enquanto o sink reproduz a fala atual. A saída
continua estritamente serial: nunca há duas falas simultâneas.
"""
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
        self.queue: queue.PriorityQueue[VoiceJob] = queue.PriorityQueue(maxsize=max_queue)
        self.ready_queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=max_queue)
        self.counter = itertools.count()
        self.thread: threading.Thread | None = None
        self.playback_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.current_job: VoiceJob | None = None
        self.synthesizing_job: VoiceJob | None = None
        self.last_error = ""

    def start(self) -> None:
        self.stop_event.clear()
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(
                target=self._worker, name="agcn-voice-synthesis", daemon=True
            )
            self.thread.start()
        if not self.playback_thread or not self.playback_thread.is_alive():
            self.playback_thread = threading.Thread(
                target=self._playback_worker, name="agcn-voice-playback", daemon=True
            )
            self.playback_thread.start()

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
            self.clear_pending(proactive_only=True)
            try:
                self.queue.put_nowait(job)
            except queue.Full as exc:
                raise RuntimeError(
                    "Fila de voz cheia; a fala atual será concluída antes de novas falas."
                ) from exc
        return job

    @staticmethod
    def _is_proactive(job: VoiceJob) -> bool:
        return job.metadata.get("type") == "proactive"

    def _filter_queue(self, target_queue, *, proactive_only: bool) -> int:
        kept = []
        removed = 0
        while True:
            try:
                item = target_queue.get_nowait()
            except queue.Empty:
                break
            job = item[1] if isinstance(item, tuple) else item
            if proactive_only and not self._is_proactive(job):
                kept.append(item)
            else:
                removed += 1
            target_queue.task_done()
        for item in kept:
            target_queue.put_nowait(item)
        return removed

    def clear_pending(self, *, proactive_only: bool = False) -> int:
        removed = self._filter_queue(self.queue, proactive_only=proactive_only)
        removed += self._filter_queue(self.ready_queue, proactive_only=proactive_only)
        return removed

    def interrupt_proactive(self) -> bool:
        """Descarta falas proativas pendentes sem cortar o áudio atual."""
        current_is_proactive = bool(self.current_job and self._is_proactive(self.current_job))
        preparing_is_proactive = bool(
            self.synthesizing_job and self._is_proactive(self.synthesizing_job)
        )
        removed = self.clear_pending(proactive_only=True)
        return current_is_proactive or preparing_is_proactive or bool(removed)

    def _has_pending_reactive(self) -> bool:
        with self.queue.mutex:
            if any(not self._is_proactive(job) for job in self.queue.queue):
                return True
        with self.ready_queue.mutex:
            if any(not self._is_proactive(item[1]) for item in self.ready_queue.queue):
                return True
        return False

    def stop(self) -> None:
        self.stop_event.set()
        try:
            self.sink.stop()
        except Exception:
            pass
        self.clear_pending()
        close_tts = getattr(self.tts, "close", None)
        if callable(close_tts):
            try:
                close_tts()
            except Exception:
                pass

    def snapshot(self) -> dict:
        current = self.current_job or self.synthesizing_job
        return {
            "running": bool(
                (self.thread and self.thread.is_alive())
                or (self.playback_thread and self.playback_thread.is_alive())
            ),
            "queued": self.queue.qsize() + self.ready_queue.qsize(),
            "current": (
                {"text": current.text, "metadata": dict(current.metadata)}
                if current else None
            ),
            "playing": bool(self.current_job),
            "synthesizing": bool(self.synthesizing_job),
            "tts": self.tts.name,
            "style_selection": self.style_selection,
            "current_style": current.metadata.get("voice_style") if current else None,
            "active_provider": getattr(self.tts, "last_provider", "") or self.tts.name,
            "last_error": self.last_error or None,
        }

    def _worker(self) -> None:
        """Synthesizes upcoming utterances ahead of playback."""
        while not self.stop_event.is_set():
            try:
                job = self.queue.get(timeout=0.15)
            except queue.Empty:
                continue
            self.synthesizing_job = job
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
                    spoken_text, voice=job.voice or self.default_voice
                )
                if self.stop_event.is_set():
                    continue
                if self._is_proactive(job) and self._has_pending_reactive():
                    self.queue.put_nowait(job)
                    continue
                try:
                    self.ready_queue.put_nowait((job.sort_key, job, chunk))
                except queue.Full:
                    self.last_error = "Fila de áudio preparado cheia."
                    self.stop_event.wait(0.1)
                    try:
                        self.queue.put_nowait(job)
                    except queue.Full:
                        pass
            except Exception as exc:
                self.last_error = str(exc)
            finally:
                self.synthesizing_job = None
                self.queue.task_done()

    def _playback_worker(self) -> None:
        """Plays prepared chunks sequentially without overlapping audio."""
        while not self.stop_event.is_set():
            try:
                _sort_key, job, chunk = self.ready_queue.get(timeout=0.15)
            except queue.Empty:
                continue
            self.current_job = job
            try:
                if not self.stop_event.is_set():
                    self.sink.play(chunk)
                    self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)
            finally:
                self.current_job = None
                self.ready_queue.task_done()
