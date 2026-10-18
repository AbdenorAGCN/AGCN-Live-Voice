from __future__ import annotations

import re
import queue
import random
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.voice_profiles import list_voice_profiles
from desktop.app_controller import DesktopController
from desktop.ui_theme import (
    APP_STYLESHEET,
    apply_danger,
    apply_ghost,
    apply_primary,
)


VOICE_STYLE_OPTIONS = [
    ("Automático — recomendado", "auto"),
    ("Vendas — energética", "sales_energy"),
    ("Animada / empolgada", "excited"),
    ("Comemorativa", "celebratory"),
    ("Urgência controlada", "urgent_grounded"),
    ("Preço — confiante", "price_confident"),
    ("Tranquila / segura", "reassuring"),
    ("Empática — dor e solução", "empathetic_solution"),
    ("Desejo / imaginação de uso", "vivid_desire"),
    ("Resposta direta", "clear_answer"),
    ("Acolhedora", "welcoming"),
    ("Suspense / revelação", "suspense_reveal"),
]



VOICE_PROVIDER_LABELS = {
    "qwen3_hq_auto": "Qwen Local HQ",
    "qwen3_hq": "Qwen Local HQ",
    "gemini_premium": "Gemini Premium",
    "openai_live": "OpenAI Live",
}


def voice_provider_label(provider_id: str) -> str:
    return VOICE_PROVIDER_LABELS.get(
        str(provider_id or "").strip().casefold(),
        str(provider_id or "—"),
    )


def voice_profile_label(profile_id: str) -> str:
    target = str(profile_id or "").strip()
    for profile in list_voice_profiles():
        if profile.get("id") == target:
            return str(profile.get("label") or target)
    return target or "—"


def voice_style_label(style_id: str) -> str:
    target = str(style_id or "").strip().casefold()
    for label, item_id in VOICE_STYLE_OPTIONS:
        if item_id == target:
            return label.replace(" — recomendado", "")
    return (target or "automático").replace("_", " ").title()


def heading(text: str, subtitle: str = "") -> tuple[QLabel, QLabel]:
    title = QLabel(text)
    title.setObjectName("PageTitle")
    desc = QLabel(subtitle)
    desc.setObjectName("PageSubtitle")
    desc.setWordWrap(True)
    return title, desc


def group(title: str) -> QGroupBox:
    return QGroupBox(title)


def card(title: str, subtitle: str = "", *, variant: str = "Card"):
    box = QFrame()
    box.setObjectName(variant)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(10)

    header = QLabel(title)
    header.setObjectName("CardTitle")
    layout.addWidget(header)

    if subtitle:
        desc = QLabel(subtitle)
        desc.setObjectName("CardSubtitle")
        desc.setWordWrap(True)
        layout.addWidget(desc)

    return box, layout


def metric_block(label: str, value: str = "—"):
    box = QFrame()
    box.setObjectName("SoftCard")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(3)

    caption = QLabel(label)
    caption.setObjectName("MetricLabel")
    output = QLabel(value)
    output.setObjectName("MetricValue")
    output.setWordWrap(True)

    layout.addWidget(caption)
    layout.addWidget(output)
    return box, output


