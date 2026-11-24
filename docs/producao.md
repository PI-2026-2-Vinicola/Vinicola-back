# Produção

Guia para publicar a API da OASIS num servidor próprio, com HTTPS, banco PostgreSQL e backup. O frontend fica na Vercel (repositório `Vinicola-Front`).

```
Navegador ── HTTPS ──▶ Vercel (frontend)
    │
    └──────── HTTPS ──▶ Caddy :443 ──▶ API :8000 ──▶ PostgreSQL :5432
                        (certificado        (rede interna do Docker,
                         automático)          sem portas publicadas)

ESP32-CAM ── HTTP (rede da fazenda) ──▶ gateway de edge ── HTTPS ──▶ Caddy
```

Só o Caddy publica portas (80 e 443). A porta 80 serve apenas para emitir o certificado e redirecionar para HTTPS.

## Requisitos

- Servidor Linux com Docker Engine e Docker Compose v2. Para o piloto bastam 2 vCPU, 2 GB de RAM e 20 GB de disco. A API usa cerca de 120 MB de memória parada, e cada foto analisada leva cerca de 0,2 s.
- Espaço para as fotos: cerca de 0,4 MB por leitura (foto + miniatura). Uma câmera com 9 capturas por dia gera perto de 1,3 GB por ano.
- Um domínio para a API (ex.: `api.oasis.agr.br`) com registro DNS do tipo A (ou AAAA) apontando para o IP do servidor.
- Portas 80 e 443 (TCP) abertas no firewall. A 443/UDP é opcional e habilita HTTP/3.

## 1. Baixar o código

```bash
sudo mkdir -p /opt/oasis && sudo chown "$USER" /opt/oasis && cd /opt/oasis
git clone https://github.com/PI-2026-2-Vinicola/Vinicola-back
git clone https://github.com/PI-2026-2-Vinicola/Vinicola-bd    # esquema do banco, lido pelo compose
cd Vinicola-back
cp .env.example .env
```

## 2. Configurar o `.env`

Gere os segredos:

```bash
python3 -c "import secrets as s; print('JWT_SECRET=' + s.token_urlsafe(48)); print('POSTGRES_PASSWORD=' + s.token_urlsafe(24)); print('OASIS_ADMIN_PASSWORD=' + s.token_urlsafe(18))"
```

Preencha no `.env`:

| Variável | Exemplo | Observação |
| --- | --- | --- |
| `OASIS_DOMAIN` | `api.oasis.agr.br` | Domínio da API, já apontado no DNS |
| `JWT_SECRET` | (gerado) | Pelo menos 32 caracteres |
| `POSTGRES_PASSWORD` | (gerado) | Só letras, números, `-` e `_`, porque a senha entra na URL do banco |
| `OASIS_ADMIN_EMAIL` | `admin@suaempresa.com.br` | Administrador criado na primeira inicialização |
| `OASIS_ADMIN_PASSWORD` | (gerado) | Pelo menos 12 caracteres. Vazio = senha aleatória exibida uma vez no log |
| `CORS_ORIGINS` | `https://oasis.vercel.app` | Endereço HTTPS do frontend, sem barra no final |
| `PUBLIC_READ` | `false` | Obrigatório em produção |

`ENVIRONMENT`, `DATABASE_URL` e `STORAGE_DIR` são definidos pelo `docker-compose.prod.yml` e não precisam ser alterados. As integrações (`THINGSPEAK_*` e `EMBRAPA_*`) são opcionais.

Proteja o arquivo: `chmod 600 .env`.

## 3. Subir

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps                          # db e api "healthy", caddy "Up"
docker compose -f docker-compose.prod.yml exec api python -m app.cli check-config
curl https://api.oasis.agr.br/health                                   # {"status":"ok","database":"ok",...}
```

- A API **não inicia** com configuração insegura (`JWT_SECRET` padrão ou curto, `PUBLIC_READ=true`, CORS `*` ou senha do administrador curta). O motivo aparece em `docker compose -f docker-compose.prod.yml logs api`.
- O `check-config` também avisa sobre sobras de desenvolvimento, como `CORS_ORIGINS` com `localhost` ou sem HTTPS.
- Se o certificado não for emitido, confira o DNS e as portas 80/443: `docker compose -f docker-compose.prod.yml logs caddy`.
- Com `OASIS_ADMIN_PASSWORD` vazio, a senha gerada aparece uma única vez em `docker compose -f docker-compose.prod.yml logs api`.

## 4. Frontend (Vercel)

1. Em *Project Settings → Environment Variables*, defina `VITE_API_URL=https://api.oasis.agr.br`, sem `/api/v1` e sem barra no final.
2. Faça um novo deploy. A variável é lida no build.
3. Na API, coloque o endereço do frontend em `CORS_ORIGINS` e recarregue: `docker compose -f docker-compose.prod.yml up -d api`.

