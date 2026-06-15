"""
agentteams.cli — command-line orchestration package.

build_team.py (the `agentteams` console-script entry) is being decomposed into
cohesive modules under this package (CH-07 modular structure). build_team.py
remains a thin facade that re-exports the public surface so existing imports,
monkeypatch targets, and the console-script entry point keep working unchanged.
"""
