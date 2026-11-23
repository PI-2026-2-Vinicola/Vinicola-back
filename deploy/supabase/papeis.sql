-- Papéis do banco da OASIS no Supabase, com o mínimo de permissões (princípio do menor privilégio).
-- Rode no Supabase → SQL Editor. Troque as duas senhas ANTES de executar e guarde-as só no Render
-- (DATABASE_URL) e nos secrets do GitHub (OASIS_BACKUP_DATABASE_URL). Nunca faça commit das senhas.
--
--   oasis_api     usado pela API: cria e altera só as próprias tabelas no schema public
--   oasis_backup  usado pelo backup do GitHub Actions: só leitura (SELECT)
--
-- Nenhum dos dois acessa os schemas internos do Supabase (auth, storage etc.) nem é superusuário.
-- Pelo pooler, o usuário vira <papel>.<ref-do-projeto>, ex.: oasis_api.abcdefghijklmnop

create role oasis_api login password 'TROQUE-PELA-SENHA-DA-API' connection limit 10;
grant usage, create on schema public to oasis_api;

create role oasis_backup login password 'TROQUE-PELA-SENHA-DO-BACKUP' connection limit 2;
grant usage on schema public to oasis_backup;
-- Tabelas e sequências criadas pela API ficam legíveis pelo backup automaticamente:
alter default privileges for role oasis_api in schema public grant select on tables to oasis_backup;
alter default privileges for role oasis_api in schema public grant select on sequences to oasis_backup;

-- A Data API do Supabase (PostgREST) expõe o schema public aos papéis anon e authenticated.
-- A OASIS não usa a Data API: as tabelas da API nunca devem ficar acessíveis por ela.
alter default privileges for role oasis_api in schema public revoke all on tables from anon, authenticated;
alter default privileges for role oasis_api in schema public revoke all on sequences from anon, authenticated;
alter default privileges for role oasis_api in schema public revoke all on functions from anon, authenticated;

-- Depois da primeira inicialização da API (tabelas já criadas), rode também o bloco abaixo.
-- Ele é idempotente e pode ser repetido a qualquer momento:
--
--   grant select on all tables in schema public to oasis_backup;
--   grant select on all sequences in schema public to oasis_backup;
--   revoke all on all tables in schema public from anon, authenticated;
--   revoke all on all sequences in schema public from anon, authenticated;
