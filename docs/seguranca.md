# Segurança na OASIS

Este documento descreve os controles de segurança da API: autenticação, autorização por perfil (RBAC), isolamento entre exportadoras, proteção das senhas, gestão de segredos e auditoria. Cada controle aponta para o código que o implementa e para o teste que o comprova.

## 1. Perfis e matriz de permissões

| Perfil (`role`) | Quem é |
| --- | --- |
| Administrador (`admin`) | Equipe que mantém a plataforma. Não pertence a uma exportadora e vê todas. |
| Exportadora, gestor (`gestor`) | Responsável pela exportadora: sensores, análises, clima e importações da própria empresa. |
| Operador de campo e logística (`operador`) | Envia fotos e medições e acompanha o monitoramento da própria exportadora. |

A matriz abaixo é gerada pelo próprio código (`python -m app.cli matriz-rbac`). É a mesma tabela que as rotas consultam (`app/rbac.py`), que a API publica em `/auth/matrix` e que o painel mostra na aba **Administração → Perfis e permissões**.

| Recurso | Administrador | Exportadora (gestor) | Operador de campo e logística |
| --- | --- | --- | --- |
| Usuários | CRUD | – | – |
| Organizações (exportadoras) | CRUD | R (própria) | R (própria) |
| Sensores IoT | CRUD | CRUD (própria organização) | R (própria organização) |
| Análises de qualidade (fotos, histórico e indicadores) | CRD | CRD (própria organização) | CR (própria organização) |
| Dados climáticos (temperatura, umidade, luz, solo) | CR | CR (própria organização) | CR (própria organização) |
| Importação de dados | CR | CR (própria organização) | – |
| Auditoria | R | – | – |
| Configurações e estado do sistema | R | – | – |

C = criar · R = consultar · U = atualizar · D = excluir · – = sem acesso

## 2. Como o RBAC é aplicado

A autorização acontece **no backend**, em toda requisição. O painel só esconde menus por conveniência (`canAccess` no frontend lê as permissões que a API devolve em `/auth/me`); uma chamada direta à API recebe a mesma resposta.

1. **Perfil × recurso × ação.** Cada rota declara o que exige, por exemplo `Depends(require("sensores", "C"))`. Se o perfil não tem a operação na matriz, a API responde **403** (`Acesso negado: o perfil … não pode …`) e grava `acesso_negado` na auditoria. Sem sessão, responde **401**.
2. **Organização.** Usuários (exceto o administrador), sensores, leituras e importações pertencem a uma organização. As listagens são filtradas pela organização do usuário, e um registro de outra responde **403** (`Acesso negado: este registro pertence a outra organização.`), também auditado.

O perfil e a organização são lidos do banco a cada requisição, não do token: um usuário rebaixado ou desativado perde o acesso na hora.

## 3. Autenticação

- **Login** em `POST /api/v1/auth/login` com e-mail e senha. A resposta traz um **JWT** (HS256, assinado com `JWT_SECRET`, validade de 8 horas) e o perfil com as permissões.
- **Login inválido** responde 401 `E-mail ou senha inválidos.`, sem revelar se o e-mail existe. Após **5 tentativas erradas** do mesmo e-mail e IP, o login fica bloqueado por 15 minutos (429). Falhas e bloqueios vão para a auditoria.
- **Sessão.** O painel envia o token em `Authorization: Bearer` e volta para o login quando a API responde 401 (token expirado, adulterado ou usuário desativado). Sair apaga o token do navegador.
- **Rotas protegidas.** Tudo exige sessão, exceto `/health`, `/public/*` (resumo sem localização) e o envio das câmeras, que usa o token próprio de cada sensor (aleatório, 192 bits, guardado só como hash).
- Política de senha: mínimo de 8 caracteres, com letras e números. Em produção, a senha do administrador inicial precisa de 12 ou mais caracteres.

## 4. Proteção das senhas

As senhas são guardadas com **PBKDF2-HMAC-SHA256, 240 mil iterações e sal aleatório de 16 bytes** por usuário (`app/security.py`), no formato `pbkdf2_sha256$<iterações>$<sal>$<hash>`. Usuários com a mesma senha têm hashes diferentes, por causa do sal, e a comparação é feita em tempo constante (`hmac.compare_digest`).

## 5. Dados em trânsito

Todo tráfego fora da rede da fazenda usa HTTPS/TLS: navegador e Vercel, painel e API no Render, gateway e API, API e PostgreSQL (TLS do pooler do Supabase) e API e armazenamento de fotos (HTTPS com assinatura SigV4). Sob HTTPS, a API envia `Strict-Transport-Security`. O site tem Content-Security-Policy (`vercel.json` do frontend).

## 6. Gestão de segredos

| Segredo | Desenvolvimento | Produção |
| --- | --- | --- |
| `JWT_SECRET` | `.env` local | Gerado pelo Render (`generateValue: true`) |
| `DATABASE_URL` | `.env` local | Painel do Render (`sync: false`) |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | `.env` local | Painel do Render (`sync: false`) |
| `OASIS_ADMIN_PASSWORD` | `.env` local | Painel do Render (`sync: false`) |
| `RENDER_DEPLOY_HOOK_URL`, `VERCEL_TOKEN`, `OASIS_BACKUP_DATABASE_URL`, `OASIS_BACKUP_PASSPHRASE` e `OASIS_BACKUP_S3_*` | — | Secrets dos ambientes `producao` e `backup` do GitHub |

- `.env` está no `.gitignore`; só o modelo `.env.example`, sem valores, é versionado.
- O pipeline roda o **gitleaks** em todo o histórico a cada push; um segredo commitado reprova o build.
- `python -m app.cli check-config` recusa configuração de produção insegura: `JWT_SECRET` padrão ou curto, senha de administrador fraca, CORS aberto ou S3 incompleto.

## 7. Auditoria

A tabela `audit_log` registra logins (inclusive falhos e bloqueados), acessos negados, criação e alteração de usuários, organizações e sensores, geração de tokens, importações e exclusões, com o IP de origem. O administrador consulta em **Administração → Auditoria**.

## 8. Testes

`tests/test_seguranca.py` monta duas exportadoras (A e B) com usuários de cada perfil e verifica: login válido e inválido, rotas sem sessão, cada perfil em cada recurso conforme a matriz, acesso negado com auditoria, Exportadora A barrada em 7 rotas da Exportadora B, hash PBKDF2 com sal, segredos fora do repositório e migração de bancos antigos.

```bash
python -m pytest tests/test_seguranca.py -v
```

## 9. Demonstração local

```bash
python -m app.cli demo-seguranca --senha Demo2026seg
```

Cria as exportadoras **Vale do Sol** (Petrolina) e **Rio Verde** (Juazeiro), cada uma com um sensor, um gestor e um operador (`gestor@valedosol.example`, `operador@valedosol.example` e os equivalentes em `rioverde.example`). Empresas e pessoas são fictícias.

## Limitações conhecidas

- O JWT não é revogado antes de expirar. Desativar o usuário ou trocar o perfil vale na hora, mas um token roubado de um usuário ativo continua válido por até 8 horas.
- O token fica no `localStorage` do navegador. A Content-Security-Policy reduz o risco de XSS, mas um cookie `HttpOnly` seria mais forte.
- O OWASP sugere hoje 600 mil iterações para PBKDF2-SHA256. Mantivemos 240 mil pela CPU reduzida do plano gratuito; o número fica gravado em cada hash e pode subir sem invalidar as senhas.
- O bloqueio de login fica na memória do processo. Com várias instâncias, precisaria de um Redis.
