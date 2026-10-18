from core.integration_contracts import BrainResult
from core.product_store import ProductStore
from core.runtime import AGCNVoiceRuntime


class FakeBrain:
    name = "fake-brain"

    def __init__(self):
        self.calls = 0

    def healthcheck(self):
        return True, "ok"

    def generate(self, context):
        self.calls += 1
        fact = context.allowed_facts[0] if context.allowed_facts else None
        return BrainResult(
            speech="Fala de teste.",
            topic=context.planner_topic or "test",
            used_facts=[fact] if fact else [],
            needs_fact=False,
            next_sales_thread="benefits",
        )


class UnavailableBrain:
    name = "unavailable-brain"

    def __init__(self):
        self.health_calls = 0
        self.generate_calls = 0

    def healthcheck(self):
        self.health_calls += 1
        return False, "Ollama indisponível para teste."

    def generate(self, context):
        self.generate_calls += 1
        raise AssertionError("Brain indisponível não deve gerar fala")


class FakeMonitor:
    def __init__(self, active=True):
        self.active = active

    def snapshot(self):
        return {
            "monitoring": self.active,
            "status": "ativo" if self.active else "parado",
            "username": "@teste",
            "connected": self.active,
            "room_id": "1" if self.active else None,
            "comments_received": 0,
        }

    def start(self, username):
        self.active = True
        return {"ok": True}

    def stop(self):
        self.active = False
        return {"ok": True}


def make_store(tmp_path):
    store = ProductStore(tmp_path / "products.json")
    store.add(
        name="Produto Teste",
        description="Produto cadastrado para teste.",
        key_benefits="Benefício real",
    )
    return store


def test_snapshot_is_read_only_and_never_calls_brain(tmp_path):
    brain = FakeBrain()
    runtime = AGCNVoiceRuntime(
        make_store(tmp_path),
        brain_provider=brain,
    )
    runtime.monitor = FakeMonitor(active=True)

    for _ in range(5):
        data = runtime.snapshot()
        assert data["status"] == "ativo"

    assert brain.calls == 0
    runtime.close()


def test_presenter_tick_calls_brain_when_live(tmp_path):
    brain = FakeBrain()
    runtime = AGCNVoiceRuntime(
        make_store(tmp_path),
        brain_provider=brain,
    )
    runtime.monitor = FakeMonitor(active=True)

    with runtime.lock:
        runtime._tick_presenter_locked()

    assert brain.calls >= 1
    runtime.close()



def test_brain_preflight_degrades_to_deterministic_presenter(tmp_path):
    brain = UnavailableBrain()
    runtime = AGCNVoiceRuntime(
        make_store(tmp_path),
        brain_provider=brain,
    )
    runtime.monitor = FakeMonitor(active=True)

    runtime._preflight_brain()

    assert brain.health_calls == 1
    assert brain.generate_calls == 0
    assert runtime.brain_provider is None
    assert runtime.brain_status == "fallback"
    assert "Ollama indisponível" in runtime.brain_degraded_reason
    assert runtime.presenter.__class__.__name__ == "PresenterV2"

    with runtime.lock:
        runtime._tick_presenter_locked()

    assert runtime.speeches_generated >= 1
    runtime.close()


def test_monitor_callback_only_queues_comment_for_worker(tmp_path):
    runtime = AGCNVoiceRuntime(make_store(tmp_path), brain_provider=FakeBrain())
    runtime.presenter.ingest_comment = lambda *_args: (_ for _ in ()).throw(
        AssertionError("monitor callback must not mutate Presenter")
    )

    runtime._on_monitor_event(
        "comment",
        {"time": 1, "user": "viewer", "text": "Qual o benefício?"},
    )

    assert len(runtime.pending_comment_ingest) == 1
    assert runtime.snapshot()["last_comment"]["user"] == "viewer"
    runtime.close()


def test_snapshot_does_not_wait_for_presenter_generation_lock(tmp_path):
    import threading

    runtime = AGCNVoiceRuntime(make_store(tmp_path), brain_provider=FakeBrain())
    runtime.monitor = FakeMonitor(active=True)
    result = []
    runtime.presenter_lock.acquire()
    try:
        worker = threading.Thread(target=lambda: result.append(runtime.snapshot()))
        worker.start()
        worker.join(timeout=1.0)
        assert not worker.is_alive(), "dashboard snapshot waited for Presenter lock"
        assert result and result[0]["status"] == "ativo"
    finally:
        runtime.presenter_lock.release()
        runtime.close()
