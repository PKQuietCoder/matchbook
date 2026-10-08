# Homework 3, creating the purchase-to-pay trace dataset

Homework 3 asks you to create a collection of Matchbook traces for Homework 4. A
*scenario* is a planned request with *extra metadata* from the world and the
policy corpus that the request itself may not mention. When you run a scenario,
Matchbook records the conversation, the model calls and the tool calls in a
*trace*.

You will begin by deciding which kinds of requests to include. You will run 30
scenarios first, so you can fix unclear ones and confirm that the agent produces
some failures. You will then create the full set of 250 and export their traces.
In Homework 4 you will use those traces to find repeated failure patterns.

## Working through the assignment with a coding agent

If you would like a coding agent to walk you through this, paste the prompt below
at the start of a session in your repository. It assumes no programming
background, so it suits an analyst or a product manager as well as an engineer.
The agent generates the scenario files with the scenario skill; the handout's
review points are where you decide.

> Walk me through Homework 3 in `homework/module-1/hw3.md` as an interactive
> tutorial. Read `AGENTS.md`, the handout, `SPEC.md` and
> `scenarios/skill/SKILL.md` first. I may not have a programming background, so
> assume nothing about what I know, and adapt once you see what I do know.
>
> I am driving. Work one step at a time, in the handout's order. Before each
> step, explain in plain language what you propose to do and why the assignment
> needs it, and show me the command you would run or the change you would make.
> Then wait for me to say go. Do not run a command, change a file, or generate
> anything until I have said so, and do not take several steps on one go ahead.
> Reading files to prepare a proposal is fine. Once I say go, do that step, show
> me the result, and explain what it means. Move on only when you are confident I
> understand the current step. One short question about what I expect to see, or
> what a result means, is enough to check; keep questions few, and do not turn
> the session into a quiz. Explain every unfamiliar term the first time it
> appears, using the actual files and outputs as examples. When a picture would
> help, draw one; a text diagram is fine.
>
> If something fails, read the error, explain it plainly, and propose a focused
> fix. Keep a short progress note of what is done and what is next, so we can
> resume later, and keep a checklist of every deliverable so nothing is skipped.
> Leave the assessments and the video to me. Do not call the assignment done
> until every file in the "Files to commit" list exists and the checks in the
> handout pass.
>
> Concepts I need to understand before we use them: what a scenario is and why it
> records extra metadata; why that metadata comes from the world, the policy
> corpus and the control code rather than from a model; what a dimension and a
> tuple are; why the coverage set and the challenge set are kept apart; what the
> pilot is for; and why `activities_expected` is usually empty. Stop at each
> review point -- the dimension plan, the pilot review, the final review -- and
> let me make the decisions there. Diagrams that would help: the path from
> dimensions to tuples to requests to extra metadata to traces, and the flow from
> the scenario file through the runner and the endpoint into Langfuse.

## Expected work

- Estimated time: 4 to 6 hours of your own work, excluding model response time.
- Run 30 pilot scenarios and review at least 10 results.
- Confirm at least five agent failures.
- Create and run 250 final scenarios on one model.
- Review 15 final scenarios, export the traces, record a video of no more than
  5 minutes.

The runner sends requests one at a time and each scenario needs several model
calls. Measured on this repository rather than estimated: one live
`claude-sonnet-5` session cost **$0.0133** and took about 5.5 seconds. Budget for
roughly 280 scenario runs in all, which is about **25 minutes of unattended time
and about $4**, before reruns. Multi-turn scenarios cost proportionally more.

## Preparation

Continue in the same repository as Homework 2. Homework 3 needs the Homework 2
endpoints, because the runner sends every request through them, and the Homework 2
span attributes, because the run report counts roles and permission denials from
them.

Homework 2 was optional. If you did not complete it, apply the reference
implementations before starting the server:

```bash
git apply homework/module-1/hw1-reference.patch    # only if you skipped HW1 too
git apply homework/module-1/hw2-reference.patch
```

If you completed them, keep your own implementations and do not apply the patches.

Generate the world and start the trace stack:

```bash
uv run python -m seed.generate
uv run python -m seed.validate
docker compose -f observability/docker-compose.yml up -d
```

Start the endpoint in a second terminal and leave it running:

```bash
uv run uvicorn server.app:app --port 8010
```

