# Propojení `t` s Trellem (krok za krokem)

Nástroj `t` mluví s Trellem přes oficiální REST API
([dokumentace](https://developer.atlassian.com/cloud/trello/rest/),
[autorizace](https://developer.atlassian.com/cloud/trello/guides/rest-api/authorization/)).
Potřebuje dvě věci: **API klíč** (identifikuje aplikaci) a **token**
(oprávnění jednat tvým jménem). Postup ověřen v aktuální dokumentaci Trella.

## 1. Vytvoření API klíče

1. Přihlas se do Trella a otevři [Power-Ups admin konzoli](https://trello.com/power-ups/admin).
2. Klikni na **New Power-Up** (nestavíš skutečný Power-Up, konzole slouží jen jako výdejna klíčů).
3. Vyplň povinná pole:
   - **Name** – cokoliv, např. `t-cli`,
   - **Workspace** – vyber svůj workspace.
4. Otevři detail Power-Upu, sekce **API key**, a klikni na **Generate a new API Key**.
5. Zkopíruj vygenerovaný klíč. Secret, který konzole ukazuje vedle, pro tento nástroj **nepotřebuješ**.

## 2. Vygenerování tokenu

Nejrychlejší cesta: na stránce API klíče klikni na odkaz **Token** vedle klíče,
potvrď přístup a zkopíruj token.

Ručně jde totéž autorizační URL (dosaď svůj klíč za `<API_KEY>`):

```
https://trello.com/1/authorize?expiration=never&scope=read,write&response_type=token&name=t-cli&key=<API_KEY>
```

- `expiration=never` – token nevyprší (pohodlné pro osobní CLI).
  Alternativa `expiration=30days` je bezpečnější, ale token musíš každý měsíc
  přegenerovat.
- `scope=read,write` – čtení struktury + vytváření karet. Víc nepotřebujeme
  (žádný `account` scope).
- Po otevření URL potvrď přístup a zkopírovaný token si ulož – stránka ho
  ukáže jen jednou.

## 3. Uložení secretů

Nástroj čte `TRELLO_KEY` a `TRELLO_TOKEN` z prostředí (případně ze souboru
`.env` v aktuálním adresáři). Pro klasifikátor `jev` přidej i `JEV_API_KEY`
(klíč získáš v [TypeSafe dashboardu](https://console.typesafe.ai/keys),
do API jde v hlavičce `Authorization: Bearer`).

Varianta A – `.env` (nejjednodušší, `t init` ho umí zapsat za tebe):

```bash
printf 'TRELLO_KEY=tvuj-klic\nTRELLO_TOKEN=tvuj-token\n' > .env
chmod 600 .env
```

Varianta B – shell profil (`~/.bashrc`, `~/.zshrc`):

```bash
export TRELLO_KEY="tvuj-klic"
export TRELLO_TOKEN="tvuj-token"
```

Kontrola, že `.env` není v gitu:

```bash
git check-ignore -v .env   # má vypsat cestu k .gitignore pravidlu
git status --short         # .env se nesmí objevit
```

Repozitář `t` má `.env` v `.gitignore` od prvního commitu, ale `.env` vytvořený
jinde (např. v home) si hlídej sám.

**Pokud token unikne** (commit, screenshot, cizí počítač):

1. Odvolej ho v [nastavení účtu](https://trello.com/u/my/account) → sekce
   **Applications** → **Revoke** u aplikace `t-cli` (alternativně API voláním
   `DELETE /1/tokens/<TOKEN>`), a
2. vygeneruj nový token podle kroku 2 a starý všude přepiš. Samotné smazání
   `.env` nestačí – uniklý token platí, dokud ho neodvoláš.

## 4. Ověření

```bash
t doctor
```

`doctor` zkontroluje config, přítomnost secretů (hodnoty nikdy nevypisuje),
platnost tokenu, čerstvost cache a stav Jev klasifikátoru.

Ruční test bez nástroje (očekávaná odpověď: JSON s tvým `id`, `username` a
`fullName`):

```bash
curl "https://api.trello.com/1/members/me?key=<API_KEY>&token=<TOKEN>"
```

## 5. Časté chyby

| Projev | Příčina / řešení |
|---|---|
| `401 invalid key` | špatný `TRELLO_KEY`; zkopíruj klíč znovu z admin konzole |
| `401 invalid token` / `unauthorized` | špatný nebo odvolaný `TRELLO_TOKEN`; vygeneruj nový (krok 2) |
| Token neumožní vytvořit kartu | token vznikl bez `write` scope; přegeneruj s `scope=read,write` |
| Fungovalo, teď `401` | token s `expiration=30days` vypršel; přegeneruj (nebo použij `never`) |
| `429` / `Rate limited` | překročen limit Trello API; `t` retryuje automaticky (max 3 pokusy), jinak chvíli počkej |
| `t add` hlásí chybějící Inbox/label | spusť `t init` a nech chybějící list/label vytvořit (krok 6) |

## 6. Příprava workspace

Doporučená struktura (na každém používaném boardu):

- list **`Inbox`** – sem padají úkoly s nízkou jistotou klasifikace,
- label **`k roztřídění`** – označí karty, které je třeba ručně přesunout
  (názvy jdou změnit v configu: `inbox_list_name`, `needs_sorting_label`).

Vytvoření ručně: na boardu **… → Create new list / Manage labels**.
Nebo automaticky: `t init` po výběru boardů chybějící list i label nabídne
k vytvoření. Nový uživatel jde celou cestu od nuly takto:

```bash
t init                              # klíč + token, výběr boardů, Inbox/label
t sync                              # stažení struktury do cache
t add "domluvit konzultaci k projektu, do pátku" --dry-run   # náhled rozhodnutí
t add "domluvit konzultaci k projektu, do pátku" --yes        # vytvoření karty
t log                               # historie rozhodnutí
```