class DashboardPage(QWidget):
    def __init__(
        self,
        controller: DesktopController,
        *,
        open_voice_audio=None,
    ) -> None:
        super().__init__()
        self.setObjectName("PageRoot")
        self.controller = controller
        self.open_voice_audio = open_voice_audio

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("PageContent")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(14)

        header_row = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        title, desc = heading(
            "Dashboard",
            "Acompanhe a LIVE, as decisões da IA e o andamento da apresentação em tempo real.",
        )
        header_text.addWidget(title)
        header_text.addWidget(desc)
        header_row.addLayout(header_text, 1)

        self.live_state_badge = QLabel("Aguardando LIVE")
        self.live_state_badge.setObjectName("StatusInfo")
        self.live_state_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_row.addWidget(
            self.live_state_badge,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        layout.addLayout(header_row)

        top_row = QHBoxLayout()
        top_row.setSpacing(14)

        live_card, live_layout = card(
            "TikTok LIVE",
            "Informe o @username da transmissão que será monitorada.",
        )
        live_card.setMinimumWidth(330)
        self.username = QLineEdit()
        self.username.setPlaceholderText("@username")
        live_layout.addWidget(self.username)

        live_actions = QHBoxLayout()
        self.connect_btn = QPushButton("Iniciar monitoramento")
        apply_primary(self.connect_btn)
        self.stop_btn = QPushButton("Parar")
        apply_danger(self.stop_btn)
        self.stop_btn.setEnabled(False)
        live_actions.addWidget(self.connect_btn, 1)
        live_actions.addWidget(self.stop_btn)
        live_layout.addLayout(live_actions)

        self.connection_hint = QLabel(
            "Conecte uma LIVE para receber métricas e comentários."
        )
        self.connection_hint.setObjectName("Muted")
        self.connection_hint.setWordWrap(True)
        live_layout.addWidget(self.connection_hint)
        top_row.addWidget(live_card, 4)

        status_card, status_layout = card(
            "Status da LIVE",
            "Visão operacional do Presenter, produto e voz.",
        )
        metrics = QGridLayout()
        metrics.setHorizontalSpacing(10)
        metrics.setVerticalSpacing(10)

        metric, self.status = metric_block("LIVE", "Parado")
        metrics.addWidget(metric, 0, 0)
        metric, self.viewers = metric_block("Viewers")
        metrics.addWidget(metric, 0, 1)
        metric, self.likes = metric_block("Curtidas")
        metrics.addWidget(metric, 0, 2)
        metric, self.product = metric_block("Produto ativo", "Nenhum")
        metrics.addWidget(metric, 1, 0)
        metric, self.brain = metric_block("Brain")
        metrics.addWidget(metric, 1, 1)
        metric, self.voice = metric_block("Voz")
        metrics.addWidget(metric, 1, 2)
        status_layout.addLayout(metrics)

        status_footer = QHBoxLayout()
        status_footer.setSpacing(8)
        self.voice_style = QLabel("Estilo: Automático")
        self.voice_style.setObjectName("StatusInfo")
        self.presenter_mode = QLabel("Modo: Interativo")
        self.presenter_mode.setObjectName("StatusInfo")
        self.comments_pause = QLabel("Comentários: ativos")
        self.comments_pause.setObjectName("StatusGood")
        status_footer.addWidget(self.voice_style)
        status_footer.addWidget(self.presenter_mode)
        status_footer.addWidget(self.comments_pause)
        status_footer.addStretch(1)
        status_layout.addLayout(status_footer)
        top_row.addWidget(status_card, 7)
        layout.addLayout(top_row)

        middle_row = QHBoxLayout()
        middle_row.setSpacing(14)

        speech_card, speech_layout = card(
            "Falando agora",
            "Texto efetivamente enviado para o motor de voz.",
        )
        self.speech = QTextEdit()
        self.speech.setReadOnly(True)
        self.speech.setMinimumHeight(126)
        self.speech.setPlaceholderText(
            "Aguardando o início da apresentação..."
        )
        speech_layout.addWidget(self.speech)
        middle_row.addWidget(speech_card, 4)

        plan_card, plan_layout = card(
            "Andamento da apresentação",
            "O AGCN mantém visível o que está fazendo agora e o que vem em seguida.",
        )
        plan_row = QHBoxLayout()
        plan_row.setSpacing(10)

        def add_plan_step(title_text: str, current: bool = False):
            frame = QFrame()
            frame.setObjectName("PlanCurrent" if current else "SoftCard")
            step_layout = QVBoxLayout(frame)
            step_layout.setContentsMargins(13, 11, 13, 12)
            step_layout.setSpacing(6)
            label = QLabel(title_text)
            label.setObjectName("MetricLabel")
            value = QLabel("Aguardando decisão")
            value.setObjectName("ValueStrong")
            value.setWordWrap(True)
            value.setMinimumHeight(66)
            step_layout.addWidget(label)
            step_layout.addWidget(value, 1)
            plan_row.addWidget(frame, 1)
            return value

        self.current_action = add_plan_step("AGORA", True)
        self.next_action = add_plan_step("PRÓXIMO")
        self.after_action = add_plan_step("DEPOIS")
        plan_layout.addLayout(plan_row)
        middle_row.addWidget(plan_card, 7)
        layout.addLayout(middle_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(14)

        comments_card, comments_layout = card(
            "Comentários recentes",
            "Mensagens recebidas da LIVE.",
        )
        self.comments = QListWidget()
        self.comments.setObjectName("DataList")
        self.comments.setMinimumHeight(225)
        comments_layout.addWidget(self.comments)
        bottom_row.addWidget(comments_card, 4)

        queue_card, queue_layout = card(
            "Fila / Decisão da IA",
            "Itens priorizados para resposta ou retomada da venda.",
        )
        self.queue = QListWidget()
        self.queue.setObjectName("DataList")
        self.queue.setMinimumHeight(225)
        queue_layout.addWidget(self.queue)
        bottom_row.addWidget(queue_card, 4)

        voice_card, voice_layout = card(
            "Voz e áudio",
            "Resumo operacional. Os controles completos ficam na aba dedicada.",
        )
        voice_grid = QGridLayout()
        voice_grid.setHorizontalSpacing(10)
        voice_grid.setVerticalSpacing(8)

        self.voice_summary_engine = QLabel("—")
        self.voice_summary_profile = QLabel("—")
        self.voice_summary_speed = QLabel("—")
        self.voice_summary_expression = QLabel("—")
        self.voice_summary_style = QLabel("—")

        rows = (
            ("Motor ativo", self.voice_summary_engine),
            ("Perfil", self.voice_summary_profile),
            ("Velocidade", self.voice_summary_speed),
            ("Expressividade", self.voice_summary_expression),
            ("Estilo", self.voice_summary_style),
        )
        for row_index, (label_text, widget) in enumerate(rows):
            label = QLabel(label_text)
            label.setObjectName("MetricLabel")
            widget.setObjectName("ValueStrong")
            widget.setWordWrap(True)
            voice_grid.addWidget(label, row_index, 0)
            voice_grid.addWidget(widget, row_index, 1)
        voice_layout.addLayout(voice_grid)

        self.voice_advanced_btn = QPushButton("Ajustes avançados →")
        apply_primary(self.voice_advanced_btn)
        if callable(self.open_voice_audio):
            self.voice_advanced_btn.clicked.connect(self.open_voice_audio)
        voice_layout.addWidget(self.voice_advanced_btn)
        bottom_row.addWidget(voice_card, 4)

        layout.addLayout(bottom_row)

        self.diagnostic = QLabel("")
        self.diagnostic.setObjectName("InfoBanner")
        self.diagnostic.setWordWrap(True)
        self.diagnostic.setVisible(False)
        layout.addWidget(self.diagnostic)

        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        self.connect_btn.clicked.connect(self._connect)
        self.stop_btn.clicked.connect(self._stop)

        self._show_empty_lists()

    def _show_empty_lists(self) -> None:
        if self.comments.count() == 0:
            self.comments.addItem(
                "Aguardando comentários da LIVE..."
            )
        if self.queue.count() == 0:
            self.queue.addItem(
                "Aguardando decisões do Presenter..."
            )

    def _connect(self) -> None:
        try:
            result = self.controller.start_live(
                self.username.text().strip()
            )
            if not result.get("ok", True):
                QMessageBox.warning(
                    self,
                    "Não foi possível iniciar",
                    result.get("message") or "Falha ao iniciar.",
                )
        except Exception as exc:
            QMessageBox.critical(self, "Erro", str(exc))

    def _stop(self) -> None:
        try:
            self.controller.stop_live()
        except Exception as exc:
            QMessageBox.critical(self, "Erro", str(exc))

    def refresh(self, data: dict) -> None:
        monitoring = bool(data.get("monitoring"))
        self.connect_btn.setEnabled(not monitoring)
        self.stop_btn.setEnabled(monitoring)

        status_text = str(data.get("status") or "parado")
        self.status.setText(status_text.title())
        self.live_state_badge.setText(
            "LIVE conectada" if monitoring else "Aguardando LIVE"
        )
        self.live_state_badge.setObjectName(
            "StatusGood" if monitoring else "StatusInfo"
        )
        self.live_state_badge.style().unpolish(self.live_state_badge)
        self.live_state_badge.style().polish(self.live_state_badge)

        self.connection_hint.setText(
            "Conectado e monitorando comentários em tempo real."
            if monitoring
            else "Conecte uma LIVE para receber métricas e comentários."
        )

        self.viewers.setText(
            str(data.get("viewers"))
            if data.get("viewers") is not None
            else "—"
        )
        self.likes.setText(
            str(data.get("likes"))
            if data.get("likes") is not None
            else "—"
        )

        active = data.get("active_product") or {}
        self.product.setText(active.get("name") or "Nenhum")
        self.brain.setText(str(data.get("brain_provider") or "—"))

        voice = data.get("voice") or {}
        self.voice.setText(
            str(
                voice.get("active_provider")
                or voice.get("tts")
                or "Desativada"
            )
        )

        configured_style = str(
            voice.get("style_selection") or "auto"
        )
        current_style = str(
            voice.get("current_style")
            or (
                configured_style
                if configured_style not in {"", "auto"}
                else (data.get("current_speech") or {}).get("voice_style")
            )
            or "auto"
        )
        self.voice_style.setText(
            f"Estilo: {voice_style_label(current_style)}"
        )

        tts_cfg = dict(self.controller.config.get("tts") or {})
        provider_id = str(tts_cfg.get("provider") or "qwen3_hq_auto")
        selected_provider = voice_provider_label(provider_id)
        active_provider = str(
            voice.get("active_provider")
            or voice.get("tts")
            or ""
        ).strip()
        self.voice_summary_engine.setText(
            active_provider or selected_provider
        )
        self.voice_summary_profile.setText(
            voice_profile_label(
                str(tts_cfg.get("profile") or "female_fast")
            )
        )
        self.voice_summary_speed.setText(
            f"{float(tts_cfg.get('speed') or 1.28):.2f}x"
        )
        hq_cfg = dict(tts_cfg.get("qwen3_hq") or {})
        strength = float(
            tts_cfg.get(
                "expression_strength",
                hq_cfg.get("expression_strength", 1.0),
            )
        )
        self.voice_summary_expression.setText(
            f"{int(round(strength * 100))}%"
        )
        if configured_style in {"", "auto"}:
            live_style = voice_style_label(current_style)
            self.voice_summary_style.setText(
                "Automático"
                if current_style in {"", "auto"}
                else f"Automático · {live_style}"
            )
        else:
            self.voice_summary_style.setText(
                voice_style_label(configured_style)
            )

        mode = (
            "Produto"
            if data.get("presenter_mode") == "produto"
            else "Interativo"
        )
        self.presenter_mode.setText(f"Modo: {mode}")
        paused = int(data.get("comments_paused_seconds") or 0)
        self.comments_pause.setText(
            f"Comentários: pausados {paused}s"
            if paused
            else "Comentários: ativos"
        )

        current = dict(data.get("current_speech") or {})
        fixed_style = (
            configured_style
            if configured_style not in {"", "auto"}
            else ""
        )
        if fixed_style and current:
            current["voice_style"] = fixed_style

        speech = current.get("speech") or ""
        self.speech.setPlainText(speech)
        self.current_action.setText(
            self._action_text(current)
            if current
            else "Aguardando início da apresentação"
        )

        queued = [dict(item) for item in (data.get("queue") or [])]
        if fixed_style:
            for item in queued:
                item["voice_style"] = fixed_style
        product_locked = (
            data.get("presenter_mode") == "produto"
            and paused > 0
        )
        if product_locked:
            self.next_action.setText("Continuar falando do produto")
            self.after_action.setText(
                self._action_text(queued[0])
                if queued
                else "Aguardar nova decisão"
            )
        else:
            self.next_action.setText(
                self._action_text(queued[0])
                if queued
                else "Aguardar nova decisão"
            )
            self.after_action.setText(
                self._action_text(queued[1])
                if len(queued) > 1
                else "Manter fluxo da apresentação"
            )

        self.comments.clear()
        comments = list(data.get("comments") or [])
        if comments:
            for item in reversed(comments):
                user = item.get("user") or "—"
                text = item.get("text") or ""
                self.comments.addItem(f"{user}  ·  {text}")
        else:
            self.comments.addItem(
                "Aguardando comentários da LIVE..."
            )

        self.queue.clear()
        queue = list(data.get("queue") or [])
        if queue:
            for item in queue:
                priority = item.get("priority", 0)
                text = item.get("comment") or item.get("speech") or ""
                label = item.get("topic") or item.get("intent") or "decisão"
                label = str(label).replace("_", " ").title()
                self.queue.addItem(
                    f"{label}  ·  prioridade {priority}\n{text}"
                )
        else:
            self.queue.addItem(
                "Aguardando decisões do Presenter..."
            )

        presenter = data.get("presenter") or {}
        presenter_brain = presenter.get("brain") or {}
        error = (
            data.get("presenter_worker_error")
            or data.get("brain_degraded_reason")
            or presenter_brain.get("last_error")
            or voice.get("last_error")
            or data.get("error")
            or ""
        )
        self.diagnostic.setText(str(error))
        self.diagnostic.setVisible(bool(error))

    @staticmethod
    def _action_text(item: dict) -> str:
        if not item:
            return "—"
        kind = str(item.get("type") or "")
        topic = str(
            item.get("topic") or item.get("intent") or ""
        ).replace("_", " ")
        style = str(item.get("voice_style") or "").replace("_", " ")
        if kind == "reactive":
            user = str(item.get("user") or "cliente")
            comment = str(item.get("comment") or "").strip()
            base = f"Responder {user}"
            if comment:
                base += f"\n“{comment}”"
        else:
            base = (
                f"Apresentação do produto\n{topic}"
                if topic
                else "Continuar apresentação"
            )
        if style:
            base += f"\nVoz: {voice_style_label(style)}"
        return base


class PointsEditor(QWidget):
    """Editor visual reutilizável para campos cadastrados por tópicos."""

    def __init__(
        self,
        *,
        add_label: str,
        placeholder: str,
    ) -> None:
        super().__init__()
        self.placeholder = placeholder
        self.rows: list[tuple[QWidget, QLineEdit]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(6)
        root.addLayout(self.rows_layout)

        self.add_btn = QPushButton(add_label)
        self.add_btn.clicked.connect(lambda: self.add_point(""))
        root.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self.add_point("")

    def add_point(self, value: str = "") -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        edit = QLineEdit()
        edit.setPlaceholderText(self.placeholder)
        edit.setText(str(value or "").strip())

        remove = QPushButton("×")
        remove.setFixedWidth(34)
        remove.setToolTip("Remover este ponto")
        remove.clicked.connect(
            lambda _=False, target=row: self._remove_row(target)
        )

        layout.addWidget(edit, 1)
        layout.addWidget(remove)
        self.rows_layout.addWidget(row)
        self.rows.append((row, edit))

    def _remove_row(self, target: QWidget) -> None:
        if len(self.rows) == 1:
            self.rows[0][1].clear()
            return

        remaining = []
        for row, edit in self.rows:
            if row is target:
                row.setParent(None)
                row.deleteLater()
                continue
            remaining.append((row, edit))
        self.rows = remaining

    def values(self) -> list[str]:
        return [
            edit.text().strip()
            for _, edit in self.rows
            if edit.text().strip()
        ]

    def text(self) -> str:
        return "\n".join(self.values())

    def set_text(self, value) -> None:
        for row, _ in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = []

        if isinstance(value, (list, tuple, set)):
            values = [str(x).strip() for x in value if str(x).strip()]
        else:
            values = [
                x.strip()
                for x in re.split(r"[\n;|]+", str(value or ""))
                if x.strip()
            ]

        for item in values or [""]:
            self.add_point(item)


class ProductPage(QWidget):
    PERMANENT = [
        ("product_url", "Link do produto"),
        ("name", "Nome *"),
        ("brand", "Marca"),
        ("model", "Modelo"),
        ("category", "Categoria"),
        ("description", "Descrição por tópicos"),
        ("key_benefits", "Benefícios por tópicos"),
        ("problems_solved", "Problemas que resolve por tópicos"),
        ("differentials", "Diferenciais"),
        ("included_items", "Itens inclusos"),
        ("compatibility", "Compatibilidade"),
        ("size_info", "Tamanho / medidas"),
        ("battery_info", "Bateria / autonomia"),
        ("usage_info", "Modo de uso"),
        ("warranty", "Garantia"),
        ("limitations", "Limitações"),
        ("additional_info", "Informações adicionais"),
        ("image_url", "URL da imagem"),
    ]
    LIVE = [
        ("regular_price", "Preço regular"),
        ("current_price", "Preço atual"),
        ("discount", "Desconto (%)"),
        ("stock", "Estoque real disponível"),
        ("shipping_info", "Frete / entrega"),
        ("coupon", "Cupom"),
        ("live_offer_text", "Oferta real da LIVE"),
        ("promotion_note", "Urgência real / prazo promocional"),
    ]

    def __init__(self, controller: DesktopController) -> None:
        super().__init__()
        self.setObjectName("PageRoot")
        self.controller = controller
        self.current_id: str | None = None
        self.fields: dict[str, QWidget] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(14)
        title, desc = heading(
            "Produto",
            "Cadastre os fatos que o Presenter pode usar. O produto ativo é a fonte da verdade para todos os motores.",
        )
        root.addWidget(title)
        root.addWidget(desc)

        selector_card, selector_layout = card(
            "Produto ativo",
            "Selecione um cadastro existente ou crie um novo produto para esta LIVE.",
        )
        top = QHBoxLayout()
        self.product_combo = QComboBox()
        self.new_btn = QPushButton("Novo produto")
        self.activate_btn = QPushButton("Ativar")
        self.delete_btn = QPushButton("Excluir")
        apply_ghost(self.new_btn)
        apply_primary(self.activate_btn)
        apply_danger(self.delete_btn)
        top.addWidget(self.product_combo, 1)
        top.addWidget(self.new_btn)
        top.addWidget(self.activate_btn)
        top.addWidget(self.delete_btn)
        selector_layout.addLayout(top)
        root.addWidget(selector_card)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        container = QWidget()
        container.setObjectName("PageContent")
        stack = QVBoxLayout(container)
        stack.setContentsMargins(0, 0, 4, 0)
        stack.setSpacing(14)

        permanent_box = group("Ficha do produto")
        permanent_form = QFormLayout(permanent_box)
        permanent_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        permanent_form.setHorizontalSpacing(18)
        permanent_form.setVerticalSpacing(10)
        for field, label in self.PERMANENT:
            if field == "description":
                widget = PointsEditor(
                    add_label="+ Adicionar descrição",
                    placeholder="Ex.: bateria de até 6 dias",
                )
            elif field == "key_benefits":
                widget = PointsEditor(
                    add_label="+ Adicionar benefício",
                    placeholder="Ex.: áudio claro mesmo em chamadas",
                )
            elif field == "problems_solved":
                widget = PointsEditor(
                    add_label="+ Adicionar problema que resolve",
                    placeholder="Ex.: evita ficar preso a fios",
                )
            elif field in {
                "differentials",
                "included_items",
                "additional_info",
            }:
                widget = QTextEdit()
                widget.setFixedHeight(72)
            else:
                widget = QLineEdit()
            self.fields[field] = widget
            permanent_form.addRow(label, widget)
        stack.addWidget(permanent_box)

        live_box = group("Condições desta LIVE")
        live_form = QFormLayout(live_box)
        live_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        live_form.setHorizontalSpacing(18)
        live_form.setVerticalSpacing(10)
        for field, label in self.LIVE:
            widget = QLineEdit()
            self.fields[field] = widget
            live_form.addRow(label, widget)
        self.live_offer = QCheckBox(
            "Oferta ativa nesta LIVE (usar apenas condição real)"
        )
        live_form.addRow("", self.live_offer)
        stack.addWidget(live_box)

        self.save_btn = QPushButton("Salvar e usar este produto")
        apply_primary(self.save_btn)
        stack.addWidget(self.save_btn)
        stack.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        self.product_combo.currentIndexChanged.connect(self._selected)
        self.new_btn.clicked.connect(self.clear_form)
        self.activate_btn.clicked.connect(self._activate)
        self.delete_btn.clicked.connect(self._delete)
        self.save_btn.clicked.connect(self._save)

        self.reload_products()

    @staticmethod
    def _get_text(widget) -> str:
        if isinstance(widget, PointsEditor):
            return widget.text()
        if isinstance(widget, QTextEdit):
            return widget.toPlainText().strip()
        return widget.text().strip()

    @staticmethod
    def _set_text(widget, value) -> None:
        if isinstance(widget, PointsEditor):
            widget.set_text(value)
            return
        text = "" if value is None else str(value)
        if isinstance(widget, QTextEdit):
            widget.setPlainText(text)
        else:
            widget.setText(text)

    def reload_products(self) -> None:
        products = self.controller.products()
        active_id = None
        self.product_combo.blockSignals(True)
        self.product_combo.clear()
        self.product_combo.addItem("Selecione um produto", None)
        for product in products:
            label = product.get("name") or "Sem nome"
            if product.get("active"):
                label += "  • ATIVO"
                active_id = product.get("id")
            self.product_combo.addItem(label, product.get("id"))
        self.product_combo.blockSignals(False)

        if active_id:
            index = self.product_combo.findData(active_id)
            if index >= 0:
                self.product_combo.setCurrentIndex(index)
                self.load_product(active_id)

    def clear_form(self) -> None:
        self.current_id = None
        self.product_combo.setCurrentIndex(0)
        for widget in self.fields.values():
            self._set_text(widget, "")
        self.live_offer.setChecked(False)

    def _selected(self, index: int) -> None:
        product_id = self.product_combo.itemData(index)
        if product_id:
            self.load_product(product_id)

    def load_product(self, product_id: str) -> None:
        product = next(
            (
                item
                for item in self.controller.products()
                if item.get("id") == product_id
            ),
            None,
        )
        if not product:
            return

        self.current_id = product_id
        live = product.get("live_conditions") or {}
        for field, _ in self.PERMANENT:
            self._set_text(self.fields[field], product.get(field))
        for field, _ in self.LIVE:
            self._set_text(self.fields[field], live.get(field))
        self.live_offer.setChecked(bool(live.get("live_offer")))

    def _payload(self) -> dict:
        payload = {
            field: self._get_text(widget)
            for field, widget in self.fields.items()
        }
        payload["live_offer"] = self.live_offer.isChecked()
        return payload

    def _save(self) -> None:
        try:
            product = self.controller.save_product(
                self._payload(),
                product_id=self.current_id,
                activate=True,
            )
            self.current_id = product.get("id")
            self.reload_products()
            QMessageBox.information(
                self,
                "Produto salvo",
                "Produto salvo e ativado para a apresentação.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao salvar", str(exc))

    def _activate(self) -> None:
        if not self.current_id:
            return
        try:
            self.controller.activate_product(self.current_id)
            self.reload_products()
        except Exception as exc:
            QMessageBox.critical(self, "Erro", str(exc))

    def _delete(self) -> None:
        if not self.current_id:
            return
        answer = QMessageBox.question(
            self,
            "Excluir produto",
            "Deseja excluir este produto?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.controller.delete_product(self.current_id)
            self.clear_form()
            self.reload_products()
        except Exception as exc:
            QMessageBox.critical(self, "Erro", str(exc))


class VoiceAudioPage(QWidget):
    def __init__(self, controller: DesktopController) -> None:
        super().__init__()
        self.setObjectName("PageRoot")
        self.controller = controller

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(14)
        title, desc = heading(
            "Voz e áudio",
            "Configure o motor, interpretação, teste e saída de áudio. "
            "O Dashboard mostra apenas o resumo operacional.",
        )
        root.addWidget(title)
        root.addWidget(desc)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        container = QWidget()
        container.setObjectName("PageContent")
        stack = QVBoxLayout(container)
        stack.setContentsMargins(0, 0, 4, 0)
        stack.setSpacing(14)

        engine_box = group("Motor de voz e interpretação")
        self.engine_form = QFormLayout(engine_box)
        self.engine_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self.engine_form.setHorizontalSpacing(18)
        self.engine_form.setVerticalSpacing(10)

        self.voice_engine = QComboBox()
        self.voice_engine.addItem(
            "Local HQ — Qwen3-TTS (offline)",
            "qwen3_hq_auto",
        )
        self.voice_engine.addItem(
            "Gemini Premium TTS",
            "gemini_premium",
        )
        self.voice_engine.addItem(
            "OpenAI Live — GPT-Live 1",
            "openai_live",
        )

        self.gemini_model = QComboBox()
        self.gemini_model.addItem(
            "Gemini 3.8 Flash-Lite — rápido/econômico",
            "gemini-3.8-flash-lite-tts",
        )
        self.gemini_model.addItem(
            "Gemini 3.8 Flash — máxima qualidade",
            "gemini-3.8-flash-tts",
        )
        self.gemini_key = QLineEdit()
        self.gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key.setPlaceholderText(
            "Deixe vazio para manter a chave Gemini já salva"
        )
        self.gemini_status = QLabel("")
        self.gemini_status.setWordWrap(True)
        self.gemini_status.setStyleSheet(
            "color:#6B7280;font-size:12px;"
        )

        self.openai_live_model = QComboBox()
        self.openai_live_model.addItem(
            "GPT-Live 1 — voz mais natural/expressiva",
            "gpt-live-1",
        )
        self.openai_live_mode = QComboBox()
        self.openai_live_mode.addItem(
            "Controlado — texto validado pelo AGCN",
            "strict_speech",
        )
        self.openai_live_mode.addItem(
            "Agente guiado — em breve",
            "guided_agent",
        )
        live_mode_model = self.openai_live_mode.model()
        live_item_getter = getattr(live_mode_model, "item", None)
        guided_item = (
            live_item_getter(1)
            if callable(live_item_getter)
            else None
        )
        if guided_item is not None:
            guided_item.setEnabled(False)
            guided_item.setToolTip(
                "Será liberado depois dos testes do modo controlado."
            )

        self.openai_key = QLineEdit()
        self.openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key.setPlaceholderText(
            "Deixe vazio para manter a chave OpenAI já salva"
        )
        self.openai_live_status = QLabel("")
        self.openai_live_status.setWordWrap(True)
        self.openai_live_status.setStyleSheet(
            "color:#6B7280;font-size:12px;"
        )

        self.voice_profile = QComboBox()
        for profile in list_voice_profiles():
            self.voice_profile.addItem(
                profile["label"],
                profile["id"],
            )

        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setMinimum(80)
        self.speed_slider.setMaximum(160)
        self.speed_slider.setSingleStep(1)
        self.speed_slider.setValue(128)
        self.speed_label = QLabel("1.28x")
        self.speed_slider.valueChanged.connect(
            lambda value: self.speed_label.setText(
                f"{value / 100:.2f}x"
            )
        )
        speed_row = QHBoxLayout()
        speed_row.addWidget(self.speed_slider, 1)
        speed_row.addWidget(self.speed_label)

        self.expression_slider = QSlider(Qt.Orientation.Horizontal)
        self.expression_slider.setMinimum(0)
        self.expression_slider.setMaximum(150)
        self.expression_slider.setSingleStep(5)
        self.expression_slider.setValue(100)
        self.expression_label = QLabel("100%")
        self.expression_slider.valueChanged.connect(
            lambda value: self.expression_label.setText(f"{value}%")
        )
        expression_row = QHBoxLayout()
        expression_row.addWidget(self.expression_slider, 1)
        expression_row.addWidget(self.expression_label)

        self.voice_style_combo = QComboBox()
        for label, style_id in VOICE_STYLE_OPTIONS:
            self.voice_style_combo.addItem(label, style_id)

        self.voice_hint = QLabel(
            "No modo Automático, o AGCN escolhe a interpretação conforme "
            "comentário, preço, compra, objeção, escassez e etapa da venda. "
            "O modo manual força o estilo selecionado em todas as falas."
        )
        self.voice_hint.setWordWrap(True)
        self.voice_hint.setStyleSheet("color:#6B7280;font-size:12px;")

        self.engine_form.addRow("Motor", self.voice_engine)
        self.engine_form.addRow("Modelo Gemini", self.gemini_model)
        self.engine_form.addRow("Chave Gemini", self.gemini_key)
        self.engine_form.addRow("", self.gemini_status)
        self.engine_form.addRow("Modelo OpenAI Live", self.openai_live_model)
        self.engine_form.addRow("Modo OpenAI Live", self.openai_live_mode)
        self.engine_form.addRow("Chave OpenAI", self.openai_key)
        self.engine_form.addRow("", self.openai_live_status)
        self.engine_form.addRow("Perfil de voz", self.voice_profile)
        self.engine_form.addRow("Velocidade", speed_row)
        self.engine_form.addRow("Expressividade", expression_row)
        self.engine_form.addRow("Estilo", self.voice_style_combo)
        self.engine_form.addRow("", self.voice_hint)

        self.fallback_hint = QLabel(
            "Fallback automático: Gemini/OpenAI Live → Qwen Local HQ → "
            "Kokoro. Se uma API falhar, a LIVE tenta continuar com voz local."
        )
        self.fallback_hint.setWordWrap(True)
        self.fallback_hint.setObjectName("InfoBanner")
        self.engine_form.addRow("Continuidade", self.fallback_hint)
        stack.addWidget(engine_box)

        test_box = group("Teste e saída de áudio")
        test_form = QFormLayout(test_box)
        test_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        test_form.setHorizontalSpacing(18)
        test_form.setVerticalSpacing(10)
        self.voice_test_text = QLineEdit()
        self.voice_test_text.setText(
            "Gente, presta atenção nessa oferta porque esse produto "
            "está valendo muito a pena hoje!"
        )
        self.voice_test_text.setPlaceholderText(
            "Digite uma frase para ouvir com os ajustes atuais"
        )

        self.device = QComboBox()
        self.device.setEditable(True)
        self.refresh_devices_btn = QPushButton("Atualizar dispositivos")
        apply_ghost(self.refresh_devices_btn)
        self.test_voice_btn = QPushButton("Ouvir teste com estes ajustes")
        apply_primary(self.test_voice_btn)
        test_form.addRow("Texto de teste", self.voice_test_text)
        test_form.addRow("Saída de áudio", self.device)

        voice_actions = QHBoxLayout()
        voice_actions.addWidget(self.refresh_devices_btn)
        voice_actions.addWidget(self.test_voice_btn)
        test_form.addRow("", voice_actions)
        stack.addWidget(test_box)

        self.save_btn = QPushButton("Salvar Voz e áudio")
        apply_primary(self.save_btn)
        stack.addWidget(self.save_btn)
        stack.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        self.save_btn.clicked.connect(self._save)
        self.refresh_devices_btn.clicked.connect(self._devices)
        self.test_voice_btn.clicked.connect(self._test_voice)
        self.voice_engine.currentIndexChanged.connect(
            self._update_voice_provider_controls
        )

        self.load()

    def load(self) -> None:
        config = self.controller.config
        tts = config.get("tts") or {}
        gemini = tts.get("gemini") or {}
        openai_live = tts.get("openai_live") or {}
        audio = config.get("audio") or {}

        provider_id = str(tts.get("provider") or "qwen3_hq_auto")
        provider_index = self.voice_engine.findData(provider_id)
        if provider_index >= 0:
            self.voice_engine.setCurrentIndex(provider_index)

        gemini_model = str(
            gemini.get("model") or "gemini-3.8-flash-lite-tts"
        )
        gemini_index = self.gemini_model.findData(gemini_model)
        if gemini_index >= 0:
            self.gemini_model.setCurrentIndex(gemini_index)

        live_model = str(openai_live.get("model") or "gpt-live-1")
        live_index = self.openai_live_model.findData(live_model)
        if live_index >= 0:
            self.openai_live_model.setCurrentIndex(live_index)

        live_mode = str(
            openai_live.get("mode") or "strict_speech"
        )
        live_mode_index = self.openai_live_mode.findData(live_mode)
        self.openai_live_mode.setCurrentIndex(
            live_mode_index if live_mode_index >= 0 else 0
        )

        profile_id = str(tts.get("profile") or "female_fast")
        profile_index = self.voice_profile.findData(profile_id)
        if profile_index >= 0:
            self.voice_profile.setCurrentIndex(profile_index)

        speed = float(tts.get("speed") or 1.28)
        self.speed_slider.setValue(
            max(80, min(160, int(round(speed * 100))))
        )

        hq_cfg = dict(tts.get("qwen3_hq") or {})
        strength = float(
            tts.get(
                "expression_strength",
                hq_cfg.get("expression_strength", 1.0),
            )
        )
        self.expression_slider.setValue(
            max(0, min(150, int(round(strength * 100))))
        )

        style = str(
            tts.get("voice_style")
            or hq_cfg.get("voice_style")
            or "auto"
        )
        style_index = self.voice_style_combo.findData(style)
        self.voice_style_combo.setCurrentIndex(
            style_index if style_index >= 0 else 0
        )

        self.device.setCurrentText(
            str(audio.get("output_device") or "")
        )
        self.gemini_status.setText(
            "Chave Gemini salva com segurança"
            if self.controller.gemini_key_saved()
            else "Chave Gemini ainda não configurada"
        )
        self.openai_live_status.setText(
            (
                "Chave OpenAI salva · pronta para OpenAI Live."
            )
            if self.controller.api_key_saved()
            else (
                "Chave OpenAI ainda não configurada. "
                "Se o motor OpenAI Live for escolhido, o fallback local "
                "continua disponível."
            )
        )
        self._update_voice_provider_controls()

    def _patch(self) -> dict:
        """Salva somente controles expostos na UI.

        Campos técnicos ocultos (URLs, timeouts, pack_dir etc.) permanecem
        intactos no ConfigStore graças ao deep-merge.
        """
        return {
            "tts": {
                "provider": (
                    self.voice_engine.currentData()
                    or "qwen3_hq_auto"
                ),
                "profile": (
                    self.voice_profile.currentData()
                    or "female_fast"
                ),
                "speed": self.speed_slider.value() / 100.0,
                "expressive": self.expression_slider.value() > 0,
                "expression_strength": (
                    self.expression_slider.value() / 100.0
                ),
                "voice_style": (
                    self.voice_style_combo.currentData() or "auto"
                ),
                "qwen3_hq": {
                    "expressive": self.expression_slider.value() > 0,
                    "expression_strength": (
                        self.expression_slider.value() / 100.0
                    ),
                    "voice_style": (
                        self.voice_style_combo.currentData() or "auto"
                    ),
                },
                "gemini": {
                    "model": (
                        self.gemini_model.currentData()
                        or "gemini-3.8-flash-lite-tts"
                    ),
                    "api_key_env": "GEMINI_API_KEY",
                },
                "openai_live": {
                    "model": (
                        self.openai_live_model.currentData()
                        or "gpt-live-1"
                    ),
                    "mode": (
                        self.openai_live_mode.currentData()
                        or "strict_speech"
                    ),
                    "api_key_env": "OPENAI_API_KEY",
                },
            },
            "audio": {
                "output_device": self.device.currentText().strip(),
            },
        }

    def _save(self) -> None:
        try:
            openai_key = self.openai_key.text().strip()
            gemini_key = self.gemini_key.text().strip()
            self.controller.save_settings(
                self._patch(),
                openai_key=openai_key if openai_key else None,
                gemini_key=gemini_key if gemini_key else None,
            )
            self.openai_key.clear()
            self.gemini_key.clear()
            self.load()
            QMessageBox.information(
                self,
                "Voz e áudio",
                "Configurações de voz salvas. O runtime foi recarregado.",
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Erro ao salvar Voz e áudio",
                str(exc),
            )

    def _set_provider_row_visible(self, widget, visible: bool) -> None:
        widget.setVisible(visible)
        label = self.engine_form.labelForField(widget)
        if label is not None:
            label.setVisible(visible)

    def _update_voice_provider_controls(self) -> None:
        provider = self.voice_engine.currentData()
        is_gemini = provider == "gemini_premium"
        is_openai_live = provider == "openai_live"

        for widget in (
            self.gemini_model,
            self.gemini_key,
            self.gemini_status,
        ):
            self._set_provider_row_visible(widget, is_gemini)

        for widget in (
            self.openai_live_model,
            self.openai_live_mode,
            self.openai_key,
            self.openai_live_status,
        ):
            self._set_provider_row_visible(widget, is_openai_live)

    def _devices(self) -> None:
        try:
            current = self.device.currentText()
            devices = self.controller.list_audio_devices()
            self.device.clear()
            self.device.addItem("")
            self.device.addItems(devices)
            if current:
                self.device.setCurrentText(current)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Dispositivos de áudio",
                str(exc),
            )

    def _test_voice(self) -> None:
        try:
            if (
                self.voice_engine.currentData() == "gemini_premium"
                and self.gemini_key.text().strip()
            ):
                self.controller.set_gemini_key(
                    self.gemini_key.text().strip()
                )
                self.gemini_key.clear()
                self.gemini_status.setText(
                    "Chave Gemini salva com segurança"
                )

            if (
                self.voice_engine.currentData() == "openai_live"
                and self.openai_key.text().strip()
            ):
                self.controller.set_openai_key(
                    self.openai_key.text().strip()
                )
                self.openai_key.clear()
                self.openai_live_status.setText(
                    "Chave OpenAI salva · pronta para OpenAI Live."
                )

            test_text = self.voice_test_text.text().strip()
            ok, message = self.controller.test_voice(
                text=(
                    test_text
                    or "Teste de voz do AGCN Live Voice."
                ),
                config_override=self._patch(),
            )
            QMessageBox.information(
                self,
                "Teste de voz",
                ("OK — " if ok else "Falhou — ") + message,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Teste de voz", str(exc))



class TestPage(QWidget):
    COMMENTS = ["Qual é o preço?", "Como funciona?", "Quais são os benefícios?", "Tem mais detalhes?", "Como eu uso?", "O que vem na embalagem?", "Vale a pena comprar?", "Acabei de chegar, me conta mais!"]

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.stop_event = threading.Event()
        self.events = queue.Queue()
        self.worker = None
        self.viewers = 0
        self.comments_count = 0
        self.answers_count = 0
        self.speech_count = 0
        self.latency = None
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(14)
        title, desc = heading("Teste", "Simule uma sessão antes da LIVE real: comentários fictícios, respostas da IA e teste do motor de voz.")
        root.addWidget(title)
        root.addWidget(desc)
        note = QLabel("Modo simulado: comentários e espectadores são fictícios; nada é enviado ao TikTok.")
        note.setObjectName("InfoBanner")
        note.setWordWrap(True)
        root.addWidget(note)
        product_card, product_layout = card("Produto ativo", "O Presenter usa o cadastro ativo na aba Produto.")
        self.product = QLabel()
        self.product.setObjectName("ValueStrong")
        self.product.setWordWrap(True)
        product_layout.addWidget(self.product)
        root.addWidget(product_card)
        grid = QGridLayout()
        self.m_viewers = self.metric(grid, "Espectadores simulados", "0", 0, 0)
        self.m_comments = self.metric(grid, "Comentários gerados", "0", 0, 1)
        self.m_answers = self.metric(grid, "Respostas da IA", "0", 0, 2)
        self.m_speech = self.metric(grid, "Falas na fila de voz", "0", 1, 0)
        self.m_latency = self.metric(grid, "Tempo de geração", "—", 1, 1)
        self.m_voice = self.metric(grid, "Motor de voz", "Aguardando", 1, 2)
        self.m_speed = self.metric(grid, "Velocidade configurada", "—", 2, 0)
        root.addLayout(grid)
        actions = QHBoxLayout()
        self.start_btn = QPushButton("▶  Iniciar teste")
        apply_primary(self.start_btn)
        self.stop_btn = QPushButton("■  Encerrar teste")
        apply_danger(self.stop_btn)
        self.stop_btn.setEnabled(False)
        self.status = QLabel("Teste parado")
        self.status.setObjectName("StatusInfo")
        actions.addWidget(self.start_btn)
        actions.addWidget(self.stop_btn)
        actions.addWidget(self.status, 1)
        root.addLayout(actions)
        log_card, log_layout = card("Atividade da IA", "Comentários simulados e falas produzidas.")
        self.log = QListWidget()
        self.log.setObjectName("DataList")
        self.log.setMinimumHeight(200)
        log_layout.addWidget(self.log)
        root.addWidget(log_card, 1)
        self.start_btn.clicked.connect(self.start_test)
        self.stop_btn.clicked.connect(self.stop_test)
        self.poll = QTimer(self)
        self.poll.setInterval(250)
        self.poll.timeout.connect(self.drain_events)
        self.poll.start()
        self.reload_product()

    @staticmethod
    def metric(grid, label, value, row, col):
        frame, output = metric_block(label, value)
        grid.addWidget(frame, row, col)
        return output

    def reload_product(self):
        p = self.controller.active_product() or {}
        details = [str(p.get("name") or "Nenhum produto ativo")]
        if p.get("current_price") is not None:
            details.append("Preço: R$ " + str(p.get("current_price")))
        if p.get("description"):
            details.append(str(p.get("description")))
        benefits = p.get("benefits")
        if benefits:
            details.append("Benefícios: " + (", ".join(map(str, benefits)) if isinstance(benefits, list) else str(benefits)))
        self.product.setText(" · ".join(details))
        self.start_btn.setEnabled(bool(p.get("name")) and not (self.worker and self.worker.is_alive()))

    def start_test(self):
        if self.worker and self.worker.is_alive():
            return
        if self.controller.snapshot().get("monitoring"):
            QMessageBox.warning(self, "Teste indisponível", "Encerre o monitoramento da LIVE real antes de iniciar a simulação.")
            return
        if not (self.controller.active_product() or {}).get("name"):
            QMessageBox.warning(self, "Produto necessário", "Cadastre e ative um produto antes de testar.")
            return
        self.stop_event.clear()
        self.viewers = random.randint(8, 46)
        self.comments_count = self.answers_count = self.speech_count = 0
        self.latency = None
        self.log.clear()
        self.update_metrics()
        self.status.setText("Simulação em andamento")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.add_log("Sistema", "Teste iniciado usando o produto ativo.")
        self.worker = threading.Thread(target=self.simulate, daemon=True, name="agcn-test-simulation")
        self.worker.start()

    def stop_test(self):
        self.stop_event.set()
        try:
            if self.controller.runtime.voice_service is not None:
                self.controller.runtime.voice_service.clear_pending()
        except Exception:
            pass
        self.status.setText("Teste parado")
        self.stop_btn.setEnabled(False)
        self.start_btn.setEnabled(bool((self.controller.active_product() or {}).get("name")))

    def simulate(self):
        voice = self.controller.runtime.voice_service
        if voice is not None:
            try:
                voice.start()
            except Exception as exc:
                self.events.put(("voice_error", str(exc)))
        cycle = 0
        while not self.stop_event.is_set():
            comment = random.choice(self.COMMENTS)
            user = "cliente_" + str(random.randint(10, 999))
            started = time.monotonic()
            self.events.put(("comment", (user, comment)))
            try:
                result = self.controller.test_presenter(comment=comment, user=user)
                elapsed = time.monotonic() - started
                item = result.get("speech") if isinstance(result, dict) else None
                speech = str(item.get("speech") or "").strip() if isinstance(item, dict) else ""
                if not speech and cycle % 3 == 2:
                    proactive = self.controller.test_presenter()
                    item = proactive.get("speech") if isinstance(proactive, dict) else None
                    speech = str(item.get("speech") or "").strip() if isinstance(item, dict) else ""
                if speech:
                    queued = False
                    if voice is not None:
                        try:
                            voice.enqueue(speech, priority=40, metadata={"type": "proactive", "topic": "test", "tactic": "test", "voice_style": "sales_energy", "test_mode": True})
                            queued = True
                        except Exception as exc:
                            self.events.put(("voice_error", str(exc)))
                    self.events.put(("answer", (speech, elapsed, queued)))
                else:
                    self.events.put(("ignored", (result.get("brain_error") or "A IA não gerou resposta para esta mensagem.", elapsed)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            cycle += 1
            self.viewers = max(0, min(250, self.viewers + random.randint(-5, 8)))
            self.events.put(("viewers", self.viewers))
            if self.stop_event.wait(4.0):
                break
        self.events.put(("finished", None))

    def drain_events(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "comment":
                self.comments_count += 1
                self.add_log(value[0], "Comentário simulado: “" + value[1] + "”")
            elif kind == "answer":
                self.answers_count += 1
                speech, elapsed, queued = value
                self.latency = elapsed
                if queued:
                    self.speech_count += 1
                    self.m_voice.setText("Fila de voz ativa")
                self.add_log("IA", speech)
            elif kind == "ignored":
                self.latency = value[1]
                self.add_log("IA", "Sem resposta: " + str(value[0]))
            elif kind == "viewers":
                self.viewers = int(value)
            elif kind == "voice_error":
                self.m_voice.setText("Erro na voz")
                self.add_log("Voz", str(value))
            elif kind == "error":
                self.add_log("Erro", str(value))
            elif kind == "finished":
                self.status.setText("Teste parado")
                self.stop_btn.setEnabled(False)
                self.start_btn.setEnabled(bool((self.controller.active_product() or {}).get("name")))
            self.update_metrics()

    def update_metrics(self):
        self.m_viewers.setText(str(self.viewers))
        self.m_comments.setText(str(self.comments_count))
        self.m_answers.setText(str(self.answers_count))
        self.m_speech.setText(str(self.speech_count))
        self.m_latency.setText((str(round(self.latency, 2)) + " s") if self.latency is not None else "—")
        tts = self.controller.config.get("tts") or {}
        self.m_speed.setText(str(tts.get("speed") or 1.28) + "×")

    def add_log(self, who, message):
        self.log.insertItem(0, time.strftime("%H:%M:%S") + " · " + str(who) + ": " + str(message))
        while self.log.count() > 100:
            self.log.takeItem(self.log.count() - 1)


class SettingsPage(QWidget):
    def __init__(self, controller: DesktopController) -> None:
        super().__init__()
        self.setObjectName("PageRoot")
        self.controller = controller

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(14)
        title, desc = heading(
            "Configurações",
            "Configure o Presenter Brain e o diagnóstico geral. "
            "Voz e áudio agora ficam em uma aba própria.",
        )
        root.addWidget(title)
        root.addWidget(desc)

        brain_box = group("Presenter Brain")
        brain_box.setMaximumWidth(920)
        brain_form = QFormLayout(brain_box)
        brain_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        brain_form.setHorizontalSpacing(18)
        brain_form.setVerticalSpacing(10)
        self.brain_provider = QComboBox()
        self.brain_provider.addItem(
            "AGCN Local — Qwen3 4B (incluído)",
            "agcn_local",
        )
        self.brain_provider.addItem(
            "OpenAI API",
            "openai",
        )
        self.brain_provider.addItem(
            "API compatível com OpenAI",
            "openai_compatible",
        )
        self.local_brain_status = QLabel(
            "Qwen3 4B + llama.cpp fazem parte do instalador completo. "
            "Não é necessário instalar Ollama."
        )
        self.local_brain_status.setWordWrap(True)
        self.local_brain_status.setObjectName("InfoBanner")
        self.ollama_url = QLineEdit()
        self.ollama_model = QLineEdit()
        self.api_url = QLineEdit()
        self.api_model = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText(
            "Deixe vazio para manter a chave OpenAI já salva"
        )
        self.fallback = QCheckBox(
            "Usar AGCN Local se a API falhar"
        )
        brain_form.addRow("Provider", self.brain_provider)
        brain_form.addRow("Brain local", self.local_brain_status)
        brain_form.addRow("API URL", self.api_url)
        brain_form.addRow("Modelo API", self.api_model)
        brain_form.addRow("Chave OpenAI", self.api_key)
        brain_form.addRow("", self.fallback)

        brain_actions = QHBoxLayout()
        self.test_brain_btn = QPushButton("Testar Brain")
        apply_ghost(self.test_brain_btn)
        self.doctor_btn = QPushButton("Diagnóstico completo")
        apply_ghost(self.doctor_btn)
        self.key_status = QLabel("")
        brain_actions.addWidget(self.test_brain_btn)
        brain_actions.addWidget(self.doctor_btn)
        brain_actions.addWidget(self.key_status, 1)
        brain_form.addRow("", brain_actions)
        root.addWidget(brain_box)

        self.save_btn = QPushButton("Salvar configurações do Brain")
        self.save_btn.setMaximumWidth(920)
        apply_primary(self.save_btn)
        root.addWidget(self.save_btn)
        root.addStretch(1)

        self.save_btn.clicked.connect(self._save)
        self.test_brain_btn.clicked.connect(self._test_brain)
        self.doctor_btn.clicked.connect(self._doctor)

        self.load()

    def load(self) -> None:
        config = self.controller.config
        brain = config.get("brain") or {}
        ollama = brain.get("ollama") or {}
        api = brain.get("api") or {}

        provider_id = str(brain.get("provider") or "agcn_local")
        if provider_id in {"qwen_local", "local"}:
            provider_id = "agcn_local"
        provider_index = self.brain_provider.findData(provider_id)
        self.brain_provider.setCurrentIndex(
            provider_index if provider_index >= 0 else 0
        )
        self.ollama_url.setText(
            str(ollama.get("base_url") or "http://127.0.0.1:11434")
        )
        self.ollama_model.setText(
            str(ollama.get("model") or "qwen3:4b")
        )
        self.api_url.setText(
            str(api.get("base_url") or "https://api.openai.com/v1")
        )
        self.api_model.setText(str(api.get("model") or ""))
        self.fallback.setChecked(bool(brain.get("fallback_local", True)))
        self.key_status.setText(
            "Chave OpenAI salva"
            if self.controller.api_key_saved()
            else "Chave OpenAI opcional; AGCN Local funciona offline"
        )

    def _patch(self) -> dict:
        return {
            "brain": {
                "provider": (
                    self.brain_provider.currentData()
                    or "agcn_local"
                ),
                "fallback_local": self.fallback.isChecked(),
                "ollama": {
                    "base_url": self.ollama_url.text().strip(),
                    "model": self.ollama_model.text().strip(),
                },
                "api": {
                    "base_url": self.api_url.text().strip(),
                    "model": self.api_model.text().strip(),
                    "api_key_env": "OPENAI_API_KEY",
                },
            },
        }

    def _save(self) -> None:
        try:
            key = self.api_key.text().strip()
            self.controller.save_settings(
                self._patch(),
                openai_key=key if key else None,
            )
            self.api_key.clear()
            self.load()
            QMessageBox.information(
                self,
                "Configurações",
                "Configurações do Brain salvas. O runtime foi recarregado.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao salvar", str(exc))

    def _apply_settings_before_test(self) -> None:
        key = self.api_key.text().strip()
        self.controller.save_settings(
            self._patch(),
            openai_key=key if key else None,
        )
        if key:
            self.api_key.clear()

    def _test_brain(self) -> None:
        try:
            self._apply_settings_before_test()
            ok, message = self.controller.brain_health()
            QMessageBox.information(
                self,
                "Teste do Brain",
                ("OK — " if ok else "Falhou — ") + message,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Teste do Brain", str(exc))

    def _doctor(self) -> None:
        try:
            ok, message = self.controller.diagnostics()
            box = QMessageBox(self)
            box.setWindowTitle("Diagnóstico AGCN")
            box.setIcon(
                QMessageBox.Icon.Information
                if ok
                else QMessageBox.Icon.Warning
            )
            box.setText(
                "Pronto para teste de LIVE"
                if ok
                else "Há pendências no ambiente"
            )
            box.setDetailedText(message)
            box.setInformativeText(message)
            box.exec()
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Diagnóstico AGCN",
                str(exc),
            )


class MainWindow(QMainWindow):
    def __init__(
        self,
        controller: DesktopController | None = None,
    ) -> None:
        super().__init__()
        self.controller = controller or DesktopController()

        self.setWindowTitle(
            "AGCN Live Voice — Sua voz inteligente para vender ao vivo."
        )
        self.setMinimumSize(1180, 720)
        self.resize(1440, 900)

        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(228)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(12, 18, 12, 14)
        side_layout.setSpacing(8)

        brand = QLabel("AGCN Live Voice")
        brand.setObjectName("BrandTitle")
        brand.setWordWrap(True)
        side_layout.addWidget(brand)

        brand_subtitle = QLabel("Apresentador inteligente para LIVE")
        brand_subtitle.setObjectName("BrandSubtitle")
        brand_subtitle.setWordWrap(True)
        side_layout.addWidget(brand_subtitle)
        side_layout.addSpacing(12)

        self.nav = QListWidget()
        self.nav.setObjectName("SidebarNav")
        for name in (
            "Dashboard",
            "Produto",
            "Voz e áudio",
            "Configurações",
            "Teste",
        ):
            self.nav.addItem(QListWidgetItem(name))
        side_layout.addWidget(self.nav, 1)

        footer = QLabel("●  Sistema pronto")
        footer.setObjectName("SidebarFooter")
        side_layout.addWidget(footer)

        self.pages = QStackedWidget()
        self.dashboard = DashboardPage(
            self.controller,
            open_voice_audio=lambda: self.nav.setCurrentRow(2),
        )
        self.product_page = ProductPage(self.controller)
        self.voice_audio_page = VoiceAudioPage(self.controller)
        self.settings_page = SettingsPage(self.controller)
        self.test_page = TestPage(self.controller)
        self.pages.addWidget(self.dashboard)
        self.pages.addWidget(self.product_page)
        self.pages.addWidget(self.voice_audio_page)
        self.pages.addWidget(self.settings_page)
        self.pages.addWidget(self.test_page)

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.currentRowChanged.connect(self._page_changed)
        self.nav.setCurrentRow(0)

        outer.addWidget(sidebar)

        content = QWidget()
        content.setObjectName("AppRoot")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(20, 10, 22, 10)
        top_bar_layout.setSpacing(10)

        self.top_connection = QLabel("Aguardando LIVE")
        self.top_connection.setObjectName("StatusInfo")
        self.top_live = QLabel("TikTok LIVE · não conectado")
        self.top_live.setObjectName("Muted")
        self.top_voice = QLabel("Voz · carregando")
        self.top_voice.setObjectName("Muted")

        top_bar_layout.addWidget(self.top_connection)
        top_bar_layout.addWidget(self.top_live)
        top_bar_layout.addStretch(1)
        top_bar_layout.addWidget(self.top_voice)

        content_layout.addWidget(top_bar)
        content_layout.addWidget(self.pages, 1)
        outer.addWidget(content, 1)

        self.setStyleSheet(APP_STYLESHEET)

        self.timer = QTimer(self)
        self.timer.setInterval(700)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def _page_changed(self, index: int) -> None:
        if index == 1:
            self.product_page.reload_products()
        elif index == 2:
            self.voice_audio_page.load()
        elif index == 3:
            self.settings_page.load()
        elif index == 4:
            self.test_page.reload_product()

    def refresh(self) -> None:
        try:
            data = self.controller.snapshot()
            self.dashboard.refresh(data)
            self._refresh_top_bar(data)
        except Exception as exc:
            self.dashboard.diagnostic.setText(str(exc))
            self.dashboard.diagnostic.setVisible(True)

    def _refresh_top_bar(self, data: dict) -> None:
        monitoring = bool(data.get("monitoring"))
        username = str(data.get("username") or "").strip()
        self.top_connection.setText(
            "●  Conectado" if monitoring else "●  Aguardando LIVE"
        )
        self.top_connection.setObjectName(
            "StatusGood" if monitoring else "StatusInfo"
        )
        self.top_connection.style().unpolish(self.top_connection)
        self.top_connection.style().polish(self.top_connection)

        self.top_live.setText(
            f"TikTok LIVE · {username}"
            if username
            else "TikTok LIVE · não conectado"
        )
        voice = data.get("voice") or {}
        tts_cfg = dict(self.controller.config.get("tts") or {})
        provider = (
            voice.get("active_provider")
            or voice.get("tts")
            or voice_provider_label(
                str(tts_cfg.get("provider") or "qwen3_hq_auto")
            )
        )
        self.top_voice.setText(f"Voz · {provider}")

    def closeEvent(self, event) -> None:
        try:
            self.controller.close()
        finally:
            event.accept()