Confirm it says `tracing -> http://localhost:3001`. If it says tracing is off,
`.env` is missing the `LANGFUSE_*` values and you will run 250 scenarios that
produce no traces.

Read before generating scenarios:

- `SPEC.md`, which defines required behaviour and supplies the requirement ids.
- `scenarios/skill/SKILL.md`, which explains how to generate grounded scenarios.

Ask your coding agent to follow the scenario skill. You review the plan and a
sample of its scenarios yourself.

## Part A, plan the scenario dataset

You will decide which kinds of requests the dataset must include. A *dimension*
is one way requests can differ, such as the actor's role or intent. Planning the
dimensions before generation helps a coding agent create a varied dataset instead
of 250 variations of one request.

Start a coding agent session from the repository root, then use this prompt:

> Read `scenarios/skill/SKILL.md` and `SPEC.md`. Query the `actors`,
> `po_items` and `data_quality_cases` tables. Propose the scenario dimensions and
> their values, then stop so I can review them before generation.

The plan must include:

- Authenticated role.
- Intent.
- The item involved, and its state.
- The applicable policy document.
- Number of tool calls needed.
- Request difficulty.
- **Matching flow.**

That last one has no Oakline equivalent and it is the one most often forgotten.
Every control rule is flow-scoped: a missing goods receipt is a breach on a 3-way
flow and correct on a 2-way one, and `Consignment` is outside invoice matching
altogether. A dataset that is all 3-way exercises a quarter of the control logic
and will look fine while doing it.

```bash
sqlite3 -header -column data/matchbook.db \
  'SELECT flow, count(*) AS items, sum(payment_blocked) AS blocked
     FROM po_items GROUP BY flow ORDER BY items DESC;'
```

Weight the coverage set toward that observed mix rather than inventing a uniform
split, and note that `facts.yaml:flow_shares_observed` records the real
distribution in BPI 2019.

Add another dimension only when `SPEC.md` or the world provides a reason. Each
scenario also records its turn count, which equals one plus the number of
followups; the validator checks it.

The *coverage set* includes ordinary and difficult requests across every
important dimension. The *challenge set* includes requests that are intentionally
difficult: missing information, a correction across turns, an authorization
boundary, a stateful duty conflict, a damaged record, an amount above the limit.

Record the group in `scenario_group` as either `coverage` or `challenge`. Do not
include prompt injection or malicious documents; Module 5 covers deliberate
attacks.

The world contains three deliberately damaged records. Read them before planning
the challenge set:

```bash
sqlite3 -header -column data/matchbook.db \
  'SELECT case_id, entity_type, entity_id, description, expected_handling
     FROM data_quality_cases ORDER BY case_id;'
```

Three rather than Oakline's six, so the quota per record is higher: the final
challenge set needs **at least 15 scenarios for each**, with the rest drawn from
the other challenge shapes. A scenario about a damaged record must use an actor
who may access it and must record the matching `case_id` in
`data_quality_case_id`. Other scenarios set it to `null`.

One of the three is worth reading twice. `dq-block-without-set-event` exists
because in the real BPI 2019 log there are 57,137 `Remove Payment Block` events
against 124 `Set Payment Block` — so a block with no setting event is not
misconduct, it is a log-completeness artefact faithfully reproduced here. A
scenario expecting the agent to treat it as fraud would be wrong about the data.

## Part B, run a pilot and confirm failures

Run a small pilot before creating all 250. The pilot is a cheaper way to find
invalid or repetitive scenarios, and it confirms the agent produces failures you
can study in Homework 4.

Have the coding agent generate `scenarios/pilot_scenarios.jsonl` with the skill:
30 scenarios with broad role, intent and flow coverage, including ordinary and
difficult requests from the dimensions in Part A.

Each scenario must record what the agent should do and the source that supports
it. The skill shows the required fields. Two are easy to get wrong:

- **`activities_expected`** is the list of business activities the run should
  contribute to the event log, not the tools it should call. It is usually `[]`.
  A lookup contributes nothing, a refused write contributes nothing, and **a
  clearing queued for a controller contributes nothing** — SPEC RESP-3. A
  scenario expecting `["Clear Invoice"]` there marks correct behaviour as a
  failure.
- **`expected.source`** must name where the answer comes from: a world query, a
  policy document, a control function, the damaged-record table, or the
  specification. Never a model's judgement.

Validate, then run:

