-- Demo state: one campaign running, three creators already contracted.
-- Everything else (discovery, tracking, metrics, reports) is produced by the workflows.
-- All handles, channel ids and emails here are fictional.

INSERT INTO campaigns (name, tracking_code, brief_url, budget_eur, starts_on, ends_on) VALUES
  ('Voice Agents Launch', 'VA-LAUNCH', 'https://example.com/briefs/voice-agents', 6000,
   CURRENT_DATE - 10, CURRENT_DATE + 20);

INSERT INTO creators (channel_id, handle, followers, niche, email, status, rate_eur) VALUES
  ('UC_demo_aria',  '@aria.builds',   84000, 'ai tools',       'aria@example.com',  'contracted', 1200),
  ('UC_demo_jonas', '@jonas.voices',  41000, 'text to speech', 'jonas@example.com', 'contracted',  800),
  ('UC_demo_mei',   '@mei.makes.ai', 152000, 'ai voice',       'mei@example.com',   'contracted', 2000);

-- Due dates: one already past, one in ~1 day (reminder fires), one later.
INSERT INTO deliverables (creator_id, campaign_id, due_on)
SELECT cr.id, c.id, CURRENT_DATE + x.offset_days
FROM (VALUES ('UC_demo_aria', -2), ('UC_demo_jonas', 1), ('UC_demo_mei', 9)) AS x(channel_id, offset_days)
JOIN creators cr ON cr.channel_id = x.channel_id
CROSS JOIN campaigns c;

INSERT INTO events (workflow, entity, action, detail)
VALUES ('seed', 'campaign', 'created', '{"note":"demo seed"}');
