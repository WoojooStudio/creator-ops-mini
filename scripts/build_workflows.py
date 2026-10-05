"""Generate the n8n workflow JSON files in workflows/.

The JSON is what n8n imports and what reviewers read; this script is the
source of truth so the seven workflows stay consistent (same error handler,
same retry policy, same credential names). Run: python3 scripts/build_workflows.py
"""
import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "workflows"

PG = {"postgres": {"id": "creatorOpsPg01", "name": "Creator Ops DB"}}
YOUTUBE = {"httpQueryAuth": {"id": "youtubeApiKey001", "name": "YouTube Data API key"}}
SLACK = {"slackApi": {"id": "slackBotToken001", "name": "Slack bot (creator-ops)"}}
ERROR_WORKFLOW_ID = "creatorOps000000"
RETRY = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 2000}


# ── node helpers ────────────────────────────────────────────────────────────

def manual():
    return {"name": "Run now", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "parameters": {}}


def schedule(name, interval):
    return {"name": name, "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
            "parameters": {"rule": {"interval": [interval]}}}


def code(name, js, notes=None):
    n = {"name": name, "type": "n8n-nodes-base.code", "typeVersion": 2, "parameters": {"jsCode": js.strip()}}
    if notes:
        n["notes"], n["notesInFlow"] = notes, True
    return n


def sql(name, query, params=None, notes=None):
    """Postgres node. Params are passed as an array so commas inside JSON stay intact."""
    p = {"operation": "executeQuery", "query": query.strip(), "options": {}}
    if params:
        p["options"]["queryReplacement"] = "={{ [ " + ", ".join(params) + " ] }}"
    n = {"name": name, "type": "n8n-nodes-base.postgres", "typeVersion": 2.6, "parameters": p, "credentials": PG}
    if notes:
        n["notes"], n["notesInFlow"] = notes, True
    return n


def flag_is_true(name, env_var):
    return {"name": name, "type": "n8n-nodes-base.if", "typeVersion": 2.2, "parameters": {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
            "conditions": [{"id": "flag", "leftValue": "={{ $env." + env_var + " }}", "rightValue": "true",
                            "operator": {"type": "string", "operation": "equals"}}],
            "combinator": "and"},
        "options": {}}}


def youtube(name, endpoint, query):
    return {"name": name, "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, **RETRY,
            "credentials": YOUTUBE, "parameters": {
                "url": f"https://www.googleapis.com/youtube/v3/{endpoint}",
                "authentication": "genericCredentialType", "genericAuthType": "httpQueryAuth",
                "sendQuery": True,
                "queryParameters": {"parameters": [{"name": k, "value": v} for k, v in query]},
                "options": {}}}


def workflow(wid, name, nodes, edges, with_error_handler=True):
    """edges: list of (from, to) or (from, to, output_index) for IF branches."""
    conns = {}
    for e in edges:
        src, dst, out = (e + (0,))[:3]
        outs = conns.setdefault(src, {"main": []})["main"]
        while len(outs) <= out:
            outs.append([])
        outs[out].append({"node": dst, "type": "main", "index": 0})
    # simple layout: column = longest path from a trigger
    depth = {n["name"]: 0 for n in nodes}
    for _ in nodes:
        for e in edges:
            depth[e[1]] = max(depth[e[1]], depth[e[0]] + 1)
    rows = {}
    for i, n in enumerate(nodes):
        d = depth[n["name"]]
        r = rows.get(d, 0)
        rows[d] = r + 1
        n["id"] = f"{wid}-{i:02d}"
        n["position"] = [d * 260, r * 200]
    settings = {"executionOrder": "v1", "saveManualExecutions": True, "callerPolicy": "workflowsFromSameOwner"}
    if with_error_handler:
        settings["errorWorkflow"] = ERROR_WORKFLOW_ID
    return {"id": wid, "name": name, "active": False, "nodes": nodes, "connections": conns,
            "settings": settings, "pinData": {}, "tags": []}


# ── 00 Error handler ────────────────────────────────────────────────────────

