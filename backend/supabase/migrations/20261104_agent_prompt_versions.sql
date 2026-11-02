-- Every saved version of an agent prompt, so a change can be reviewed and undone.
create table if not exists public.agent_prompt_versions (
  id bigint generated always as identity primary key,
  key text not null,
  body text not null,
  note text not null default '',
  created_by text,
  created_at timestamptz not null default now()
);

create index if not exists agent_prompt_versions_key_created_idx
  on public.agent_prompt_versions (key, created_at desc);

alter table public.agent_prompt_versions enable row level security;
revoke all on public.agent_prompt_versions from anon, authenticated;
grant all on public.agent_prompt_versions to service_role;
