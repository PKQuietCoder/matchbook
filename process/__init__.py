"""Matchbook's hand-written process-mining library.

No a third-party mining framework, no commercial mining tool. Everything here -- XES parsing, the log
representation, discovery, conformance, visualization -- is original code. That
is deliberate: it keeps the algorithms as the teachable content, it keeps the IP
ours, and it keeps the hosted explorer free of AGPL obligations.

The core (`process.log`, `process.store`, `process.xes`, `process.csvio`,
`process.otlp`, `process.dfg`, `process.filters`, `process.variants`,
`process.rules`, `process.viz`) must stay importable with nothing but the
standard library. `process.otlp` emits OpenTelemetry spans without the
`opentelemetry` package, which is the point: a test blocks that import.

`process.config` and `process.cli` additionally read the facts sheet and so
need PyYAML -- the library's only runtime dependency. "No heavy dependencies"
is the accurate claim; "standard library only" would not be.
"""

from process.log import EventLog, Trace  # noqa: F401
