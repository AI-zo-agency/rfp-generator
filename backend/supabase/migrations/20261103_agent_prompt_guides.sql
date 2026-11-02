-- Plain-English "what it does / when to change it" for each agent prompt, written by the AI once per
-- version of the prompt text. body_hash is the sha256 of the prompt text the note was written for, so the
-- note is regenerated only after the prompt changes, never on page load.
create table if not exists public.agent_prompt_guides (
  key text primary key,
  body_hash text not null,
  what text not null default '',
  when_to_change text not null default '',
  updated_at timestamptz not null default now()
);

alter table public.agent_prompt_guides enable row level security;
revoke all on public.agent_prompt_guides from anon, authenticated;
grant all on public.agent_prompt_guides to service_role;
