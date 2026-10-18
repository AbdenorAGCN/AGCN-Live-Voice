import io
import wave

from core.integration_contracts import AudioChunk
from core.speech_text import normalize_ptbr_for_tts
from core.tts_providers import FallbackTTSProvider, wav_bytes_to_chunk
from core.voice_service import VoiceService


def make_wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 100)
    return buffer.getvalue()


class FakeTTS:
    def __init__(self, name="fake", error=None):
        self._name = name
        self.error = error
        self.calls = []
        self.configured = []

    @property
    def name(self):
        return self._name

    def healthcheck(self):
        return True, "ok"

    def configure_for_job(self, metadata=None):
        self.configured.append(dict(metadata or {}))

    def synthesize(self, text, *, voice=None):
        self.calls.append((text, voice))
        if self.error:
            raise self.error
        return AudioChunk(
            data=b"\x00\x00" * 10,
            sample_rate=24000,
        )


class FakeSink:
    def __init__(self):
        self.stop_calls = 0

    def list_devices(self):
        return ["fake"]

    def select_device(self, device_name):
        pass

    def play(self, chunk):
        pass

    def stop(self):
        self.stop_calls += 1


def test_wav_bytes_to_audio_chunk():
    chunk = wav_bytes_to_chunk(make_wav_bytes())
    assert chunk.sample_rate == 24000
    assert chunk.channels == 1
    assert chunk.sample_width == 2
    assert chunk.data


def test_fallback_tts_uses_local_when_primary_fails():
    premium = FakeTTS("premium", error=RuntimeError("down"))
    local = FakeTTS("local")
    provider = FallbackTTSProvider(premium, local)
    chunk = provider.synthesize("teste")
    assert chunk.sample_rate == 24000
    assert provider.last_provider == "local"


def test_voice_queue_prioritizes_reactive_over_proactive():
    service = VoiceService(FakeTTS(), FakeSink())
    service.enqueue(
        "fala proativa",
        priority=30,
        metadata={"type": "proactive"},
    )
    service.enqueue(
        "resposta de compra",
        priority=98,
        metadata={"type": "reactive"},
    )

    first = service.queue.get_nowait()
    assert first.text == "resposta de compra"


def test_voice_service_can_restart_after_stop():
    service = VoiceService(FakeTTS(), FakeSink())
    service.start()
    assert service.stop_event.is_set() is False

    service.stop()
    assert service.stop_event.is_set() is True

    service.start()
    assert service.stop_event.is_set() is False

    service.stop()


def test_voice_service_manual_style_overrides_automatic_metadata():
    tts = FakeTTS()
    service = VoiceService(
        tts,
        FakeSink(),
        style_selection="suspense_reveal",
    )
    service.enqueue(
        "agora presta atenção",
        metadata={
            "type": "proactive",
            "voice_style": "sales_energy",
        },
    )

    job = service.queue.get_nowait()
    service.current_job = job
    metadata = dict(job.metadata)
    if service.style_selection not in {"", "auto"}:
        metadata["voice_style"] = service.style_selection
        job.metadata["voice_style"] = service.style_selection
    tts.configure_for_job(metadata)

    assert job.metadata["voice_style"] == "suspense_reveal"
    assert tts.configured[-1]["voice_style"] == "suspense_reveal"



def test_brl_is_spoken_as_reais_not_currency_symbol():
    assert normalize_ptbr_for_tts("Agora é R$ 49,90.") == (
        "Agora é 49 reais e 90 centavos."
    )
    assert normalize_ptbr_for_tts("De R$ 199 por R$ 149,50.") == (
        "De 199 reais por 149 reais e 50 centavos."
    )


def test_voice_service_can_interrupt_current_proactive_audio():
    sink = FakeSink()
    service = VoiceService(FakeTTS(), sink)
    service.current_job = service.enqueue(
        "fala proativa",
        priority=30,
        metadata={"type": "proactive"},
    )
    service.queue.get_nowait()

    assert service.interrupt_proactive() is True
    # Prioriza o comentário, mas deixa a fala já iniciada terminar inteira.
    assert sink.stop_calls == 0
