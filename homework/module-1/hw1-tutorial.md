# Homework 1 tutorial

This walkthrough is for students who want guided help. Your coding agent should
follow the steps and checkpoints below when you choose the tutorial.

Help me complete Homework 1 for the Matchbook course as an interactive tutorial.

I want to do the homework and understand the decisions I'm making. You can handle
the implementation and terminal commands. Adapt your explanations to what I
already know, and help me connect the technical work to the business behaviour I
am evaluating.

We are working in my local copy of the Matchbook repository. It already includes
`SPEC.md`, `facts.yaml`, a real human event log, and a purchase-to-pay agent
whose five tools I am about to write.

## How to work with me

- Give me one manageable step at a time. Briefly explain its purpose, do the
  technical work you can, and show me the result. Pause at the checkpoints below
  so I can ask questions or make a decision.
- Ask one question at a time when you need information. Inspect the current
  folder and available tools before asking me something you can determine
  yourself.
- Once I answer a checkpoint or ask you to continue, carry out the agreed step
  and proceed to the next question that needs my input. Avoid a separate
  permission question for each command, or an extra "ready to continue?" after I
  have already answered.
- Explain unfamiliar terms when we encounter them, using the actual files and
  results as examples. Keep explanations short unless I ask for more. Terms I
  will need early: purchase-to-pay, three-way match, goods receipt, payment
  block, tolerance, segregation of duties, and what a *case* is in this
  repository.
- Ask me to predict or assess behaviour in ordinary language. Show the actual
  result and ask what I notice before suggesting a pass/fail label or the source
  of a failure. If I am unsure, help me compare the result with the relevant
  requirement, then let me make the assessment. If my answer conflicts with the
  specification, explain the conflict and ask me to reconsider.
- If something fails, inspect the error and try a focused fix. Explain what
  happened in plain language. If we stay stuck, prepare a short message for the
  course forum with the step, the error, and what we tried, with secrets removed.
- Keep a short local progress note with completed steps, evidence, and the next
  step, so we can resume later. Maintain a checklist of all deliverables,
  including the additional-tool requirement and my video, so unfinished work
  stays visible. Keep the note separate from the homework submission files.

## 1. Get oriented

Check whether this session is in the Matchbook repository. If it is, use the
existing files and preserve any work.

Read [AGENTS.md](../../AGENTS.md), this handout's
[HW1 instructions](hw1.md), and [module README](README.md), along with
`README.md` and `SPEC.md` at the repository root. Inspect code as needed. Use the
handout as the checklist. I have chosen the tutorial, so begin orientation
without asking me to choose a help style again.

Explain what Matchbook is and why we will use it for later evals. Two things
deserve explaining properly before we touch code:

- **`SPEC.md` is never loaded by the running application.** Show me where it
  lives, summarize what it defines, and explain how an intended behaviour becomes
  either a model instruction, a code check, or a number in `facts.yaml`. Editing
  `SPEC.md` alone changes nothing.
- **`facts.yaml` is the only place a policy number lives.** Run
  `uv run python -m seed.validate` and explain what it would catch. This is how
  the policy a document quotes, the check a tool enforces, and the rule applied
  to the real human log stay the same number.

Give me a short overview of the whole HW1 finish line: the five tools, the
additional tool Part A asks for, the ten recorded conversations, the prompt
investigation, and the video. Then focus on our first milestone: one real
conversation with the local agent.

Checkpoint: ask me whether the relationship between the specification, the facts
sheet, and the running agent is clear before moving on.

## 2. Get one conversation working

Check the Python and uv setup, install dependencies with the agent extra, and
generate the local world per the README. Preserve existing files and settings. If
the world already exists, note that regenerating it is deterministic and is the
intended way to reset.

Explain that `.env` is a settings file and that the agent needs
`ANTHROPIC_API_KEY` to hold a live conversation. Show me how to put the key in
locally, following the repository's credential rules — never print its value, and
never paste it into a chat. Explain any account setup or billing I must handle
myself.

The tests and the seed commands work without a key. If I cannot get one yet, we
can do Part A completely and mark the live conversations as pending.

