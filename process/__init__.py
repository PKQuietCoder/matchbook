"""Matchbook's hand-written process-mining library.

No a third-party mining framework, no commercial mining tool. Everything here -- XES parsing, the log
representation, discovery, conformance, visualization -- is original code. That
is deliberate: it keeps the algorithms as the teachable content, it keeps the IP
ours, and it keeps the hosted explorer free of AGPL obligations.

The core (`process.log`, `process.store`, `process.xes`, `process.csvio`,
`process.dfg`) must stay importable with nothing but the standard library.
"""

from process.log import EventLog, Trace  # noqa: F401
