# PLAN: `t` – CLI, které zařadí úkol do správného Trello listu

Tento soubor je zadání pro coding agenta. Pracuj po milnících (M0–M5), po každém spusť testy a krátce shrň, co je hotové. Na konci proveď **závěrečný úkol** (sekce 10).

## 1. Cíl

Uživatel napíše jednu větu:

```
t add "domluvit konzultaci k projektu, do pátku"
```

CLI:
1. načte reálnou strukturu Trella (boardy, listy, labely),
2. z ní vygeneruje **typované schéma** (enumy = skutečné názvy/ID),
3. nechá klasifikační model (Jev, TypeSafe System One) vrátit rozhodnutí podle schématu,
4. deterministicky zpracuje datum,
5. vytvoří kartu přes Trello REST API (nebo při nízké jistotě pošle do Inboxu / zeptá se).

Rozsah v1: **pouze CLI**, žádné GUI, žádný server.

## 2. Pravidla pro agenta

- **Nevymýšlej API Jevu.** Endpoint, autentizaci ani formát požadavku neznám. Udělej `Classifier` rozhraní (viz 5), implementuj `MockClassifier` (pravidla/klíčová slova) a `JevClassifier` jako **kostru s TODO**. Jakmile dojdeš k M3, **zastav se a zeptej se mě** na dokumentaci Jevu (URL, příklad volání, autentizace, jak se předává schéma).
- Trello endpointy a flow pro získání tokenu **ověř v aktuální dokumentaci** (developer.atlassian.com/cloud/trello/rest/), nespoléhej na paměť.
- Žádné secrety v gitu. `.env` a config soubor v `.gitignore` od prvního commitu.
- Nepoužívej modely na parsování dat ("v pátek", "za 2 týdny"). To dělá knihovna.
- Piš striktně typovaně, žádné `Any`, kde jde tomu zabránit.

## 3. Stack (lze změnit, pokud to zdůvodníš)

- Python 3.11+, správa přes `uv`
- `typer` (CLI), `rich` (výstup), `httpx` (HTTP), `pydantic` v2 (schémata), `dateparser` (cs + en, timezone `Europe/Prague`)
- testy: `pytest`, `respx` (mock HTTP)
- lint/typy: `ruff`, `mypy --strict`

## 4. Struktura

```
t/
  cli.py            # typer příkazy
  config.py         # načítání configu a secretů
  trello/
    client.py       # tenký wrapper nad REST API
    models.py       # Board, TrelloList, Label
    cache.py        # lokální cache struktury (JSON, TTL)
  classify/
    base.py         # Classifier protokol + Decision model
    schema.py       # dynamické generování schématu z cache
    mock.py         # MockClassifier
    jev.py          # JevClassifier (kostra do M3)
  dates.py          # deterministické parsování termínů
  decision_log.py   # JSONL log rozhodnutí
tests/
SETUP.md
```

## 5. Datové typy

```python
class Decision(BaseModel):
    board_id: str          # jen z enumu existujících boardů
    list_id: str           # jen z listů daného boardu
    label_ids: list[str]   # jen z existujících labelů
    title: str             # krátký, očištěný od data
    description: str | None
    due_phrase: str | None # surový text termínu, NE datum
    confidence: float      # 0..1

class Classifier(Protocol):
    def decide(self, text: str, schema: StructureSchema) -> Decision: ...
```

`StructureSchema` se staví z cache při každém volání: seznam boardů → jejich listy → labely, s ID i čitelnými názvy. Validuj výstup klasifikátoru proti schématu; neplatné ID = chyba, ne tichá oprava.

Poznámka: `due_phrase` je text, `dates.py` z něj udělá `datetime` vzhledem k aktuálnímu času. Model datum nepočítá.

## 6. Příkazy

| Příkaz | Co dělá |
|---|---|
| `t init` | interaktivní průvodce: zadání API key + token, test spojení, výběr boardů, které se mají používat |
| `t sync` | znovu stáhne strukturu Trella do cache |
| `t add "text"` | hlavní příkaz (viz 7) |
| `t doctor` | ověří config, platnost tokenu, dostupnost Jevu, stáří cache |
| `t log` | vypíše posledních N rozhodnutí |

Společné přepínače pro `add`: `--dry-run` (nic nevytvoří, jen ukáže rozhodnutí), `--yes` (bez potvrzení), `--board <název>` (omezí schéma na jeden board), `--classifier mock|jev`.

## 7. Chování `t add`