Apply the hole patch and run the baseline checks. Explain the difference between
a test that fails because my code is unfinished and a test that fails because
something is broken, and show me why `--runxfail` matters. An expected failure
does not mean the function is complete.

Then start a real conversation as `ap-003` about item `4507001234_00010`. Before
running it, ask what I expect the agent to do and wait for my answer. Afterwards,
show me that every tool returned `not_implemented` and the agent still produced a
reply — and ask me what I make of that.

Checkpoint: show the actual response and ask whether it met my expectation. Do
not count a simulated response as a real run.

## 3. Complete the tools with me

Follow HW1 Part A for `get_purchase_item`, `get_three_way_match`, `get_policy`,
`record_goods_receipt` and `clear_invoice`. Use the docstring contracts and the
supplied helpers in `agent/db.py` and `seed/controls.py`. For each tool, briefly
explain its purpose and ask me about one relevant success or failure case before
implementing it.

Three of these need real discussion rather than just code, so slow down:

- **`get_three_way_match` must not do arithmetic.** Explain SPEC TOOL-7 and why
  a number the model computed is a failure even when it is correct.
- **`record_goods_receipt` depends on case history.** Show me two buyers with
  identical roles and scope getting different answers, and explain why no prompt
  could implement that rule.
- **`clear_invoice`'s nine checks are ordered.** Walk me through SPEC TOOL-6 and
  ask me why I think the amount is checked *last*, before telling me the
  specification's reason.

If a docstring and a helper disagree, inspect the implementation and the tests,
explain the mismatch, and apply the repository's rules.

Run the focused HW1 tests with `--runxfail` so unfinished functions cannot hide,
then the regression suites the handout names. When a conversation reveals a
missing capability, help me define a tool, implement it, register it in `TOOLS`
and the right role set, and add it to `bridge/activity_map.yaml` if it records a
business activity.

Checkpoint: show what now works and which checks pass. Ask whether I want any
part explained before we examine more conversations.

## 4. Help me examine behaviour

Walk me through Part B one conversation at a time. Cover every required case and
all three roles, then help me design the rest to reach at least ten. Ask what I
expect before each run, then ask whether the observed behaviour met it.

Start each conversation fresh so an earlier request cannot influence it, and keep
follow-up turns of one conversation together.

Handle the mechanical work of capturing the request, the tool calls and results,
the final response, and `activities_recorded` into `hw1-session.jsonl`. Use
`--debug`. Do not invent a tool call or a result. Use my assessment for the
judgement fields, and help me distinguish a prompt failure, a tool failure, and
an unclear requirement.

Draw my attention to any case where the reply and `activities_recorded` disagree,
and explain why that gap is the thing this repository exists to measure.

Explain when a receipt or clearing changes the world, and re-seed between
conversations when needed, preserving records we have already saved and keeping
state consistent within each conversation.

Track missing capabilities as we go. Before leaving Part B, review them with me
and choose a useful additional tool to satisfy Part A. Keep that requirement
marked pending until it is done.

## 5. Investigate a prompt improvement and finish

Guide me through Part C. Help me compare `SPEC.md` against
`SYSTEM_PROMPT_TEMPLATE` family by family, predict a failure, and test it. Do not
tell me which requirement is missing — help me find one and then test whether its
absence actually matters.

Change the prompt only when a recorded conversation supports the change, then
rerun the same case with the same starting data. If none of the omissions I test
causes a failure, help me write up which ones I tested and why no edit was
justified. Explain that this is a complete answer, not a failed assignment.

Check every deliverable against the handout. Verify the conversation records and
run the required checks. Prepare the relevant local commits, excluding `.env` and
unrelated files. Leave publishing or pushing to me.

Help me prepare the demonstration video of no more than five minutes, with a
short checklist of what I must show. I will record it and explain my own
observations. Do not mark the recording complete until I have made it.

Stay on HW1. Docker, Langfuse and HW2 can wait.

Start by checking the current folder and helping me get oriented. Do not execute
the entire tutorial in one turn.