w00 = workflow(ERROR_WORKFLOW_ID, "00 · Error handler", [
    {"name": "On any workflow error", "type": "n8n-nodes-base.errorTrigger", "typeVersion": 1, "parameters": {}},
    code("Format alert", r"""
const { execution = {}, workflow = {} } = $input.first().json;
const err = execution.error || {};
const text = [
  `:rotating_light: *${workflow.name || 'unknown workflow'}* failed`,
  `Node: ${execution.lastNodeExecuted || '?'}`,
  `Error: ${(err.message || 'no message').slice(0, 300)}`,
  execution.url ? `Execution: ${execution.url}` : null,
].filter(Boolean).join('\n');
return [{ json: {
  workflow: workflow.name || 'unknown',
  execution_id: String(execution.id ?? ''),
  detail: { node: execution.lastNodeExecuted, message: err.message, mode: execution.mode },
  text,
} }];
"""),
    sql("Log + queue alert", """
WITH e AS (
  INSERT INTO events (workflow, entity, entity_id, action, detail)
  VALUES ($1, 'execution', $2, 'failed', $3::jsonb)
  RETURNING id
)
-- one alert per workflow per hour: a broken API can't flood the channel
INSERT INTO notifications (channel, text, dedupe_key)
SELECT '#automation-alerts', $4, 'alert:' || $1 || ':' || to_char(now(), 'YYYY-MM-DD"T"HH24')
FROM e
ON CONFLICT (dedupe_key) DO NOTHING
RETURNING id AS notification_id
""", ["$json.workflow", "$json.execution_id", "JSON.stringify($json.detail)", "$json.text"]),
], [("On any workflow error", "Format alert"), ("Format alert", "Log + queue alert")],
    with_error_handler=False)


# ── 01 Creator discovery → CRM sync ────────────────────────────────────────

