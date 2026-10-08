"""Planned requests, run through the endpoint, recorded as traces.

A *scenario* is a request plus the metadata that says what the agent ought to do
about it -- taken from the world and the policy corpus, not from a model's
opinion. Running one produces a trace. Module 2 reviews those traces.
"""
