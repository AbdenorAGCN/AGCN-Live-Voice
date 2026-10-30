# AGCN Live Voice · Portal comercial

Branch exclusiva do portal de clientes do domínio **voice.agcndigital.com.br**.

**Contém somente:** páginas de apresentação, cadastro/login, área do cliente, pedidos/checkout InfinitePay e controle de download do instalador Windows por licença ativa.

**Não contém nem executa:** código do Brain Qwen3, treinamento de IA, llama.cpp, processamento de comentários TikTok, voz neural, motores TTS ou programa Windows.

O aplicativo Windows completo e a inteligência local permanecem na branch `main` deste repositório.

## Rodar localmente

```bash
pip install -r requirements-portal.txt
uvicorn backend.portal_app:app --host 127.0.0.1 --port 8080
```

No Railway, o serviço `voice-web` usa esta branch (`site`) e `Dockerfile.portal`, com volume persistente `/data` via `AGCN_DATA_DIR`. A publicação no domínio exige DNS válido.

Checkout e download permanecem indisponíveis enquanto preço, validade, credenciais InfinitePay e instalador privado não forem configurados. Consulte `web/PORTAL_CLIENTES.md` antes de abrir vendas.