w01 = workflow("creatorOps000001", "01 · Creator discovery → CRM sync", [
    manual(),
    schedule("Every day 07:00", {"field": "days", "daysInterval": 1, "triggerAtHour": 7}),
    code("Discovery settings", r"""
// Brand keywords and follower band for this program.
const settings = {
  keywords: ['ai voice', 'text to speech', 'ai tools'],
  minFollowers: 10000,
  maxFollowers: 500000,
};
return settings.keywords.map(keyword => ({ json: { keyword, ...settings } }));
"""),
    flag_is_true("Live YouTube API?", "LIVE_YOUTUBE"),
    youtube("YouTube · search channels", "search", [
        ("part", "snippet"), ("type", "channel"), ("maxResults", "25"), ("q", "={{ $json.keyword }}")]),
    code("Collect channel ids", r"""
const settings = $('Discovery settings').all();
const nicheById = {};
$input.all().forEach((item, i) => {
  for (const r of item.json.items || []) nicheById[r.id.channelId] ??= settings[i]?.json.keyword;
});
const ids = Object.keys(nicheById).slice(0, 50);
if (!ids.length) return [];
return [{ json: { ids: ids.join(','), nicheById } }];
"""),
    youtube("YouTube · channel stats", "channels", [
        ("part", "snippet,statistics"), ("maxResults", "50"), ("id", "={{ $json.ids }}")]),
    code("Normalize (live)", r"""
const { nicheById } = $('Collect channel ids').first().json;
return ($input.first().json.items || []).map(ch => ({ json: {
  channel_id: ch.id,
  handle: ch.snippet.customUrl || ch.snippet.title,
  followers: ch.statistics.hiddenSubscriberCount ? 0 : Number(ch.statistics.subscriberCount || 0),
  niche: nicheById[ch.id] || null,
} }));
"""),
    code("Fixture channels", r"""
// Offline fixture shaped like the normalized YouTube result. Fictional channels.
// A small daily drift in follower counts shows re-runs refresh rows instead of duplicating them.
const drift = Number(new Date().toISOString().slice(0, 10).replaceAll('-', '')) % 7;
const pool = [
  ['UC_demo_aria',  '@aria.builds',      84000, 'ai tools'],
  ['UC_demo_jonas', '@jonas.voices',     41000, 'text to speech'],
  ['UC_demo_mei',   '@mei.makes.ai',    152000, 'ai voice'],
  ['UC_demo_noor',  '@noor.narrates',    23500, 'ai voice'],
  ['UC_demo_lukas', '@lukas.dubs',       67800, 'ai voice'],
  ['UC_demo_sofia', '@sofia.podcasts',  310000, 'text to speech'],
  ['UC_demo_kenji', '@kenji.codes',      18900, 'ai tools'],
  ['UC_demo_ola',   '@ola.creates',       4200, 'ai tools'],        // below band
  ['UC_demo_mega',  '@mega.tech.daily', 2400000, 'ai tools'],       // above band
  ['UC_demo_noor',  '@noor.narrates',    23500, 'text to speech'],  // found by two keywords
];
return pool.map(([channel_id, handle, followers, niche]) =>
  ({ json: { channel_id, handle, followers: followers + drift * 37, niche } }));
"""),
    code("Filter band + dedupe", r"""
const { minFollowers, maxFollowers } = $('Discovery settings').first().json;
const keep = new Map();
let outOfBand = 0;
for (const { json: c } of $input.all()) {
  if (c.followers < minFollowers || c.followers > maxFollowers) { outOfBand++; continue; }
  if (!keep.has(c.channel_id)) keep.set(c.channel_id, c);
}
if (!keep.size) return [];
return [{ json: { rows: [...keep.values()], outOfBand } }];
"""),
    sql("Upsert creators + log + notify", """
WITH x AS (
  SELECT * FROM jsonb_to_recordset($1::jsonb)
    AS x(channel_id text, handle text, followers int, niche text)
), up AS (
  INSERT INTO creators (channel_id, handle, followers, niche)
  SELECT channel_id, handle, followers, niche FROM x
  ON CONFLICT (channel_id) DO UPDATE
    SET followers = EXCLUDED.followers, handle = EXCLUDED.handle, updated_at = now()
  RETURNING id, handle, followers, (xmax = 0) AS inserted
), ev AS (
  INSERT INTO events (workflow, entity, entity_id, action, detail)
  SELECT '01-discovery', 'creator', id::text,
         CASE WHEN inserted THEN 'discovered' ELSE 'refreshed' END,
         jsonb_build_object('followers', followers)
  FROM up
  RETURNING 1
), n AS (
  INSERT INTO notifications (channel, text, dedupe_key)
  SELECT '#creator-ops',
         'New creators found: ' || string_agg(handle || ' (' || followers || ')', ', '),
         'discovery:' || current_date
  FROM up WHERE inserted
  HAVING count(*) > 0
  ON CONFLICT (dedupe_key) DO NOTHING
  RETURNING 1
)
SELECT (SELECT count(*) FROM up WHERE inserted)::int     AS new_creators,
       (SELECT count(*) FROM up WHERE NOT inserted)::int AS refreshed,
       $2::int                                       AS skipped_out_of_band
""", ["JSON.stringify($json.rows)", "$json.outOfBand"]),
], [
    ("Run now", "Discovery settings"), ("Every day 07:00", "Discovery settings"),
    ("Discovery settings", "Live YouTube API?"),
    ("Live YouTube API?", "YouTube · search channels", 0),
    ("YouTube · search channels", "Collect channel ids"),
    ("Collect channel ids", "YouTube · channel stats"),
    ("YouTube · channel stats", "Normalize (live)"),
    ("Normalize (live)", "Filter band + dedupe"),
    ("Live YouTube API?", "Fixture channels", 1),
    ("Fixture channels", "Filter band + dedupe"),
    ("Filter band + dedupe", "Upsert creators + log + notify"),
])


# ── 03 Content reminders ────────────────────────────────────────────────────

w03 = workflow("creatorOps000003", "03 · Content reminders", [
    manual(),
    schedule("Every day 09:00", {"field": "days", "daysInterval": 1, "triggerAtHour": 9}),
    sql("Queue reminders + overdue alerts", """
WITH open AS (
  SELECT d.id, cr.handle, c.name AS campaign, c.tracking_code, d.due_on
  FROM deliverables d
  JOIN creators  cr ON cr.id = d.creator_id
  JOIN campaigns c  ON c.id  = d.campaign_id
  WHERE d.post_url IS NULL AND cr.status = 'contracted'
), reminders AS (
  INSERT INTO notifications (channel, text, dedupe_key)
  SELECT 'dm:' || handle,
         format('Hi %s! Friendly reminder: your %s post is due %s. Please include %s in the description so we can track it.',
                handle, campaign, to_char(due_on, 'Dy DD Mon'), tracking_code),
         'reminder:' || id || ':' || current_date
  FROM open
  WHERE due_on BETWEEN current_date AND current_date + 2
  ON CONFLICT (dedupe_key) DO NOTHING
  RETURNING dedupe_key
), overdue AS (
  INSERT INTO notifications (channel, text, dedupe_key)
  SELECT '#creator-ops',
         format('Overdue: %s — %s was due %s (%s days ago)', handle, campaign, due_on, current_date - due_on),
         'overdue:' || id || ':' || current_date
  FROM open
  WHERE due_on < current_date
  ON CONFLICT (dedupe_key) DO NOTHING
  RETURNING dedupe_key
), ev AS (
  INSERT INTO events (workflow, entity, entity_id, action)
  SELECT '03-reminders', 'deliverable', split_part(dedupe_key, ':', 2), split_part(dedupe_key, ':', 1) || '_queued'
  FROM (SELECT dedupe_key FROM reminders UNION ALL SELECT dedupe_key FROM overdue) q
  RETURNING 1
)
SELECT (SELECT count(*) FROM reminders)::int AS reminders_queued,
       (SELECT count(*) FROM overdue)::int   AS overdue_alerts_queued
"""),
], [("Run now", "Queue reminders + overdue alerts"), ("Every day 09:00", "Queue reminders + overdue alerts")])


