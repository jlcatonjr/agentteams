# Self-Team Build Descriptor

`agentteams` self-maintains its own agent team via
`python build_team.py --self --update --merge`. The descriptor it
reads from is `.github/agents/_build-description.json` — a local,
operator-owned file. The entire `.github/agents/` tree is gitignored
(see `.gitignore` line 17 and adjacent comment) because the team's
internal knowledge of the module should not ship with the repo.

A consequence: a fresh clone has no descriptor. `--self` will fail
until one is created.

## Bootstrap

```bash
cp references/_self-build-description.template.json .github/agents/_build-description.json
python build_team.py --self --update --merge --yes
```

The template ships with `reference_db_path` and `style_reference`
set to `null`. `agentteams.analyze` infers sensible defaults
(`docs/`, `docs_src/`) when those fields are null AND
`doc_site_config_file` is set AND the directories exist on disk —
which is the case for agentteams itself. No manual placeholder
intervention should be required.

## Customising

Edit the copied descriptor freely; subsequent `--update --merge`
runs propagate the changes into the generated team files. The
template represents agentteams' own canonical configuration as of
the last refresh; treat it as a starting point.
