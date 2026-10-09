# Matchbook homework

The Matchbook repository grows across the course, so every student assignment is
stored here beside the code used to complete it. Begin with Module 1, and keep
the files each assignment produces, because later modules reuse the same agent,
the same scenario dataset and the same evaluation records.

Matchbook evaluates the *process* an agent executes, not only the answers it
gives. The subject, the requirement IDs and the tooling are this repository's
own.

Two things here are unusual, and they are the point of the course:

- **A real human event log to compare against.** BPI Challenge 2019 records
  251,734 purchase-to-pay cases executed by people in a real company. The agent's
  runs are mined into the same activity alphabet, so "the agent cleared an
  invoice before the goods receipt" is a measured deviation rather than an
  opinion.
- **What reached the event log, beside what the agent said.** Several records
  below ask for `activities_recorded`. A reply is not evidence.

Start with [module-1/README.md](module-1/README.md), which explains how the
homework holes work: `main` is finished code, so you opt into each assignment's
holes with a generated patch rather than finding them already in the source.

## Module 1

- [Homework 1](module-1/hw1.md) covers the five agent tools, with a guided
  walkthrough in [hw1-tutorial.md](module-1/hw1-tutorial.md).
- [Homework 2](module-1/hw2.md) covers the authenticated endpoint and tracing.
- [Homework 3](module-1/hw3.md) covers the scenario dataset and trace export.

## Not yet adapted

The handouts below still describe a customer-support agent rather than this
repository's purchase-to-pay one, and will not run against it. They are kept in
place so the arc of the course is visible, and each is rewritten when the module
is reached.

- Module 2: [Homework 4](module-2/hw4.md) trace review and failure taxonomy,
  [Homework 5](module-2/hw5.md) an LLM judge.
- Module 3: [Homework 6](module-3/hw6.md) continuous integration,
  [Homework 7](module-3/hw7.md) post-deployment monitoring.
- Module 5: [Homework 8](module-5/hw8.md) accuracy and a Pareto frontier,
  [Homework 9](module-5/hw9.md) reducing cost.

`design.md` schedules the full course layer as milestone 8.