# ── 04 Content tracking ─────────────────────────────────────────────────────

w04 = workflow("creatorOps000004", "04 · Content tracking", [
    manual(),
    schedule("Every 6 hours", {"field": "hours", "hoursInterval": 6}),
    sql("Open deliverables", """
SELECT d.id, cr.channel_id, cr.handle, c.tracking_code, c.starts_on, d.due_on
FROM deliverables d
JOIN creators  cr ON cr.id = d.creator_id
JOIN campaigns c  ON c.id  = d.campaign_id
WHERE d.post_url IS NULL AND cr.status = 'contracted'
ORDER BY d.id
"""),
    flag_is_true("Live YouTube API?", "LIVE_YOUTUBE"),
    youtube("YouTube · latest uploads", "search", [
        ("part", "snippet"), ("type", "video"), ("order", "date"), ("maxResults", "10"),
        ("channelId", "={{ $json.channel_id }}"),
        ("publishedAfter", "={{ new Date($json.starts_on).toISOString() }}")]),
    code("Fixture uploads", r"""
// Offline fixture shaped like YouTube search.list. One item per open deliverable, same order.
const daysAgo = n => new Date(Date.now() - n * 864e5).toISOString();
const uploads = {
  UC_demo_aria:  [{ id: 'vid_aria_01',  title: 'I cloned my voice in 3 minutes', description: 'Made with a voice agent platform. Code VA-LAUNCH for 20% off.', at: daysAgo(3) }],
  UC_demo_jonas: [{ id: 'vid_jonas_09', title: 'Weekly vlog',                    description: 'Just my week, no sponsor.',                                    at: daysAgo(1) }],
};
return $input.all().map(({ json: d }) => ({ json: {
  items: (uploads[d.channel_id] || []).map(v => ({
    id: { videoId: v.id }, snippet: { title: v.title, description: v.description, publishedAt: v.at },
  })),
} }));
"""),
    code("Find campaign post", r"""
// Pair each upload list with the deliverable it was fetched for, and look for the tracking code.
const open = $('Open deliverables').all();
const rows = [];
$input.all().forEach((item, i) => {
  const d = open[i].json;
  const v = (item.json.items || []).find(v =>
    `${v.snippet.title}\n${v.snippet.description}`.includes(d.tracking_code));
  if (v) rows.push({
    deliverable_id: d.id,
    video_id: v.id.videoId,
    url: `https://www.youtube.com/watch?v=${v.id.videoId}`,
    published_at: v.snippet.publishedAt,
  });
});
if (!rows.length) return [];
return [{ json: { rows } }];
"""),
    sql("Mark live + route to finance", """
WITH m AS (
  SELECT * FROM jsonb_to_recordset($1::jsonb)
    AS m(deliverable_id bigint, video_id text, url text, published_at timestamptz)
), upd AS (
  UPDATE deliverables d
     SET post_url = m.url, platform_post_id = m.video_id,
         posted_at = m.published_at, verified_at = now()
    FROM m
   WHERE d.id = m.deliverable_id AND d.post_url IS NULL
  RETURNING d.id, d.creator_id, d.post_url
), cr AS (
  UPDATE creators c SET status = 'live', updated_at = now()
    FROM upd WHERE c.id = upd.creator_id
  RETURNING c.id, c.handle, c.rate_eur, upd.id AS deliverable_id, upd.post_url
), pay AS (
  -- content verified live → invoice can go to finance
  INSERT INTO payments (deliverable_id, amount_eur)
  SELECT deliverable_id, rate_eur FROM cr
  ON CONFLICT (deliverable_id) DO NOTHING
  RETURNING id
), ev AS (
  INSERT INTO events (workflow, entity, entity_id, action, detail)
  SELECT '04-tracking', 'deliverable', deliverable_id::text, 'verified_live', jsonb_build_object('url', post_url)
  FROM cr
  RETURNING 1
), n AS (
  INSERT INTO notifications (channel, text, dedupe_key)
  SELECT channel, text, key FROM (
    SELECT '#creator-ops' AS channel, format('Live: %s posted %s', handle, post_url) AS text, 'live:' || deliverable_id AS key FROM cr
    UNION ALL
    SELECT '#finance', format('Invoice ready: %s — €%s for deliverable #%s (content verified live)', handle, rate_eur, deliverable_id), 'invoice:' || deliverable_id FROM cr
  ) q
  ON CONFLICT (dedupe_key) DO NOTHING
  RETURNING 1
)
SELECT (SELECT count(*) FROM cr)::int AS verified_live, (SELECT count(*) FROM pay)::int AS invoices_routed
""", ["JSON.stringify($json.rows)"]),
], [
    ("Run now", "Open deliverables"), ("Every 6 hours", "Open deliverables"),
    ("Open deliverables", "Live YouTube API?"),
    ("Live YouTube API?", "YouTube · latest uploads", 0),
    ("YouTube · latest uploads", "Find campaign post"),
    ("Live YouTube API?", "Fixture uploads", 1),
    ("Fixture uploads", "Find campaign post"),
    ("Find campaign post", "Mark live + route to finance"),
])


