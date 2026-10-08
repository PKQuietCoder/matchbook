"""The mining half must work with no API key and no agent dependencies.

This is a load-bearing guarantee, not a nicety: the moment `process` needs a
model key, it stops being usable as teaching material and stops being runnable
in CI. The test asserts it mechanically by importing every mining module in a
subprocess with the agent packages blocked and the credential environment
stripped.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

MINING_MODULES = [
    "process.log",
    "process.store",
    "process.xes",
    "process.csvio",
    "process.dfg",
    "process.filters",
    "process.variants",
    "process.rules",
    "process.viz",
    "process.config",
    "process.cli",
]

# `anthropic` is here because it is now the agent half's model dependency. The
# guarantee is only worth anything if it names the package the agent actually
# imports; leaving a retired one in its place would pass while testing nothing.
BLOCKED = ("anthropic", "openai", "openai_agents", "agents", "litellm", "fastapi", "opentelemetry")

PROBE = f"""
import sys

class Blocker:
    def find_module(self, name, path=None):
        if name.split(".")[0] in {BLOCKED!r}:
            return self
        return None
    def load_module(self, name):
        raise ImportError("blocked for the offline-mining guarantee: " + name)

sys.meta_path.insert(0, Blocker())
sys.path.insert(0, {str(REPO_ROOT)!r})

for module in {MINING_MODULES!r}:
    __import__(module)

# And actually do the work, not just import it.
from process import csvio, dfg, rules, variants, config
log = csvio.read_csv(
    {str(REPO_ROOT / "logs" / "snapshot" / "bpic19-sample-events.csv.gz")!r},
    log_id="probe",
)
csvio.read_case_attributes(log, {str(REPO_ROOT / "logs" / "snapshot" / "bpic19-sample-cases.csv.gz")!r})
graph = dfg.build(log)
report = rules.report(log, config.load_facts())
assert log.case_count > 2000 and len(graph) > 50 and report["total_violations"] > 0
print("OFFLINE OK", log.case_count, len(graph), report["total_violations"])
"""


def test_mining_runs_with_no_key_and_no_agent_packages():
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},  # no API keys of any kind
    )
    assert result.returncode == 0, result.stderr
    assert "OFFLINE OK" in result.stdout
