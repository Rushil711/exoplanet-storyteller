# Archive fields used by the skill

All values come from the NASA Exoplanet Archive table `pscomppars` (one row per confirmed
planet, ~6,451 rows), queried through TAP:
`https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=<ADQL>&format=json`.

| field | label | unit (as stored, never converted) |
|---|---|---|
| pl_name | Planet name | |
| hostname | Host star | |
| pl_rade | Planet radius | Earth radii |
| pl_bmasse | Planet mass | Earth masses |
| pl_eqt | Planet equilibrium temperature | K |
| pl_orbper | Orbital period | days |
| pl_orbsmax | Orbit semi-major axis | AU |
| pl_insol | Starlight received (insolation) | times the starlight Earth receives |
| st_teff | Host star effective temperature | K |
| st_spectype | Host star spectral type | |
| st_rad | Host star radius | solar radii |
| sy_dist | Distance from Earth | parsecs |
| sy_pnum | Known planets in the system | planets |
| disc_year | Discovery year | |
| discoverymethod | Discovery method | |
| disc_facility | Discovery facility | |

## Missing values (full catalog snapshot, 6,451 planets)

`st_spectype` is null for 4,041 planets (63%), `pl_insol` for 596, `pl_eqt` for 540,
`pl_orbsmax` for 495, `pl_orbper` for 354, `st_rad` for 332, `st_teff` for 307. Nulls are
rendered as `unknown` in FACTS and must be described as unknown in the story.

## Star color from temperature

Because the spectral type is usually missing, the FACTS block adds a derived
`star_color` from `st_teff` (coarse blackbody bins):

| st_teff (K) | color |
|---|---|
| < 3500 | deep red |
| 3500 - 3999 | red-orange |
| 4000 - 5199 | orange |
| 5200 - 5999 | yellow-white |
| 6000 - 7499 | white |
| 7500 - 9999 | blue-white |
| >= 10000 | blue |
