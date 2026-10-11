#!/usr/bin/env bash
# Instala ou atualiza a app num host com Docker (ex. LXC "Docker" do community-scripts).
#
#   curl -fsSL https://raw.githubusercontent.com/afonsosantos/hosting-app/main/scripts/install.sh | bash
#
# Na primeira execução cria /opt/hosting-app/.env com segredos aleatórios e acesso pelo IP do host.
# Nas seguintes faz git pull e reconstrói os containers. O .env existente nunca é alterado.
set -euo pipefail

REPO="${REPO:-https://github.com/afonsosantos/hosting-app.git}"
DIR="${DIR:-/opt/hosting-app}"

command -v docker >/dev/null || { echo "Docker não está instalado." >&2; exit 1; }
docker compose version >/dev/null || { echo "Falta o plugin docker compose." >&2; exit 1; }
command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git >/dev/null; }

if [ -d "$DIR/.git" ]; then
    echo "→ A atualizar $DIR"
    git -C "$DIR" pull --ff-only
else
    echo "→ A clonar para $DIR"
    git clone -q "$REPO" "$DIR"
fi
cd "$DIR"

random() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }

if [ ! -f .env ]; then
    ip="$(hostname -I | awk '{print $1}')"
    echo "→ A criar .env (acesso por http://$ip:8000)"
    cp .env.example .env
    sed -i \
        -e "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$(random 60)|" \
        -e "s|^DJANGO_ALLOWED_HOSTS=.*|DJANGO_ALLOWED_HOSTS=$ip|" \
        -e "s|^DJANGO_CSRF_TRUSTED_ORIGINS=.*|DJANGO_CSRF_TRUSTED_ORIGINS=http://$ip:8000|" \
        -e "s|^DJANGO_SECURE_COOKIES=.*|DJANGO_SECURE_COOKIES=false|" \
        -e "s|^DJANGO_SUPERUSER_PASSWORD=.*|DJANGO_SUPERUSER_PASSWORD=$(random 20)|" \
        -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(random 32)|" \
        .env
    chmod 600 .env
    echo "  Password do admin: grep SUPERUSER_PASSWORD $DIR/.env"
fi

echo "→ A construir e arrancar"
docker compose up -d --build --wait
docker compose ps
