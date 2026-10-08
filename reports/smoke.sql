-- The Homework 3 smoke report: what ran, not how well it went.
--
--   docker compose -f observability/docker-compose.yml exec -T clickhouse \
--     clickhouse-client --user clickhouse --password clickhouse \
--     --database default --multiquery --format PrettyCompact \
--     < reports/smoke.sql | tee reports/smoke-output.txt
--
-- Every number here describes coverage and shape: how many traces per scenario
-- and role, how many erred, which tools were called, how many calls were
-- refused, what reached the event log, and what it cost. None of it says the
-- agent behaved well. That judgement is Module 2's work, from the traces this
-- report only counts.
--
-- Every query says FINAL. Langfuse's ClickHouse tables are ReplacingMergeTree,
-- which keeps superseded rows until a background merge decides to run, so a
-- plain SELECT counts some traces twice. Without it this report quietly
-- overstated a trace count by one row out of 26, which is exactly the size of
-- error nobody notices.
--
-- Two more things to read carefully rather than at a glance.
--
-- `mb.permission_denied` is extracted from the span attributes JSON and is
-- present on EVERY tool call, not only the denied ones -- so the denial rate
-- below has a real denominator. If that column is ever empty rather than
-- 'false', the Homework 2 recorder is only setting the attribute when it is
-- true, and every rate computed from it is meaningless.
--
-- The cost column understates this provider. Langfuse prices
-- gen_ai.usage.input_tokens, which for Anthropic excludes cache reads and
-- writes; those are billed, and they sit under mb.* because the four counts are
-- disjoint and charged at three different rates. `python -m bridge.cost` is
-- authoritative for money. On one measured session the gap was $0.0055 here
-- against $0.0106 there.

SELECT '--- traces per scenario ---' AS report;
SELECT
  metadata['scenario_id'] AS scenario_id,
  count() AS traces,
  countDistinct(session_id) AS sessions,
  countDistinct(metadata['prompt_version']) AS prompt_versions
FROM traces FINAL
WHERE is_deleted = 0 AND metadata['scenario_id'] != ''
GROUP BY scenario_id
ORDER BY scenario_id;

SELECT '--- traces per role, and per actor ---' AS report;
SELECT
  metadata['role'] AS role,
  user_id AS actor_id,
  count() AS traces
FROM traces FINAL
WHERE is_deleted = 0
GROUP BY role, actor_id
ORDER BY role, actor_id;

SELECT '--- scenarios with more than one prompt version (a mixed run) ---' AS report;
-- A scenario whose traces span two prompt versions was run before and after an
-- edit, so its results are not comparable with each other. Expect zero rows.
SELECT
  metadata['scenario_id'] AS scenario_id,
  groupUniqArray(metadata['prompt_version']) AS versions
FROM traces FINAL
WHERE is_deleted = 0 AND metadata['scenario_id'] != ''
GROUP BY scenario_id
HAVING length(versions) > 1
ORDER BY scenario_id;

SELECT '--- observation levels: errors and warnings ---' AS report;
SELECT level, count() AS observations
FROM observations FINAL
GROUP BY level
ORDER BY observations DESC;

SELECT '--- tool calls, and how many were refused ---' AS report;
SELECT
  JSONExtractString(metadata['attributes'], 'gen_ai.tool.name') AS tool,
  count() AS calls,
  countIf(JSONExtractString(metadata['attributes'], 'mb.permission_denied') = 'true') AS permission_denied,
  countIf(JSONExtractString(metadata['attributes'], 'mb.permission_denied') = '') AS flag_missing
FROM observations FINAL
WHERE tool != ''
GROUP BY tool
ORDER BY calls DESC;

SELECT '--- what reached the event log ---' AS report;
-- A business activity is a write that changed the world. A queued clearing is
-- deliberately absent from this list and present in the next one: SPEC RESP-3.
SELECT
  JSONExtractString(metadata['attributes'], 'mb.activity') AS activity,
  count() AS events
FROM observations FINAL
WHERE activity != ''
GROUP BY activity
ORDER BY events DESC;

SELECT '--- clearings queued for a controller (recorded, but NOT paid) ---' AS report;
SELECT
  JSONExtractString(metadata['attributes'], 'mb.item_key') AS item_key,
  count() AS queued
FROM observations FINAL
WHERE JSONExtractString(metadata['attributes'], 'mb.queued') = 'true'
GROUP BY item_key
ORDER BY queued DESC;

SELECT '--- the longest traces, by observation count ---' AS report;
SELECT
  t.metadata['scenario_id'] AS scenario_id,
  t.id AS trace_id,
  count(o.id) AS observations,
  round(dateDiff('millisecond', min(o.start_time), max(coalesce(o.end_time, o.start_time))) / 1000, 2) AS seconds
FROM traces AS t FINAL
INNER JOIN observations AS o FINAL ON o.trace_id = t.id
WHERE t.is_deleted = 0
GROUP BY scenario_id, trace_id
ORDER BY observations DESC, seconds DESC
LIMIT 10;

SELECT '--- tokens and cost, by model (an UNDERCOUNT: see the header) ---' AS report;
SELECT
  coalesce(provided_model_name, 'unmeasured') AS model,
  count() AS model_calls,
  sum(usage_details['input']) AS input_tokens,
  sum(usage_details['output']) AS output_tokens,
  round(sum(coalesce(total_cost, 0)), 6) AS usd_langfuse
FROM observations FINAL
WHERE type = 'GENERATION' OR provided_model_name IS NOT NULL
GROUP BY model
ORDER BY model_calls DESC;
