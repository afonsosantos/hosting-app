# Gestão de Alojamento

App interna (Django Admin) para gerir clientes, produtos, subscrições, cobranças e
VMs/LXCs do Proxmox de um negócio de alojamento web.

## Funcionalidades

- **Clientes** — dados de faturação, email alternativo para cobranças, opção de não receber notificações.
- **Catálogo** — produtos por tipo (alojamento, VPS, domínio, email, SSL, addon, serviço…), cada um
  com planos de pagamento (mensal, trimestral, semestral, anual, bienal, trienal), preço e taxa de ativação.
- **Subscrições** — conjunto de produtos de um cliente. Cada produto tem a sua periodicidade e o seu
  próximo vencimento (ex. alojamento mensal + domínio anual) e usa o preço do plano correspondente ou um
  preço especial. A taxa de ativação é cobrada só na primeira vez. Os produtos que vencem dentro da mesma
  janela de antecedência saem no mesmo documento.
- **Cobranças** — geradas automaticamente _N_ dias antes do vencimento (ou manualmente com a ação
  “Gerar próxima cobrança agora”), com numeração sequencial por ano (`NC 2026/0001`). Documento em
  HTML/PDF com os dados para transferência bancária. Pagamentos marcados manualmente (botão ou ação em massa).
- **Plano de pagamentos** — cada subscrição mostra as cobranças previstas para 12 meses; ao criar a
  subscrição o plano é enviado ao cliente (configurável), e pode ser reenviado a qualquer momento.
- **Notificações por email** (configuráveis): plano de pagamentos, envio do documento na emissão,
  lembretes X dias antes, avisos de atraso e confirmação de pagamento. Emails em HTML (com versão em
  texto) com resumo, produtos, dados de transferência e assinatura; os textos editáveis são só a
  mensagem. Cor de destaque configurável. Histórico de envios.
- **Proxmox** — ligação por API token, sincronização das VMs/LXCs do cluster e associação de cada uma
  a um produto de uma subscrição.
- **Painel** — em atraso, a vencer em 30 dias, por receber, recebido no mês e receita mensal recorrente.

> O documento emitido é uma **nota de cobrança**, não uma fatura: em Portugal as faturas têm de ser
> emitidas em software certificado pela AT (ou no Portal das Finanças). O título e o rodapé são configuráveis.

## Configuração (na app)

Tudo em **Configurações** no admin: empresa e logótipo, numeração, antecedência da emissão, dados
bancários, SMTP (com botão “Enviar email de teste”) e notificações. As notificações vêm **desligadas**
por omissão — ligue-as depois de testar o SMTP.

Proxmox: **Proxmox → Clusters → Adicionar**. Crie um API token em _Datacenter → Permissions → API Tokens_
(chega o papel `PVEAuditor` em `/`) e use as ações “Testar ligação” e “Sincronizar VMs/LXCs”. Depois,
em **VMs / LXCs**, associe cada uma a um produto de subscrição.

## Tarefas automáticas

O serviço `scheduler` corre a cada `SCHEDULER_INTERVAL` segundos (1h por omissão):

- `sync_proxmox` — sincroniza os clusters com sincronização automática ativa;
- `run_billing` — gera cobranças a vencer e envia emissões/lembretes/avisos pendentes.

Ambos são idempotentes e podem ser corridos à mão: `docker compose exec web python manage.py run_billing`.

## Instalação rápida (LXC Docker do Proxmox)

1. Na shell do nó Proxmox, criar o LXC com o script
   [Docker do community-scripts](https://community-scripts.org/scripts/docker) (as opções por omissão servem).
2. Dentro do LXC (`pct enter <CTID>`):
   ```sh
   curl -fsSL https://raw.githubusercontent.com/afonsosantos/hosting-app/main/scripts/install.sh | bash
   ```
   Instala em `/opt/hosting-app`, cria o `.env` com segredos aleatórios e acesso por
   `http://<ip-do-lxc>:8000`. A password do admin fica no `.env` (`DJANGO_SUPERUSER_PASSWORD`).
3. Para atualizar, correr o mesmo comando (faz `git pull` e reconstrói; o `.env` não é alterado).

## Produção (Docker num LXC do Proxmox)

1. Criar o LXC (Debian 13) com **nesting** e **keyctl** ativos
   (_Options → Features_, ou `pct set <id> --features nesting=1,keyctl=1`) e instalar o Docker.
2. Copiar o projeto, criar o `.env` e preencher:
   ```sh
   cp .env.example .env
   python3 -c "import secrets; print(secrets.token_urlsafe(50))"   # DJANGO_SECRET_KEY
   ```
3. Arrancar:
   ```sh
   docker compose up -d --build
   ```
   As migrações correm no arranque e é criado o superutilizador definido no `.env`.
4. No reverse proxy, apontar o domínio para `http://<ip-do-lxc>:8000` e enviar `X-Forwarded-Proto`.
   `DJANGO_ALLOWED_HOSTS` e `DJANGO_CSRF_TRUSTED_ORIGINS` têm de corresponder ao domínio.

Atualizar: `git pull && docker compose up -d --build`.

Backups (base de dados + ficheiros carregados, ex. logótipo):

```sh
docker compose exec -T db pg_dump -U hosting hosting | gzip > backup-$(date +%F).sql.gz
docker compose run --rm -T --no-deps -e RUN_MIGRATIONS=0 web tar czf - -C /app media > media-$(date +%F).tgz
```

## Desenvolvimento

```sh
uv sync
export DJANGO_DEBUG=1
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
uv run python manage.py test
uv run ruff check . && uv run ruff format .
```

Sem `DATABASE_URL` usa SQLite. O PDF usa WeasyPrint, que precisa do Pango
(`brew install pango`; no macOS pode ser preciso `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`).

## Estrutura

| App         | Conteúdo                                                        |
|-------------|-----------------------------------------------------------------|
| `core`      | Configurações (singleton), envio SMTP, agendador                |
| `customers` | Clientes                                                        |
| `catalog`   | Produtos e planos de pagamento                                  |
| `billing`   | Subscrições, cobranças, PDF, notificações                       |
| `proxmox`   | Clusters, VMs/LXCs e sincronização                              |
