# t — zařaď úkol do správného Trello listu jednou větou

```bash
t add "domluvit konzultaci k projektu, do pátku"
```

`t` načte strukturu tvého Trella, nechá klasifikátor rozhodnout
(board → list, labely) podle skutečných názvů/ID, deterministicky zpracuje
termín a vytvoří kartu přes Trello REST API. Při nízké jistotě karta padá do
listu **Inbox** s labelem `k roztřídění`.

## Instalace

```bash
uv sync
```

Příkazy pro vývoj: `scripts/ci.sh` (ruff + mypy --strict + pytest).

## První spuštění

Podrobně viz [SETUP.md](SETUP.md) (získání API klíče a tokenu).

```bash
t init                              # průvodce: přihlášení, boardy, Inbox/label
t sync                              # stažení struktury Trella do cache
t add "úkol, do pátku" --dry-run    # jen ukáže rozhodnutí, nic nevytvoří
t add "úkol, do pátku"              # náhled + potvrzení + vytvoření karty
t doctor                            # kontrola configu, tokenu a cache
t log                               # posledních N rozhodnutí
```

Užitečné přepínače `t add`: `--yes` (bez potvrzení), `--board <název>`
(omezí schéma na jeden board), `--classifier mock|jev`.

## Jak to funguje

1. `t sync` uloží boardy, listy a labely do lokální cache (TTL 1 h).
2. Z cache se postaví typované schéma (enumy = skutečné názvy/ID).
3. Klasifikátor (`mock` – pravidla/klíčová slova; `jev` – model Jev přes
   TypeSafe System One API: jeden fan-out dotaz s Choice na board, Choice na
   list a Noul na každý label) vrátí `Decision`; výstup se validuje proti
   schématu, neplatné ID je chyba, nikdy tichá oprava.
4. `due_phrase` (surový text termínu) převede `dates.py` na datum (cs + en,
   Europe/Prague). Model datum nikdy nepočítá.
5. Karta vznikne přes `POST /1/cards`; rozhodnutí se zapíše do
   `decision_log.jsonl` pro pozdější měření přesnosti.

## Konfigurace

- Secrety: `TRELLO_KEY`, `TRELLO_TOKEN` (případně `JEV_API_KEY`) v prostředí
  nebo `.env`. Nikdy do gitu, nikdy do logů.
- Nastavení: `~/.config/t/config.toml` (práh `threshold` 0.6, názvy Inboxu,
  TTL cache, vybrané boardy). Cesty jdou přepsat přes `T_CONFIG_PATH`,
  `T_CACHE_PATH`, `T_LOG_PATH`.

## MCP server (pro AI agenty)

`t mcp` vystaví stejnou pipeline jako `t add` čtyřem nástrojům:
`add_task` (vytvoří kartu, bez potvrzení; při nízké jistotě míří do
Inboxu), `preview_task` (dry run), `list_targets` (boardy/listy/labely),
`list_inbox` (karty čekající na triage). Server umí **jen vytvářet
karty** – žádné mazání, přesuny ani editace.

> Pozor: server běží s tvým Trello tokenem a `add_task` opravdu
> vytváří karty ve tvém workspace. Volání jsou throttlovaná
> (`mcp_add_per_hour`, výchozě 30/h) a v logu označená `source="mcp"`.

Secrety si server bere ze stejného prostředí/configu jako CLI; pokud
jsi při `t init` uložil `.env` v repozitáři, není potřeba nic dalšího.
Jinak je předej přes `env`/`--env` (níže).

**Claude Code** (ověřená syntaxe z oficiální dokumentace):

```bash
claude mcp add --transport stdio t -- /cesta/k/trello-jev/.venv/bin/t mcp
```

se secrety předanými explicitně:

```bash
claude mcp add --transport stdio t \
  --env TRELLO_KEY=... --env TRELLO_TOKEN=... --env JEV_API_KEY=... \
  -- /cesta/k/trello-jev/.venv/bin/t mcp
```

**Claude Desktop**: do `claude_desktop_config.json` (`~/Library/Application
Support/Claude/` na macOS, `%APPDATA%\Claude\` na Windows; pak restart
aplikace):

```json
{
  "mcpServers": {
    "t": {
      "command": "/cesta/k/trello-jev/.venv/bin/t",
      "args": ["mcp"]
    }
  }
}
```

**Antigravity**: stdio servery se registrují konfiguračním souborem
`~/.gemini/config/mcp_config.json` (globálně; pro workspace platí
`.agents/mcp_config.json`):

```json
{
  "mcpServers": {
    "t": {
      "command": "/cesta/k/trello-jev/.venv/bin/t",
      "args": ["mcp"]
    }
  }
}
```

(Příkaz `agy mcp add --url ...` je hlášený jen pro vzdálené HTTP servery
a z oficiální dokumentace se mi ho nepodařilo ověřit.)

**HTTP režim** (místo stdio): `t mcp --http --port 8000` poslouchá na
`127.0.0.1:8000/mcp`. Vazba na nelokální adresu (`--host 0.0.0.0`)
vyžaduje nastavené `T_MCP_TOKEN`, jinak server odmítne startovat.
