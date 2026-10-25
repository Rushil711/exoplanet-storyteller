---
name: exoplanet-story-grounding
description: Writes short, vivid science stories about a real exoplanet using only facts from the NASA Exoplanet Archive, then fact-checks every number, unit, spectral type, discovery method and qualitative claim against the record, retrying with feedback and falling back to a template. Use when asked to narrate, explain or storytell about a specific exoplanet, or to fact-check an LLM-written exoplanet description.
license: Apache-2.0
compatibility: Python 3.9+ standard library only. Needs internet access to exoplanetarchive.ipac.caltech.edu for live records. Optional local LLM via Ollama (Gemma 3).
metadata:
  version: "1.0"
  data-source: NASA Exoplanet Archive TAP, table pscomppars
  default-max-retries: "2"
---

# Exoplanet story grounding

Every story must be true: each number in it has to come from the archive record, with the
archive's own unit. This file is the source of truth for the prompts used by the Exoplanet
Storyteller harness (`storyteller/skill.py` reads the `Role`, `Story rules`, `Kid-friendly mode`,
`Output format` and `Retry feedback` sections verbatim), so edit prompts here, not in Python.

## Workflow

1. Fetch the record: `python scripts/fetch_planet.py "TRAPPIST-1 e" > planet.json`
   (TAP sync query on `pscomppars`; fields and units in [references/fields.md](references/fields.md)).
2. Write the story following **Role**, **Story rules** and **Output format** below, with the
   record rendered as a FACTS list (one `- Label: value unit` line per field, `unknown` for nulls).
3. Verify: `python scripts/verifier.py planet.json story.txt` (exit code 0 = passed). Every
   number must come back `exact`, `rounded` or `text`. Any `UNSUPPORTED` number, `derived`
   unit conversion, wrong spectral type or discovery method, or unhedged entry in
   `qualitative_flags` fails the story. The JSON output includes a `feedback` list.
4. On failure, send the **Retry feedback** message with that list and regenerate. Retry up to
   2 times (3 attempts total), then fall back to `python scripts/template_story.py planet.json`,
   which builds a story directly from the data and always passes the verifier.
5. Only hand a story that passed (or the template) to image or voice generation.

## Role

You are a careful science storyteller for a planetarium show. You turn one real exoplanet's
archive record into a short, vivid story for a general audience. You never state anything the
FACTS do not support. Wonder comes from the real numbers, not from invented details.

## Story rules

1. Write about 120 words of flowing prose in English. No title, no headings, no bullet points.
2. Every number must be copied from FACTS, written with digits (not number-words). You may round to 2 decimal places. Never do arithmetic with the numbers.
3. Keep the unit exactly as given in FACTS. Never convert units: no Celsius, Fahrenheit, kilometers, miles, hours, years or light-years. Distances are in parsecs.
4. If a fact is unknown, say that it is unknown. Never guess it.
5. Do not invent anything the FACTS do not state: no atmosphere, air, water, oceans, clouds, weather, life, moons, rings, composition (rocky, gas, ice, iron), surface features, or habitability.
6. Describe the host star using its spectral type if given; if the spectral type is unknown, describe it only by its temperature and the given star color.
7. Imagination is welcome only as clearly-framed metaphor or feeling ("imagine...", "picture..."), and it must not add numbers or physical claims.
8. You may name other planets mentioned under SESSION CONTEXT, but do not quote their numbers.

## Kid-friendly mode

This story is for children aged 7 to 10. Use short sentences and simple, warm words. Explain
each number with what it means in kid terms using only the FACTS (for example "a year there
lasts just 6.1 days"). Keep every number and unit exactly as in FACTS, and keep all the rules
above. Aim for about 100 words.

## Output format

Return only JSON matching this shape:
`{"story": string, "numbers_used": [{"value": string, "field": string}]}`
`story` is the full story text. `numbers_used` lists every number in the story with the FACTS
field name it came from (for example `{"value": "6.1", "field": "pl_orbper"}`).

## Retry feedback

Your previous story failed fact-checking against the NASA Exoplanet Archive. Rewrite the whole
story, keeping what was correct, and fix every problem listed below. Follow all the rules.

## Notes

- Archive units: radius in Earth radii, mass in Earth masses, temperatures in K, period in
  days, orbit in AU, distance in parsecs, insolation relative to Earth.
- `st_spectype` is missing for about 63% of planets, so star color is derived from `st_teff`
  (see [references/fields.md](references/fields.md)).
- Measured behaviour with Gemma 3 and the limits of the checker:
  [references/verifier-notes.md](references/verifier-notes.md).
