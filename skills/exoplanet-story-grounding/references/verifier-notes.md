# What the verifier checks, and what it does not

## Checks

- **Numbers**: digits (`1,958`, `3 million`), compound number-words (`one hundred sixty-three`,
  `twenty-seven`), and `... and a half`. Each is matched against the record, unit-aware:
  the words right after a number decide which fields it may belong to (`days` means
  `pl_orbper`, `parsecs` means `sy_dist`, `K` means `pl_eqt`, or `st_teff` only when the
  sentence talks about the star).
- **Unit conversions** (K to °C/°F, parsecs to light-years, days to hours, AU to km ...) are
  recognised as `derived`. They are numerically right but fail the story, because measured
  Gemma failures were mostly conversion slips.
- **Spectral class tokens** (`G8`, `M8.0 V`) against `st_spectype`, including compound
  types such as `K7/M0 V`.
- **Discovery method words** (`transit`, `wobble`, `radial velocity`, `microlensing` ...)
  against `discoverymethod`.
- **Qualitative keywords** with no supporting field (atmosphere, water, oceans, life, moons,
  rings, rocky, gas giant, habitable ...). They pass as `hedged` only when the same clause says
  the thing is unknown ("whether it has an atmosphere, no one knows").
- Planet, host, facility and spectral-type strings are masked first, so the `1` in
  `TRAPPIST-1` is never treated as a claim.

## Does not check

- Qualitative claims outside the keyword list (e.g. "a purple sky"), comparisons ("bigger
  than Neptune"), or physical plausibility.
- Whether a bare number with no unit is attached to the right noun: bare small integers can
  pass by matching any field through rounding.
- Whether every `unknown` field is described as unknown (the story may simply skip it).
- Grammar, tone or length.

## Measured results with Gemma 3 (team prototype, CPU, 6 planets x 3 seeds)

| model | prompt | numeric pass | strict + no qualitative flags | unsupported numbers | mean wall s |
|---|---|---|---|---|---|
| gemma3:1b | basic | 7/18 (39%) | 1/18 (6%) | 11/127 (8.7%) | 5.0 |
| gemma3:1b | strict + JSON | 16/18 (89%) | 4/18 (22%) | 2/149 (1.3%) | 9.4 |
| gemma3:4b | basic | 10/18 (56%) | 2/18 (11%) | 10/155 (6.5%) | 11.7 |
| gemma3:4b | strict + JSON | 17/18 (94%) | 14/18 (78%) | 1/185 (0.5%) | 25.3 |

Typical failures: parsecs written as light-years, Celsius conversions, invented qualitative
claims (atmosphere, rocky, life), and number-word arithmetic ("sixteen" for 163.8,
"twenty-seven times" for 2657.8).
