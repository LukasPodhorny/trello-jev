# ROADMAP (mimo rozsah v1)

Zaznamenáno podle PLAN.md §9 – záměrně neimplementováno ve v1:

- Rozdělení jednoho vstupu na více karet.
- Detekce duplicit (nek vytvářet kartu, která už existuje).
- Týdenní re-triage (pravidelná revize Inboxu).
- Napojení přes MCP / Antigravity jako nástroj.
- Vyhodnocení přesnosti klasifikátoru z `decision_log.jsonl`
  (porovnání rozhodnutí modelu s ručními přesuny karet).

## Jev date engine (mimo rozsah změny date extraction)

- Relativní offsety ("za dva týdny", "za 3 dny") – kuchařka je neumí;
  potřeba nové otázky (offset_num/offset_unit) nebo detekce v kódu.
- Týdenní granularita ("příští týden", "end of week") – žádný mode ji
  nevyjadřuje; eval case v `examples/eval.jsonl` zatím neprochází.
- Denní doba ("v 15:00", "ráno") – kuchařka navrhuje otázky
  hour/minute/am-pm stejného tvaru.
- "End of month" ("konec měsíce") – kalendářní výpočet v kódu, model jen
  ohlásí tvar.

## MCP server (mimo rozsah)

- Hlasové ovládání (voice) – diktování úkolů mimo textové klienty.
- Chat bot – konverzační vrstva nad nástroji (potvrzování, dotazy).
