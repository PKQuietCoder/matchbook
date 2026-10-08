# Matchbook

Matchbook is a purchase-to-pay agent and a hand-written process-mining library,
built on an openly licensed real event log. It is a sibling of the Oakline
course repository: same conventions, different subject. Oakline teaches how to
evaluate an agent's *answers*; Matchbook teaches how to see, measure and govern
the *process* an agent actually executes.

## Find the relevant instructions

- Read [README.md](README.md) for setup and commands. Run commands from the repository root.
- Before changing agent behaviour, read [SPEC.md](SPEC.md) and the relevant function
  contracts. Policy numbers come from `facts.yaml`. Editing the specification alone does
  not change the running application.
- Before touching anything under `logs/`, read [logs/README.md](logs/README.md). It records
  each dataset's DOI, license and reason for being there, plus an exclusion list with
  reasons. **Never add a dataset whose license forbids commercial use or redistribution**,
  and never commit a log that is fetched by DOI.
- For process-mining work, the library is `process/`. It is hand-written on purpose: no
  a third-party mining framework, no other mining framework.

## Shared rules

- **The mining half must run with no API key, no model and no agent dependencies.**
  `tests/test_offline_mining.py` enforces this in a subprocess with those packages
  blocked. If a change makes `process/` need a model, the change is wrong.
- **Policy numbers live in `facts.yaml` and nowhere else.** The LLM writes prose; code
  computes facts. `seed/policies.py` renders the policy corpus from those constants,
  `seed/validate.py` fails the seed if a document disagrees, `process/rules.py` evaluates
  the same constants against any log, and the agent's tools enforce them. A number must
  agree in all four places.
- **Preserve authorization, the approval gate and the kill switch.** Authorization is not
  a prompt. Note that segregation of duties makes it *stateful*: whether a caller may act
  depends on what that caller already did on the same case.
- **Order assumptions are disclosed, never hidden.** 99.99% of BPI 2019 timestamps are
  minute-precision and same-minute ties affect ~17% of events, so order comes from the
  declared `activity_rank` in `facts.yaml`. Every event ordered that way is recorded in
  `EventLog.tie_broken`; `python -m process tie-breaks` reports it. Any result computed
  over tied events must say so.
- **Report what you measured, and separate it from what you inherited.** Two widely
  repeated claims about BPI 2019 are wrong (see `logs/README.md`, "Measured, not
  inherited"). Measure the log; do not cite the description.
- **Keep the agent's log comparable to the human log.** The bridge lifts tool spans onto
  the human log's activity alphabet; model-deliberation spans go to a separate
  `agent-internal` log. Mixing them silently breaks the comparison the repo exists for.
- **Compare control flow across logs; compare performance only within a log.** BPI 2019's
  timing is minute-precision with assumed intra-day order; the agent's spans are
  microsecond-precise.
- **Never present synthetic data as real,** and never claim it is unrelated to BPI 2019.
  It is derived statistics, attributed. Fidelity is reported in `build/fidelity.md`, not
  asserted.
- Handle API keys locally through `.env`. Never request keys in chat, print their values,
  or commit them. Refer to credentials by environment variable name.
- Homework placeholders intentionally raise `NotImplementedError`. Expected failures are
  unfinished work, not proof of completion.