## 5. Sensores

O firmware da ESP32-CAM envia em HTTP, sem TLS. Em produção a câmera envia para o **gateway de edge** na rede da fazenda, e o gateway encaminha para a API por HTTPS:

```bash
# no gateway (Raspberry Pi ou mini PC na fazenda)
pip install -r edge/requirements.txt
OASIS_API_URL=https://api.oasis.agr.br uvicorn edge.gateway:app --host 0.0.0.0 --port 8081
```

Na câmera (`config.h`): `EDGE_HOST` = IP do gateway, `EDGE_PORT 8081`, `EDGE_PATH "/capture"`. Detalhes em [`edge/README.md`](../edge/README.md).

## 6. Backup e restauração

```bash
deploy/backup.sh               # backups/oasis-banco-<data>.dump e backups/oasis-fotos-<data>.tar.gz
deploy/backup.sh /mnt/backup   # outra pasta
```

O script guarda o banco (`pg_dump`) e as fotos e apaga os backups com mais de 14 dias (`OASIS_BACKUP_KEEP_DAYS`). Os arquivos ficam legíveis só pelo dono, porque contêm dados pessoais e hashes de senha.

Agende uma execução diária com `crontab -e`:

```
15 3 * * * /opt/oasis/Vinicola-back/deploy/backup.sh >> /opt/oasis/backup.log 2>&1
```

**Copie a pasta `backups/` para fora do servidor** (outro servidor, armazenamento em nuvem, `rclone` ou `rsync`). Um backup no mesmo disco não protege contra a perda do servidor.

Para restaurar, use o comando abaixo. Ele substitui o banco e as fotos atuais e deixa a API parada durante o processo:

```bash
deploy/restore.sh backups/oasis-banco-AAAAMMDD-HHMMSS.dump backups/oasis-fotos-AAAAMMDD-HHMMSS.tar.gz
```

Teste a restauração de vez em quando. Um backup que nunca foi restaurado não está comprovado.

## 7. Atualizar

```bash
deploy/backup.sh
git pull && (cd ../Vinicola-bd && git pull)
docker compose -f docker-compose.prod.yml up -d --build
```

Colunas novas do banco são criadas automaticamente na inicialização da API.

## 8. Monitorar

- `GET https://api.oasis.agr.br/health` responde 200 quando a API e o banco estão funcionando e **503** quando o banco não responde. Aponte um monitor externo (UptimeRobot, Better Stack ou similar) para esse endereço.
- Os containers usam `restart: unless-stopped`: voltam sozinhos se o processo cair ou se o servidor reiniciar. O Docker marca a API como `unhealthy` quando o `/health` falha, mas **não** a reinicia por isso. Quem avisa é o monitor externo.
- Logs: `docker compose -f docker-compose.prod.yml logs -f api caddy`. Cada container guarda até 5 arquivos de 10 MB.

## O que esta configuração garante

| Item | Como |
| --- | --- |
| HTTPS com renovação automática | Caddy + Let's Encrypt; HTTP redireciona para HTTPS (308) |
| Cabeçalhos de segurança | HSTS (1 ano), CSP `default-src 'none'`, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, sem cabeçalho `Server` |
| Superfície mínima | Só 80/443 publicadas; API e banco na rede interna do Docker |
| IP real do cliente | O Caddy grava o IP em `X-Forwarded-For`; a API ignora esse cabeçalho quando o cliente tenta forjá-lo |
| Limite de envio | Corpo das requisições limitado a 12 MB no proxy, além dos limites da API |
| Configuração segura | A API não sobe em produção com segredos fracos, leitura pública ou CORS aberto; `/docs` desligado |
| Recuperação | Backup diário do banco e das fotos, com script de restauração testado |

## Limites conhecidos

- **Um processo da API.** O bloqueio de login e os limites por IP ficam em memória: valem por processo e são zerados quando a API reinicia. Para rodar várias instâncias, seria preciso um armazenamento compartilhado (ex.: Redis).
- **Licença dos modelos.** Os pesos derivam do Embrapa WGISD (CC BY-NC-SA 4.0), então o uso é **somente não comercial**. Uso comercial exige treinar com dados próprios.
- **Validação dos modelos.** Os modelos foram avaliados com as fotos do WGISD, não com fotos do Vale do São Francisco. Valide com fotos locais antes de tomar decisões com base nos resultados.
