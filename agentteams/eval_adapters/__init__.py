"""Eval-framework adapters (Cluster A Phase 2, increments 2+3).

Each adapter is a CODE GENERATOR: it translates the framework-neutral
``agentteams.eval_suite.build_eval_suite`` output into source for a specific
eval framework. Adapter modules never import the target framework (it is an
optional downstream dependency, not an agentteams runtime dependency) — the
framework import lives only inside the generated source text. This keeps the
neutral core (``eval_suite.py``) framework-free and honors the scope test:
the generator emits an artifact a target runtime consumes.
"""