```bash
uv run python -m scenarios.validate scenarios/pilot_scenarios.jsonl

uv run python -m scenarios.runner scenarios/pilot_scenarios.jsonl \
  --model claude-sonnet-5 --output scenarios/pilot-results.jsonl
```

The validator checks more than shape. It confirms every actor exists in the world
*with the role the scenario claims*, every item exists, every policy is a rendered
document, every flow is a real `Item Category`, and every requirement id is
declared in `SPEC.md`. Repair everything it reports before running: a run costs
money, and a scenario with a wrong answer key poisons every label, judge and
report built on it afterwards.

Review at least 10 pilot results in Langfuse at `http://localhost:3001`. Start
with the difficult scenarios, and with any run where the agent used an unexpected
tool, changed data, or gave an answer that conflicts with the recorded metadata.

The result file gives you a shortcut the trace UI cannot. Each record carries
`tool_order` and `activities_recorded`. Sort by the gap between them: a run that
called `clear_invoice` and recorded no activity either refused correctly or
claimed something it did not do, and only one of those is fine.

```bash
python3 -c "
import json
for line in open('scenarios/pilot-results.jsonl'):
    r = json.loads(line)
    print(r['scenario_id'], r['expected']['outcome'],
          '| tools', len(r['tool_order']), '| activities', r['activities_recorded'])
"
```

Create `scenarios/pilot_review.jsonl` with one record per scenario you review:

- `scenario_id`
- `scenario_valid`, `true` or `false`
- `confirmed_failure`, `true` or `false`
- `evidence`, naming the world value, policy, tool result or requirement that
  supports your decision
- `scenario_change`, or `null` when no revision is needed

Count a failure only when `scenario_valid` is `true` **and** the observed
behaviour conflicts with the recorded metadata or a clear requirement in
`SPEC.md`. An agent that refuses correctly by a route you did not predict is not
a failure.

The pilot review must contain at least five confirmed failures. If the first 30
produce fewer, add 20 challenge scenarios, reset the world with
`uv run python -m seed.generate`, and run the pilot again.

Do not name or group failure modes here. Homework 4 begins the open coding and the
taxonomy.

## Part C, create and review the final scenarios

Use the pilot review to create the final file. Revise invalid or repetitive
scenarios, then generate more from the plan in Part A.

Save the final dataset in `scenarios/p2p_scenarios.jsonl`. It must contain:

- 175 scenarios with `scenario_group` set to `coverage`.
- 75 with `scenario_group` set to `challenge`, including at least 15 for each of
  the three damaged records.

Give every final scenario a new identifier, distinct from the pilot's. The export
in Part E selects traces by scenario identifier, so a reused one would pull the
pilot's traces in too.

In Homework 4 you will review 100 traces and search the rest for similar failures.
250 scenarios provides enough for both steps.

Do not select a scenario only because the model failed on that exact request. Use
the underlying dimension — a 2-way flow, a stateful duty conflict, a damaged
record — to generate new cases.

Review 15 scenarios before the full run. Include both groups, all three roles,
and every intent. Save each decision in `scenarios/p2p_review.jsonl` with
`scenario_id`, `decision`, `reason` and `change`, using `accept`, `revise` or
`reject`. Apply every revision and replace every rejection.

Select 50 of the final scenarios for a later comparison, covering both groups and
all three roles, and save them in `scenarios/monitoring_scenarios.jsonl`.
Homework 7 runs the same requests after the agent changes.

Validate the final file:

```bash
uv run python -m scenarios.validate scenarios/p2p_scenarios.jsonl --final
```

`--final` adds the group counts, the per-damaged-record quota, and a check that
all three roles and all seven intents appear. Repair every error before the run.

## Part D, run the final dataset

Run all 250 on the model you chose in the pilot. One model keeps the traces
comparable.

Reset the world first. The pilot cleared invoices and recorded receipts, and the
recorded metadata assumes the seeded state:

```bash
uv run python -m seed.generate
```

Then run:

```bash
uv run python -m scenarios.runner scenarios/p2p_scenarios.jsonl \
  --model claude-sonnet-5 --output scenarios/final-results.jsonl
```

Results are written after every scenario, so a run that dies at 200 does not lose
the first 199. A scenario whose request fails is recorded with a status other
than `completed`. Fix the cause, then rerun only the affected scenarios into the
same file:

