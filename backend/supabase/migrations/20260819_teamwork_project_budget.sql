-- Add budget columns to teamwork_projects.
-- capacity/used are stored in cents (Teamwork's native unit).

ALTER TABLE teamwork_projects
  ADD COLUMN IF NOT EXISTS budget_capacity BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS budget_used     BIGINT NOT NULL DEFAULT 0;
