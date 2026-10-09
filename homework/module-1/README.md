# Module 1 — how the holes work

Matchbook's `main` branch is finished code: `uv run pytest` is green and the
README's measured numbers depend on the tools actually working. So the homework
holes are **not** committed into the source. You opt into them.

A course repo could ship its holes in the starter, leaving the suite red on a
fresh clone. This repo cannot do that without making its own claims unverifiable,
so each assignment comes as a pair of generated patches instead.

## Homework 1 — the five tools

```bash
git apply homework/module-1/hw1-holes.patch     # empty the five tool bodies
uv run pytest --runxfail tests/test_hw_holes.py -k hw1
```

Signatures and docstrings stay. The docstrings are the contract the handout
tells you to follow, and `clear_invoice`'s lists the nine ordered checks of
SPEC TOOL-6.

To undo, from any state your copy is in:

```bash
git checkout -- agent/tools.py
```

## What you should expect to be broken

With the holes open, `uv run pytest` is **red**: 19 tests in
`tests/test_tools.py` exercise the functions you are about to write. That is the
signal, not a bug — the handout asks you to run those suites once Part A is
done, and watching them go green is the point.

`tests/test_hw_holes.py` behaves the other way round. Its tests are marked
`xfail`, so they go *quiet* when the holes are open and a casual `pytest` looks
fine. This is deliberate and it is why every handout gives you `--runxfail`
instead:

| command | finished code | holes open |
| --- | --- | --- |
| `pytest tests/test_hw_holes.py` | 13 xpassed | 13 xfailed (quiet) |
| `pytest --runxfail tests/test_hw_holes.py` | 13 passed | 13 failed, loudly |

`AGENTS.md` states the rule this rests on: *expected failures are unfinished
work, not proof of completion.*

`tests/test_offline_mining.py` keeps passing throughout. The mining half does
not import the agent, so holing a tool cannot touch it.

## The agent still runs with holes in it

```bash
uv run python -m agent --script diagnose_block --reset
```

```
  tool get_purchase_item      -> not_implemented
  tool get_three_way_match    -> not_implemented
```

`agent/agent.py`'s `_call` seam converts a `NotImplementedError` into
`{"ok": false, "error": "not_implemented", "reason": ...}`, so a half-built
agent still holds a conversation and you can see exactly which tool is missing.

One thing to notice while you are there, because it previews Module 2: with
every tool returning `not_implemented`, the **scripted** session still prints a
confident final answer quoting a €120 variance. The script's reply is fixed
text, so it cannot know its tools failed. That is a premature success claim in
miniature — a reply that is not evidence — and `python -m analysis.review` exists
because the transcript is the last place you would catch it.

## Regenerating a patch

The patches are generated from the real code, never hand-edited, so they cannot
drift from it. Each header records the commit it came from. If `agent/tools.py`
changes, regenerate rather than patching the patch: hole the functions, then
`git diff -- agent/tools.py` for the holes file and `git diff -R` for the
reference one.