1. Načti cache (pokud starší než TTL, např. 1 h, udělej `sync`).
2. Sestav schéma, zavolej `Classifier.decide`.
3. Validuj výstup proti schématu.
4. Zpracuj `due_phrase` přes `dates.py`; při neúspěchu karta vznikne bez termínu a CLI to řekne.
5. Pokud `confidence < práh` (config, default 0.6): karta jde do listu **Inbox** (název konfigurovatelný) s labelem `k roztřídění`. Pokud list/label neexistuje, `t init` nabídne jeho vytvoření.
6. Interaktivně ukaž náhled (board → list, labely, termín) a potvrzení, pokud není `--yes`.
7. `POST /1/cards`, vypiš URL karty.
8. Zapiš řádek do `decision_log.jsonl`: vstupní text, rozhodnutí, confidence, ID vytvořené karty, čas.

Cíl logu: později porovnat rozhodnutí modelu s ručními přesuny karet a změřit přesnost.

## 8. Konfigurace a secrety

- Secrety v env proměnných `TRELLO_KEY`, `TRELLO_TOKEN`, `JEV_API_KEY` (případně `.env` načtený přes `python-dotenv`). Nikdy je nevypisuj do logů ani chybových hlášek.
- Neidentifikující nastavení v `~/.config/t/config.toml` (práh, název Inboxu, TTL, vybrané boardy).
- Trello auth přes query parametry `key` a `token`; wrapper je přidává centrálně, ať se nikde neloguje celá URL.
- Ošetři rate limity (HTTP 429): retry s exponenciálním backoffem, max 3 pokusy.

## 9. Milníky

| # | Obsah | Hotovo, když |
|---|---|---|
| M0 | projekt, `uv`, ruff, mypy, CI skript, `.gitignore` | `pytest` a `mypy --strict` prochází na prázdném projektu |
| M1 | Trello klient + cache + `t sync`, `t doctor` (Trello část) | testy s `respx` pokrývají boardy, listy, labely, 401 a 429 |
| M2 | dynamické schéma + `MockClassifier` + `t add --dry-run` | z reálné cache se vygeneruje schéma, mock vrací validní `Decision` |
| M3 | **STOP, zeptej se na dokumentaci Jevu**, pak `JevClassifier` | `t add --classifier jev --dry-run` vrací validní `Decision`, neplatný výstup se odmítne |
| M4 | `dates.py`, vytváření karet, Inbox fallback, potvrzení, decision log | testy na datumy ("v pátek", "za dva týdny", "zítra v 15:00", bez data) a na práh confidence |
| M5 | `t init`, `SETUP.md`, README | nový uživatel dojde od nuly k první kartě podle SETUP.md |

Mimo rozsah v1 (jen zaznamenej do `ROADMAP.md`): rozdělení vstupu na více karet, detekce duplicit, týdenní re-triage, napojení přes MCP/Antigravity jako nástroj, vyhodnocení přesnosti z logu.

## 10. Závěrečný úkol: instrukce pro propojení s Trello API

Po dokončení M5 mi **jako poslední krok** napiš krok za krokem návod (česky, stručně, s přesnými URL), jak se nástroj propojí s Trellem. Vytvoř ho i jako `SETUP.md` a použij v `t init`. Před psaním **ověř aktuální postup v dokumentaci Trella**, protože UI se mění. Návod musí pokrývat:

1. **Vytvoření API klíče.** Vytvoření Power-Upu v admin konzoli (trello.com/power-ups/admin), kde se generuje API key (a secret, který pro tento nástroj nepotřebuju). Uveď, co se vyplňuje za povinná pole.
2. **Vygenerování tokenu.** Autorizační URL ve tvaru `https://trello.com/1/authorize?expiration=...&scope=read,write&response_type=token&name=...&key=<API_KEY>`. Vysvětli volbu `expiration` (30 dní vs. `never`) a proč stačí scope `read,write`.
3. **Uložení secretů.** Jak nastavit `TRELLO_KEY` a `TRELLO_TOKEN` (shell profil / `.env`), jak zkontrolovat, že `.env` není v gitu, a jak token odvolat, pokud unikne.
4. **Ověření.** Příkaz `t doctor` a ruční test (`curl "https://api.trello.com/1/members/me?key=...&token=..."`) s popisem očekávané odpovědi.
5. **Časté chyby.** `401 invalid key`, `invalid token`, token bez write scope, vypršelý token, `429` rate limit.
6. **Příprava workspace.** Doporučená struktura: board(y), list `Inbox`, label `k roztřídění`, a jak to nastavit ručně nebo přes `t init`.

Až to bude hotové, nahlas: co funguje, co je stub (zejména Jev, pokud jsem ještě nedodal dokumentaci), a které předpoklady jsi udělal.