# ── 05 Metrics → ROI report ─────────────────────────────────────────────────

w05 = workflow("creatorOps000005", "05 · Metrics → ROI report", [
    manual(),
    schedule("Every day 08:00", {"field": "days", "daysInterval": 1, "triggerAtHour": 8}),
    sql("Live posts", """
SELECT d.id AS deliverable_id, d.platform_post_id, d.posted_at,
       current_date::text AS today   -- snapshot date follows the DB's timezone
FROM deliverables d
WHERE d.verified_at IS NOT NULL
ORDER BY d.id
"""),
    flag_is_true("Live YouTube API?", "LIVE_YOUTUBE"),
    code("Batch video ids", r"""
return [{ json: { ids: $input.all().map(i => i.json.platform_post_id).join(',') } }];
"""),
    youtube("YouTube · video stats", "videos", [("part", "statistics"), ("id", "={{ $json.ids }}")]),
    code("Shape snapshots (live)", r"""
// YouTube returns lifetime totals; we store one snapshot per day.
// Clicks come from the link shortener, not YouTube — not wired in this demo, so 0.
const byVideo = Object.fromEntries($('Live posts').all().map(i => [i.json.platform_post_id, i.json.deliverable_id]));
const day = $('Live posts').first().json.today;
const rows = ($input.first().json.items || []).map(v => ({
  deliverable_id: byVideo[v.id], day,
  views: Number(v.statistics.viewCount || 0),
  likes: Number(v.statistics.likeCount || 0),
  comments: Number(v.statistics.commentCount || 0),
  clicks: 0,
}));
return [{ json: { rows } }];
"""),
    code("Fixture snapshots", r"""
// Offline fixture: a saturating curve per post, one snapshot per day since posting
// (so a single run backfills history). Deterministic per deliverable.
const rows = [];
for (const { json: p } of $input.all()) {
  const today = new Date(p.today);
  const posted = new Date(new Date(p.posted_at).toISOString().slice(0, 10));
  const reach = 20000 + (Number(p.deliverable_id) * 7919) % 30000;
  for (let d = new Date(posted), k = 1; d <= today; d = new Date(d.getTime() + 864e5), k++) {
    const views = Math.round(reach * (1 - Math.exp(-k / 3)));
    rows.push({
      deliverable_id: p.deliverable_id, day: d.toISOString().slice(0, 10),
      views, likes: Math.round(views * 0.042), comments: Math.round(views * 0.006),
      clicks: Math.round(views * 0.018),
    });
  }
}
return [{ json: { rows } }];
"""),
    sql("Upsert snapshots", """
WITH x AS (
  SELECT * FROM jsonb_to_recordset($1::jsonb)
    AS x(deliverable_id bigint, day date, views int, likes int, comments int, clicks int)
), up AS (
  INSERT INTO metrics_daily (deliverable_id, day, views, likes, comments, clicks)
  SELECT deliverable_id, day, views, likes, comments, clicks FROM x
  ON CONFLICT (deliverable_id, day) DO UPDATE
    SET views = EXCLUDED.views, likes = EXCLUDED.likes,
        comments = EXCLUDED.comments, clicks = EXCLUDED.clicks
  RETURNING 1
), ev AS (
  INSERT INTO events (workflow, entity, action, detail)
  SELECT '05-metrics', 'metrics_daily', 'upserted', jsonb_build_object('rows', count(*)) FROM up
  RETURNING 1
)
SELECT count(*)::int AS snapshots_upserted FROM up
""", ["JSON.stringify($json.rows)"]),
    sql("ROI by creator", """
SELECT r.*,
       date_trunc('week', current_date)::date::text AS period_from,  -- Monday, in the DB's timezone
       current_date::text                           AS period_to
FROM campaign_roi r
ORDER BY campaign, cost_per_engagement NULLS LAST
"""),
    code("Build weekly report", r"""
const rows = $input.all().map(i => i.json);
const n = v => Number(v ?? 0);
const eur = v => v == null ? '—' : `€${n(v).toFixed(2)}`;
const { period_from: from, period_to: to } = rows[0] ?? {};

const live = rows.filter(r => n(r.views) > 0);
const spend = live.reduce((s, r) => s + n(r.cost_eur), 0);
const eng = live.reduce((s, r) => s + n(r.engagements), 0);
const views = live.reduce((s, r) => s + n(r.views), 0);
const blended = eng ? spend / eng : null;
const best = live.filter(r => r.cost_per_engagement != null)
  .sort((a, b) => n(a.cost_per_engagement) - n(b.cost_per_engagement))[0];

const lines = [
  `# Creator report — week of ${from}`,
  '',
  `Live posts: **${live.length}/${rows.length}** · Views: **${views.toLocaleString('en-US')}** · Spend on live posts: **${eur(spend)}** · Blended cost/engagement: **${eur(blended)}**`,
  '',
  '| Campaign | Creator | Cost | Views | Engagements | Cost / engagement | CTR |',
  '|---|---|---:|---:|---:|---:|---:|',
  ...rows.map(r => `| ${r.campaign} | ${r.handle} | ${eur(r.cost_eur)} | ${n(r.views).toLocaleString('en-US')} | ${n(r.engagements).toLocaleString('en-US')} | ${eur(r.cost_per_engagement)} | ${r.ctr_pct == null ? '—' : r.ctr_pct + '%'} |`),
];
const summary = `Weekly creator report: ${live.length} live posts, ${views.toLocaleString('en-US')} views, blended ${eur(blended)}/engagement` +
  (best ? `. Best: ${best.handle} at ${eur(best.cost_per_engagement)}/engagement` : '');
return [{ json: { from, to, markdown: lines.join('\n'), summary } }];
"""),
    sql("Save report + notify", """
WITH r AS (
  INSERT INTO reports (period_from, period_to, markdown)
  VALUES ($1::date, $2::date, $3)
  ON CONFLICT (period_from, period_to) DO UPDATE SET markdown = EXCLUDED.markdown, created_at = now()
  RETURNING id
), n AS (
  INSERT INTO notifications (channel, text, dedupe_key)
  SELECT '#creator-ops', $4 || ' (report #' || id || ')', 'report:' || $2
  FROM r
  ON CONFLICT (dedupe_key) DO NOTHING
  RETURNING 1
), ev AS (
  INSERT INTO events (workflow, entity, entity_id, action)
  SELECT '05-report', 'report', id::text, 'generated' FROM r
  RETURNING 1
)
SELECT id AS report_id FROM r
""", ["$json.from", "$json.to", "$json.markdown", "$json.summary"]),
], [
    ("Run now", "Live posts"), ("Every day 08:00", "Live posts"),
    ("Live posts", "Live YouTube API?"),
    ("Live YouTube API?", "Batch video ids", 0),
    ("Batch video ids", "YouTube · video stats"),
    ("YouTube · video stats", "Shape snapshots (live)"),
    ("Shape snapshots (live)", "Upsert snapshots"),
    ("Live YouTube API?", "Fixture snapshots", 1),
    ("Fixture snapshots", "Upsert snapshots"),
    ("Upsert snapshots", "ROI by creator"),
    ("ROI by creator", "Build weekly report"),
    ("Build weekly report", "Save report + notify"),
])


