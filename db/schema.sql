-- Creator Ops Mini — schema
-- Every write in the workflows is an upsert against one of the UNIQUE keys below,
-- so any workflow can be re-run without creating duplicates.

DROP VIEW  IF EXISTS campaign_roi;
DROP TABLE IF EXISTS reports, notifications, events, payments, metrics_daily,
                     deliverables, campaigns, creators CASCADE;

CREATE TABLE creators (
  id          bigserial PRIMARY KEY,
  platform    text        NOT NULL DEFAULT 'youtube',
  channel_id  text        NOT NULL UNIQUE,
  handle      text        NOT NULL,
  followers   integer     NOT NULL,
  niche       text,                     -- the discovery keyword that found them
  email       text,                     -- filled at onboarding, never scraped
  status      text        NOT NULL DEFAULT 'discovered'
              CHECK (status IN ('discovered','contacted','contracted','live','paid','declined')),
  rate_eur    numeric(10,2),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE campaigns (
  id            bigserial PRIMARY KEY,
  name          text NOT NULL UNIQUE,
  tracking_code text NOT NULL UNIQUE,   -- creators put this in the video description
  brief_url     text,
  budget_eur    numeric(10,2),
  starts_on     date NOT NULL,
  ends_on       date NOT NULL
);

CREATE TABLE deliverables (
  id               bigserial PRIMARY KEY,
  creator_id       bigint NOT NULL REFERENCES creators(id),
  campaign_id      bigint NOT NULL REFERENCES campaigns(id),
  due_on           date   NOT NULL,
  post_url         text,
  platform_post_id text UNIQUE,
  posted_at        timestamptz,
  verified_at      timestamptz,
  UNIQUE (creator_id, campaign_id)
);

CREATE TABLE metrics_daily (
  deliverable_id bigint  NOT NULL REFERENCES deliverables(id),
  day            date    NOT NULL,
  views          integer NOT NULL DEFAULT 0,
  likes          integer NOT NULL DEFAULT 0,
  comments       integer NOT NULL DEFAULT 0,
  clicks         integer NOT NULL DEFAULT 0,
  PRIMARY KEY (deliverable_id, day)
);

CREATE TABLE payments (
  id                bigserial PRIMARY KEY,
  deliverable_id    bigint NOT NULL UNIQUE REFERENCES deliverables(id),
  amount_eur        numeric(10,2) NOT NULL,
  stripe_invoice_id text,
  status            text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','invoiced','paid','failed')),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Append-only audit trail: every state change any workflow makes lands here.
CREATE TABLE events (
  id        bigserial PRIMARY KEY,
  at        timestamptz NOT NULL DEFAULT now(),
  workflow  text NOT NULL,
  entity    text NOT NULL,
  entity_id text,
  action    text NOT NULL,
  detail    jsonb NOT NULL DEFAULT '{}'
);
CREATE RULE events_no_update AS ON UPDATE TO events DO INSTEAD NOTHING;
CREATE RULE events_no_delete AS ON DELETE TO events DO INSTEAD NOTHING;

-- Outbox: workflows never call Slack directly. They queue a message here and
-- the dispatcher delivers it, so a missing or broken Slack hook loses nothing
-- and the demo runs safely with no Slack at all (dry run).
CREATE TABLE notifications (
  id         bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  channel    text NOT NULL,              -- e.g. #creator-ops, #automation-alerts, dm:<handle>
  text       text NOT NULL,
  dedupe_key text UNIQUE,                -- the same reminder is never queued twice
  status     text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','dry_run','failed')),
  sent_at    timestamptz
);

CREATE TABLE reports (
  id          bigserial PRIMARY KEY,
  created_at  timestamptz NOT NULL DEFAULT now(),
  period_from date NOT NULL,
  period_to   date NOT NULL,
  markdown    text NOT NULL,
  UNIQUE (period_from, period_to)
);

-- ROI per creator per campaign, from each post's latest snapshot.
-- metrics_daily holds lifetime totals per day, so we never SUM across days.
-- Engagement = likes + comments.
CREATE VIEW campaign_roi AS
SELECT c.name                                    AS campaign,
       cr.handle,
       cr.rate_eur                               AS cost_eur,
       COALESCE(m.views, 0)                      AS views,
       COALESCE(m.likes + m.comments, 0)         AS engagements,
       COALESCE(m.clicks, 0)                     AS clicks,
       ROUND(cr.rate_eur / NULLIF(m.likes + m.comments, 0), 3) AS cost_per_engagement,
       ROUND(100.0 * m.clicks / NULLIF(m.views, 0), 2)         AS ctr_pct,
       m.day                                     AS as_of
FROM deliverables d
JOIN campaigns c  ON c.id  = d.campaign_id
JOIN creators  cr ON cr.id = d.creator_id
LEFT JOIN LATERAL (
  SELECT * FROM metrics_daily md
  WHERE md.deliverable_id = d.id
  ORDER BY md.day DESC
  LIMIT 1
) m ON true;
