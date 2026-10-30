# Portal de clientes AGCN Live Voice

O site \`voice.agcndigital.com.br\` é apenas para cadastro, pagamento de licenças e download do programa **Windows x64**. O software/Brain/voz continuam locais no Windows. O portal é separado do \`backend/web_app.py\` (painel de testes legado).

## Hospedagem

Railway (serviço \`voice-web\`) deve iniciar:

\`\`\`
uvicorn backend.portal_app:app --host 0.0.0.0 --port $PORT
\`\`\`

\`AGCN_DATA_DIR=/data\` em volume persistente. Uma réplica; SQLite com WAL.

## Configuração obrigatória para vendas

- \`AGCN_PUBLIC_URL=https://voice.agcndigital.com.br\`
- \`AGCN_LICENSE_PRICE_CENTS\`: preço inteiro em centavos (ex.: 9900 = R$ 99,00); sem preço, **não cobra**
- \`AGCN_LICENSE_DAYS\`: validade em dias, maior que zero; sem prazo, **não cobra**
- \`AGCN_INFINITEPAY_HANDLE\`: InfiniteTag sem $
- \`AGCN_INFINITEPAY_API_KEY\`: chave de Checkout Integrado \`inchk_...\`, só no servidor

A criação de pedido usa \`POST https://api.checkout.infinitepay.io/links\`, com valor do servidor, pagamento único e vencimento de 24h. O cliente volta à área do usuário. O webhook \`/webhook/infinitepay\` nunca libera licença só pelo evento, porque **a InfinitePay não assina os webhooks**: confirma por \`POST /payment_check\`, checa \`success=true\`, \`paid=true\`, valor original, número do pedido, transaction_nsu e slug.

**Atenção:** a InfinitePay Checkout Integrado **não faz renovação automática**. Cada licença é uma cobrança avulsa. Estornos devem ser conciliados manualmente até implementar sincronização confiável de reembolsos.

## Download privado, após publicação do instalador

O instalador completo pode ter \`.exe\` + múltiplos \`.bin\`; disponibilize **um ZIP contendo todos os arquivos da instalação**, e não somente o .exe. Não há release publicado no GitHub no momento da criação do portal.

Use bucket S3 **privado** (Railway Bucket ou outro S3) com:

- \`AGCN_S3_ENDPOINT\` (https)
- \`AGCN_S3_REGION\`
- \`AGCN_S3_BUCKET\`
- \`AGCN_S3_ACCESS_KEY_ID\`
- \`AGCN_S3_SECRET_ACCESS_KEY\`
- \`AGCN_INSTALLER_KEY\` (chave do objeto ZIP)
- \`AGCN_INSTALLER_FILENAME\` opcional

A ação de download verifica sessão, licença válida e produz URL S3 assinada, com validade de 120 segundos. Nunca torne o bucket público; não grave chaves em GitHub.

## Pendências antes da venda real

1. Ajustar registros DNS do subdomínio no provedor do domínio (Railway ainda não controla DNS externo).
2. Definir preço e duração da licença; habilitar InfinitePay na conta e colocar API Key no Railway.
3. Publicar o instalador All-In-One **testado** no bucket S3 privado (sem link público) e preencher \`AGCN_INSTALLER_KEY\`.
4. Implementar no Windows validação de licença/ativação associada ao cliente, se o aplicativo também precisar bloquear uso sem licença. **Atualmente o portal controla apenas o download.**
5. Adicionar verificação de e-mail e recuperação de senha antes da abertura comercial.
6. Publicar política de privacidade, termos de licença, contato/suporte, dados do vendedor e regras de reembolso conformes à legislação.
7. Fazer teste real de ponta a ponta: cadastro, checkout, webhook, instalação e estorno.

## Segurança

Senha com PBKDF2-SHA256 (310 mil iterações) e salt aleatório; sessão HTTP-only com token randômico persistido somente como SHA-256, SameSite=Lax, Secure em HTTPS; verificação de Origin, sem cache, limite simples de tentativas em memória. Use HTTPS no domínio. Para maior escala adotar rate limit compartilhado e gerenciar backups do SQLite.

O painel de teste anterior não é servido no subdomínio comercial.
