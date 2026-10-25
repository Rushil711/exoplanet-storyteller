# Working on Exoplanet Storyteller

This is a hackathon app: a planetarium desk that tells a short story about a real exoplanet and
shows its work. The rule is that every number in a story comes from the NASA Exoplanet Archive.

## Layout

- `skills/exoplanet-story-grounding/` is an Agent Skill (agentskills.io). `SKILL.md` is the
  source of truth for the story prompt, the kid-mode rules, and the retry instructions.
  Edit prompts there. Do not copy them into Python.
- `skills/exoplanet-story-grounding/scripts/verifier.py` is the fact checker. It is pure
  Python and is what the harness calls.
- `storyteller/` is the harness: archive, safe query builder, model backends, the retry loop,
  image and voice, session memory.
- `static/` is the page. No build step. No second component library.
- `data/` is the bundled catalog snapshot and the recorded Gemma stories used offline.

## Conventions

- The model never writes ADQL. A request becomes a `SearchIntent` (a closed schema) and
  `query.build_adql` is the only function that produces a query.
- The archive's units are the story's units. No Celsius, no light-years, no kilometers in a story.
  Say "unknown" for null fields. Do not invent atmospheres, water, moons, or composition.
- `XAI_API_KEY` is read from the environment on the server. It must not appear in the frontend,
  in git, in cached media, or in logs. `storyteller/logs.py` redacts it.
- New model behavior goes behind `storyteller/llm.py`. Tests use `tests/conftest.py:FakeLLM`.
- A change to the verifier needs a test, including a sentence that should pass and a planted
  mistake that should fail. `tests/test_verifier.py` walks the whole catalog's template stories.

## Run

```bash
make install
make test
make run          # http://127.0.0.1:8741
```

`LLM_BACKEND=offline APP_MOCK_MEDIA=1` runs with no Ollama and no xAI key.