```bash
uv run python -m scenarios.runner scenarios/p2p_scenarios.jsonl \
  --model claude-sonnet-5 --output scenarios/final-results.jsonl --resume
```

`--resume` keeps every completed record and reruns those missing or with another
status. To rerun named scenarios instead — because Part E reports one without a
trace — pass `--ids mb-0153,mb-0201`. A rerun does **not** reset the world, so
before rerunning a scenario that clears an invoice, check the first attempt did
not already do it. If many failed, reset the world and run the full set again.

Confirm the run:

```bash
jq -s 'group_by(.status) | map({status: .[0].status, count: length})' \
  scenarios/final-results.jsonl
```

Keep the coverage and challenge counts separate when you summarize. The challenge
set contains harder requests by design.

## Part E, check and export the traces

Run the smoke report against the trace store:

```bash
docker compose -f observability/docker-compose.yml exec -T clickhouse \
  clickhouse-client --user clickhouse --password clickhouse --database default \
  --multiquery --format PrettyCompact < reports/smoke.sql \
  | tee reports/smoke-output.txt
```

It counts traces per scenario and per role, observation levels, tool calls and
permission denials, what reached the event log, queued clearings, the longest
traces, and tokens and cost. It describes what ran, not how well the agent did.

Three rows deserve attention:

- **`flag_missing` must be zero.** `mb.permission_denied` is recorded on every
  tool call, not only denied ones, so the denial rate has a real denominator. A
  nonzero count means the Homework 2 recorder sets the attribute only when true,
  and every rate computed from it is meaningless.
- **The mixed-prompt-version query must return no rows.** A scenario whose traces
  span two prompt versions was run across an edit, and its results are not
  comparable with each other.
- **The cost column is an undercount**, because Langfuse prices
  `gen_ai.usage.input_tokens`, which for Anthropic excludes cache reads and
  writes. Use `python -m bridge.cost` for money.

Then export the traces for Homework 4:

```bash
uv run python -m scenarios.export_langfuse \
  scenarios/p2p_scenarios.jsonl traces/p2p_traces.json
```

The export **fails and writes nothing** when a scenario has no trace. That
refusal is the feature: a silent partial export is how a review draws conclusions
from 180 traces believing it saw 250, and the missing ones are never random —
they are the slow, the erroring, and the ones still sitting in the span
exporter's buffer when the server stopped. Rerun the scenarios it names with
`--ids` until the export succeeds.

Open three exported traces and confirm each contains the conversation, the model
name, the tool activity and its scenario identifier in `mb_scenario_id`. Include
one challenge scenario and one with more than one turn.

**Then mine the run, which is the step Oakline has no equivalent for.** A
scenario dataset whose traces never reach an event log would be a Oakline
dataset that happens to live in this repository:

```bash
uv run python -m bridge.spans_to_log build/spans.db --log-id hw3-business --layer business
uv run python -m bridge.spans_to_log build/spans.db --log-id hw3-attempts --layer attempts
uv run python -m process compare bpic19-sample hw3-business
```

Record what you see. Expect the business log to be much smaller than 250 cases:
a well-guarded agent refuses most exception work, so most runs contribute no
business event at all. That is a real result rather than a disappointment, and
it is why the `attempts` layer exists — refusals, retries and queued clearings
are visible there as distinct activities. Note the case count in each layer and
how it compares with the number of scenarios you ran.

## Files to commit

- `scenarios/pilot_scenarios.jsonl`
- `scenarios/pilot-results.jsonl`
- `scenarios/pilot_review.jsonl`
- `scenarios/p2p_scenarios.jsonl`
- `scenarios/p2p_review.jsonl`
- `scenarios/monitoring_scenarios.jsonl`
- `scenarios/final-results.jsonl`
- `reports/smoke-output.txt`
- `traces/p2p_traces.json`

## Video

Record one continuous screen video of no more than 5 minutes. Show:

1. One pilot scenario that failed, with its extra metadata and your evidence.
2. One final scenario you revised after review, and why.
3. One complete final trace, showing its scenario identifier and tool activity.
4. The mined log from Part E beside the number of scenarios you ran, and your
   explanation of the difference.
5. Regenerate the number of final scenario identifiers:

```bash
jq '[.traces[].mb_scenario_id] | unique | length' traces/p2p_traces.json
```

Every statement in the video must agree with the committed files and the traces
shown on screen.
