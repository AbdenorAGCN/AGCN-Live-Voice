(() => {
  "use strict";
  const support = ("speechSynthesis" in window) && ("SpeechSynthesisUtterance" in window);
  const KEY = "agcn-live-voice-web-settings-v1";
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (_) {}
  let enabled = false;
  let speaking = false;
  let lastSpeechKey = "";
  let lastSpeechText = "";
  let availableVoices = [];

  const bar = document.createElement("section");
  bar.id = "agcnVoiceBar";
  bar.setAttribute("aria-label", "Controle de áudio do AGCN Live Voice");
  bar.innerHTML = [
    '<div class="vb-head">',
    '<div><div class="vb-title"><span class="vb-indicator" id="vbLed"></span>Áudio do navegador</div>',
    '<div class="vb-sub" id="vbStatus">Desativado · clique em Ativar voz</div></div>',
    '<div class="vb-actions"><button id="vbToggle" class="primary">Ativar voz</button>',
    '<button id="vbStop">Silenciar</button></div></div>',
    '<div class="vb-extend">',
    '<label>Voz em português<select id="vbVoice" aria-label="Voz"></select></label>',
    '<label>Velocidade<input id="vbSpeed" type="number" min="0.8" max="1.6" step="0.1" value="1.0"></label>',
    '</div>',
    '<div class="vb-text" id="vbLastText">As falas produzidas pelo Presenter poderão ser reproduzidas aqui.</div>',
    '<div class="vb-utilities"><button id="vbTest">Ouvir teste</button>',
    '<button id="vbReplay">Reproduzir última fala</button>',
    '<button id="vbLogout">Sair da conta</button></div>',
    '<div class="vb-note">O áudio sai neste dispositivo. Não substitui diretamente o microfone virtual do TikTok LIVE Studio.</div>'
  ].join("");
  document.body.appendChild(bar);

  const el = (id) => document.getElementById(id);
  const status = (message) => { el("vbStatus").textContent = message; };
  const setLed = () => {
    el("vbLed").className = "vb-indicator" + (speaking ? " speaking" : enabled ? " on" : "");
    el("vbToggle").classList.toggle("on", enabled);
    el("vbToggle").textContent = enabled ? "Voz ativada" : "Ativar voz";
  };
  const remember = () => {
    try {
      localStorage.setItem(KEY, JSON.stringify({
        voice: el("vbVoice").value,
        speed: el("vbSpeed").value
      }));
    } catch (_) {}
  };
  const refreshVoices = () => {
    if (!support) return;
    availableVoices = speechSynthesis.getVoices();
    const portuguese = availableVoices.filter(v => /^pt(?:-|$)/i.test(v.lang));
    const choices = portuguese.length ? portuguese : availableVoices;
    const select = el("vbVoice");
    const previous = select.value || saved.voice || "";
    select.replaceChildren();
    for (const voice of choices) {
      const option = document.createElement("option");
      option.value = voice.voiceURI;
      option.textContent = voice.name + " (" + voice.lang + ")";
      select.appendChild(option);
    }
    if ([...select.options].some(o => o.value === previous)) select.value = previous;
  };

  const stopSpeech = () => {
    if (support) speechSynthesis.cancel();
    speaking = false;
    setLed();
  };
  const say = (text, isTest = false) => {
    const content = String(text || "").trim();
    if (!support || !content) return;
    stopSpeech();
    const utterance = new SpeechSynthesisUtterance(content);
    const selected = availableVoices.find(v => v.voiceURI === el("vbVoice").value);
    if (selected) utterance.voice = selected;
    utterance.lang = selected?.lang || "pt-BR";
    utterance.rate = Number(el("vbSpeed").value) || 1;
    utterance.volume = 1;
    utterance.onstart = () => { speaking = true; setLed(); status(isTest ? "Reproduzindo teste" : "Falando agora"); };
    const finished = () => {
      speaking = false;
      setLed();
      status(enabled ? "Pronto para a próxima fala" : "Desativado");
    };
    utterance.onend = finished;
    utterance.onerror = finished;
    speechSynthesis.speak(utterance);
  };

  el("vbSpeed").value = String(saved.speed || "1.0");
  el("vbSpeed").addEventListener("change", () => {
    const value = Math.max(.8, Math.min(1.6, Number(el("vbSpeed").value) || 1));
    el("vbSpeed").value = value.toFixed(1);
    remember();
  });
  el("vbVoice").onchange = remember;
  if (support) {
    refreshVoices();
    speechSynthesis.addEventListener?.("voiceschanged", refreshVoices);
  } else {
    status("Este navegador não oferece síntese de voz. Use Chrome ou Edge.");
    el("vbToggle").disabled = true;
    el("vbTest").disabled = true;
    el("vbReplay").disabled = true;
  }

  el("vbToggle").onclick = () => {
    enabled = !enabled;
    if (!enabled) stopSpeech();
    status(enabled ? "Ativado · aguardando a próxima fala da LIVE" : "Desativado");
    setLed();
    if (enabled) say("Áudio do AGCN Live Voice ativado.", true);
  };
  el("vbStop").onclick = () => { enabled = false; stopSpeech(); status("Desativado"); };
  el("vbTest").onclick = () => say("Olá! Este é o teste de voz do AGCN Live Voice.", true);
  el("vbReplay").onclick = () => {
    const lab = (document.getElementById("labSpeech")?.textContent || "").trim();
    if (lab && lab !== "Aguardando..." && !lab.startsWith("Não foi possível")) say(lab, true);
    else if (lastSpeechText) say(lastSpeechText, true);
    else status("Nenhuma fala disponível para reproduzir.");
  };
  el("vbLogout").onclick = async () => {
    enabled = false; stopSpeech();
    try { await fetch("/auth/logout", {method: "POST"}); } catch (_) {}
    location.assign("/login");
  };

  function onSnapshot(data) {
    if (!data || typeof data !== "object") return;
    const current = data.current_speech || {};
    const speech = String(current.speech || "").trim();
    if (!speech) return;
    const key = JSON.stringify([
      current.speech, current.started_at || current.start_at || current.time || null,
      current.type, current.user
    ]);
    if (key === lastSpeechKey) return;
    lastSpeechKey = key;
    lastSpeechText = speech;
    el("vbLastText").textContent = speech;
    if (enabled && Boolean(data.monitoring)) say(speech);
  }

  // Mantém todos os controles, WebSocket e Presenter originais do programa.
  const originalRender = window.render;
  if (typeof originalRender === "function") {
    window.render = function (data) {
      originalRender(data);
      onSnapshot(data);
    };
  }

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    if (response.status === 401 && String(args[0] || "").startsWith("/api/")) {
      enabled = false;
      stopSpeech();
      status("Sessão encerrada. Faça login novamente.");
    }
    return response;
  };
  setLed();
})();