# ── 06 Notification dispatcher (outbox → Slack) ─────────────────────────────

w06 = workflow("creatorOps000006", "06 · Notification dispatcher", [
    manual(),
    schedule("Every minute", {"field": "minutes", "minutesInterval": 1}),
    sql("Pending notifications", """
SELECT id, channel, text FROM notifications
WHERE status = 'pending'
ORDER BY id
LIMIT 50
"""),
    flag_is_true("Live Slack?", "LIVE_SLACK"),
    code("Route to Slack channel", r"""
// Creator DMs need Slack Connect / email in production; in the demo they go to one review channel.
return $input.all().map(({ json: n }) => ({ json: {
  id: n.id,
  slack_channel: n.channel.startsWith('#') ? n.channel : '#creator-dms',
  text: n.channel.startsWith('dm:') ? `[to ${n.channel.slice(3)}] ${n.text}` : n.text,
} }));
"""),
    {"name": "Slack · chat.postMessage", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, **RETRY,
     "credentials": SLACK, "parameters": {
         "method": "POST", "url": "https://slack.com/api/chat.postMessage",
         "authentication": "predefinedCredentialType", "nodeCredentialType": "slackApi",
         "sendBody": True, "specifyBody": "json",
         "jsonBody": "={{ JSON.stringify({ channel: $json.slack_channel, text: $json.text }) }}",
         "options": {}}},
    code("Check Slack responses", r"""
// Slack answers HTTP 200 even on failure — the real status is in `ok`.
const sent = [], failed = [];
const routed = $('Route to Slack channel').all();
$input.all().forEach((item, i) => (item.json.ok ? sent : failed).push({ id: routed[i].json.id, error: item.json.error }));
return [{ json: { sent_ids: sent.map(s => s.id), failed } }];
"""),
    {**sql("Mark sent", """
UPDATE notifications SET status = 'sent', sent_at = now()
WHERE id IN (SELECT jsonb_array_elements_text($1::jsonb)::bigint)
RETURNING id
""", ["JSON.stringify($json.sent_ids)"]), "alwaysOutputData": True},  # so a fully failed batch still raises
    code("Raise if any failed", r"""
// Failed rows stay 'pending' and are retried next minute; this raises an alert via the error handler.
const { failed } = $('Check Slack responses').first().json;
if (failed.length) throw new Error(`Slack rejected ${failed.length} message(s): ${failed.map(f => `#${f.id} ${f.error}`).join(', ')}`);
return [{ json: { sent: $('Check Slack responses').first().json.sent_ids.length } }];
"""),
    code("Collect dry-run ids", r"""
return [{ json: { ids: $input.all().map(i => i.json.id) } }];
"""),
    sql("Mark dry run", """
UPDATE notifications SET status = 'dry_run', sent_at = now()
WHERE id IN (SELECT jsonb_array_elements_text($1::jsonb)::bigint)
RETURNING id, channel, text
""", ["JSON.stringify($json.ids)"],
        notes="LIVE_SLACK is off: messages are recorded, not sent"),
], [
    ("Run now", "Pending notifications"), ("Every minute", "Pending notifications"),
    ("Pending notifications", "Live Slack?"),
    ("Live Slack?", "Route to Slack channel", 0),
    ("Route to Slack channel", "Slack · chat.postMessage"),
    ("Slack · chat.postMessage", "Check Slack responses"),
    ("Check Slack responses", "Mark sent"),
    ("Mark sent", "Raise if any failed"),
    ("Live Slack?", "Collect dry-run ids", 1),
    ("Collect dry-run ids", "Mark dry run"),
])


FILES = {
    "00-error-handler.json": w00,
    "01-creator-discovery.json": w01,
    "03-content-reminders.json": w03,
    "04-content-tracking.json": w04,
    "05-metrics-roi-report.json": w05,
    "06-notification-dispatcher.json": w06,
}

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for fname, wf in FILES.items():
        (OUT / fname).write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote workflows/{fname} ({len(wf['nodes'])} nodes)")
