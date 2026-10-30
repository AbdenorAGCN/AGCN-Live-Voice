# AGCN Live Voice — Portal web

A entrada web do repositório é `backend.web_app:app`. Ela monta as rotas
do backend existente (`backend.app_v054`) e reaproveita o mesmo
`AGCNVoiceRuntime` / `ProductStore` / `TikTokMonitor` / Presenter.

## Railway

- Projeto: `AGCN Live Voice Web`
- Serviço: `voice-web`
- Branch: `AbdenorAGCN/AGCN-Live-Voice:main`
- Comando: `uvicorn backend.web_app:app --host 0.0.0.0 --port $PORT`
- Healthcheck: `/health`
- Volume de produtos: `/data`, com `AGCN_DATA_DIR=/data`
- Variáveis obrigatórias de segurança: `AGCN_WEB_PASSWORD` e
  `AGCN_WEB_SESSION_SECRET` (definidas somente no Railway).
- Manter uma réplica: o monitor e as filas do Presenter são processos em
  memória, e múltiplas réplicas independentes não compartilham a LIVE.

## Domínio

Adicionar `voice.agcndigital.com.br` como domínio customizado no Railway.
No provedor DNS de `agcndigital.com.br`, criar as entradas CNAME/TXT
**exatamente como mostradas pelo Railway > Networking > Custom Domain**.
Os valores podem mudar se o domínio for recriado; consultar o Railway como
fonte de verdade. Aguardar verificação DNS e certificado TLS.

## O que funciona

- Login com senha de sessão HTTP-only, SameSite=Strict e validade 12 h.
- Rotas API de produto/ativação/pesquisa e testes do Presenter.
- Monitoramento TikTok LIVE, comentários, métricas e eventos que o backend
  atual disponibiliza.
- WebSocket `/ws` protegido pela mesma autenticação.
- Interface web responsiva com identidade AGCN Digital.
- Botão opt-in `Ativar voz`: SpeechSynthesis local no navegador lê falas
  entregues pelo Presenter; é necessário gesto do usuário.

## Limites atuais

**Não é a instalação integral do programa Windows 1.1.0 no navegador.**
O backend web reutiliza Presenter e monitoramento, mas não empacota os
modelos locais Qwen3-4B e Qwen3-TTS HQ nem um driver de áudio Windows.
A voz do navegador depende das vozes disponíveis em cada dispositivo e
não é enviada diretamente ao TikTok LIVE Studio remoto. Para o microfone
virtual/VB-CABLE no Windows, usar o programa desktop ou uma ponte local
futura e autenticada. Não prometer injeção automática no TikTok.

O servidor é single-tenant por enquanto: uma sessão LIVE por serviço.
Para vários clientes independentes seriam necessários autenticação por
conta, workers isolados, filas persistentes e separação de dados.

Ao editar a senha no Railway, sessões assinadas existentes não são
necessariamente revogadas; para invalidar todas, rotacionar também
`AGCN_WEB_SESSION_SECRET`.
