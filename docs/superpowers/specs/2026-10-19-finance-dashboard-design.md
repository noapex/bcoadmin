# bcoadmin — multi-format import, categories, recurring payments and dashboard

Date: 2026-10-19
Status: draft, pending owner approval

## 1. Intent

The owner (single user, Santander Argentina customer) wants to answer three
questions from their bank statements:

1. **Overall balance** — how much comes in, how much goes out, month by month.
2. **Where the money goes** — spending by category, and how each category
   evolves over time, with charts.
3. **Recurring payments** — which payments repeat (utilities, taxes,
   insurance, subscriptions) and, above all, **whether one was skipped**
   in the current period. The owner cares about *all* recurring payments,
   both automatic debits and manually paid services.

This lives inside the existing Django project `bcoadmin`, and the owner
explicitly welcomes improving it along the way.

## 2. Current state (summary)

- Django + SQLite, app `movimientos`. `Detalle` holds **5,072 account movements
  (2016-09 → 2026-03)** that must be preserved.
- Only parser: `movimientos/xls_parser.py`, for the legacy account `.xls`
  export. No card or PDF support.
- Categorisation uses the hardcoded `settings.CATEGORIAS_GASTOS` dict in the
  gitignored `local_settings.py`, matched with an unescaped `.*{s}.*` regex.
  The MPTT `Categoria` model exists (5 rows) but no view uses it.
- `balance()` in `views.py` has bugs: months keyed only by `fecha.month`
  (different years merge), card totals overwritten instead of summed,
  stale variable in subcategory matching, NameError on empty data.
- No charts are loaded (leftover Chartist demo code), no tests, every view is
  public (`login_required` imported but unused).
- Frontend: Materialize 0.100.2 + jQuery from CDN, tablesorter.
- `get_movs.py` is a Selenium scraper for "últimos 60 días" (`/sync/`).
- Uncommitted local diff exists (commented-out `LC_NUMERIC` locale, debug
  prints, typos, forms.py multi-upload fix). The builder must review it before
  starting and keep the valid parts (multi-upload fix, `.xlsx` accept,
  `DEFAULT_AUTO_FIELD`).

## 3. Goals

### 3.1 Import any of these formats (auto-detected)

Sample files live in the owner's `~/Downloads` (see §9). All are Santander AR.

| Format | Recognise by | Content |
|---|---|---|
| **A. Card statement `.xlsx`** ("Último resumen - Visa 5544.xlsx", "… American Express 4623.xlsx") | A1–A3: "Movimientos del resumen", "Tarjeta … terminada en NNNN" | Header: closing/due dates, totals. Sections: "Pago de tarjeta y devoluciones", "Tarjeta de <holder> - <card>" (one per additional card possible), "Otros conceptos" (stamp tax, VAT, perceptions — only description + ARS amount). Columns: Fecha, Descripción, Cuotas ("6 de 9" or empty), Comprobante, Monto en pesos, Monto en dólares. Amounts are strings like `$159.654,11`, `U$S20,00`, `$-31.041,94`. **Fecha is empty on rows that share the previous row's date** (carry it forward). |
| **B. Account movements `.xlsx`** ("movimientos (3).xlsx") | Sheet `UltimosMovimientos`, row "Cuenta \| Cuenta única <account>", header row "Fecha, Sucursal origen, Descripción, Referencia, Caja de Ahorro, Cuenta Corriente, Saldo" | Data starts in column B. Amount is in either *Caja de Ahorro* or *Cuenta Corriente* column (dot decimal, e.g. `-82212.63`). Descripción is `"<type> \t <detail>"`. Header row "Moneda" says Pesos/Dólares. |
| **C. Monthly account PDF** ("YYYY-MM-DD_<account-number>.pdf", 24 files 2024-10 → 2026-09) | Text "Mi resumen de cuenta", "Período de movimientos" | Contains: (1) "Movimientos en pesos" — Fecha, Comprobante, Movimiento, Caja de Ahorro, Cuenta Corriente, Saldo; each movement spans 2 lines (type + detail), **date sometimes printed on the detail line instead of the first line**, comprobante sometimes missing; (2) "Movimientos en dólares"; (3) an embedded **Visa credit-card statement** ("Tarjeta Santander", "Total consumos de … Tarjeta terminada en 5544", columns Fecha, Comprobante, Descripción, Cuota, Pesos, Dólares); (4) "Tarjeta de débito" and services sections that **duplicate** account movements (must not be imported again); (5) legal/marketing pages. Text extraction with plain `extract_text()` is ambiguous; use word coordinates (pdfplumber `extract_words`) to assign columns. |
| **D. Legacy account `.xls`** | current `xls_parser.py` behaviour | Keep working (files in `media/`). |

Requirements:

- One upload page accepting many files of mixed formats at once.
- Per-file result report: detected format, account/card, period, rows new /
  duplicate / skipped, and errors. A broken file never aborts the others and
  never crashes the page (fixes current `UnboundLocalError`).
- A file whose format is not recognised is rejected with a clear message,
  never half-imported.
- Re-importing the same file, or importing overlapping periods in different
  formats (B vs C, A vs C's embedded Visa, new files vs the 5,072 legacy rows),
  **must not create duplicates**. This is the hardest part; see §5.3.
- Every movement keeps a link to the file it came from (reuse `DataFile`).

### 3.2 Categorise

- Categories are the existing MPTT `Categoria` tree, editable in the admin.
- Rules are editable in the admin (see §5.2) and seeded from the current
  `CATEGORIAS_GASTOS`, `TARJETAS`, `IGNORAR` in `local_settings.py`.
- A movement can be manually re-categorised; a manual choice always wins over
  rules and survives re-running rules.
- Changing rules re-categorises all non-manual movements (admin action or
  automatic on save).
- Uncategorised movements are visible as "Sin categoría" so the owner can
  write rules for them.

### 3.3 No double counting — movement kinds

Each movement has a **kind**: `ingreso`, `gasto`, or `interno`.
`interno` movements are excluded from income/expense totals but still listed.

`interno` covers at least:

- Account-side credit-card payments ("Pago de tarjeta de credito", "Pago
  tarjeta de credito amex", matched by `TARJETAS` codes) — the real expenses
  are the card's consumptions.
- Card-side payments ("Su pago en pesos", "Su pago en usd").
- Buying/selling dollars ("Debito por compra de dolares" in ARS and the
  matching USD credit).
- Transfers between the owner's own accounts (e.g. "De <owner surname>/<owner name>").
- Investments in `IGNORAR` (FCI subscriptions, LEBACs, bonds).

Kind is decided by rules (same rule engine as categories) with a sensible
default from sign (positive → ingreso, negative → gasto). Card refunds
(negative consumptions, e.g. "Dlo*duvet home $-31.041,94") are negative
`gasto`, not income.

### 3.4 Currencies separate

ARS and USD are never mixed or converted. Every total, chart and recurring
item is per currency; the dashboard has a Pesos / Dólares toggle.

### 3.5 Recurring payments

- **Automatic detection** after each import: group movements by a
  *normalized payee key* (description stripped of variable parts — dates,
  receipt numbers, "tarj nro. NNNN", amounts in the detail — but **keeping
  contract/customer numbers**, so the 3 Federación Patronal policies, or two
  utilities of the same provider, are separate recurring items).
- A group is a candidate when it appears in ≥ 3 distinct periods within the
  recent history. Infer **frequency** (monthly, bimonthly, quarterly, yearly)
  and the **usual day of month**, and a reference amount (last amount).
- Card consumptions count too (Netflix, Spotify, AWS, Anthropic, HBO…).
  Purchases at the same generic merchant (e.g. Mercado Libre) will create
  false positives; that is acceptable because of the admin controls below.
- **Status per current period**: `pagado` (seen in this period), `pendiente`
  (not seen yet, usual day + tolerance not reached), `falta` (not seen and
  usual day + tolerance passed), `atrasado` (seen but after tolerance).
- **Admin controls**: confirm a suggestion, rename it, silence it (e.g.
  cancelled on purpose), or add one manually. Silenced items never alert.
  Detection must not overwrite owner edits.

### 3.6 Dashboard — chosen layout "A: everything on one page"

Owner picked a single page (mockup: `.superpowers/brainstorm/…/layout.html`):

- Top: Pesos / Dólares toggle, period range selector (default last 12 months).
- KPI row: income, expenses, balance for the latest month; count of
  recurring items in `falta`.
- Charts (Chart.js from CDN):
  - Monthly income vs expenses (grouped bars) with balance.
  - Spending by category for the selected month (doughnut).
  - Category evolution over the range (lines, top N categories + "Otros").
- Recurring panel: each item with status icon (✅ pagado / ⏳ pendiente /
  ❌ falta / ⚠ atrasado), last amount, last date.
- Drill-down: clicking a month, a category slice or a recurring item opens
  the filtered movement list (reuse/improve the existing month archive view).
- UI copy in Spanish (the existing project's language).

### 3.7 Housekeeping included

- `login_required` on every view.
- Replace the buggy `balance()` aggregation; old pages (balance / tarjetas /
  categorias) may be removed or redirected to the new dashboard.
- Tests (see §7).

## 4. Non-goals

- Email / Pushover / any notification of missing payments.
- Alerts on price increases of recurring payments.
- Currency conversion, budgets, forecasts, multi-user.
- Changing the Selenium `/sync/` scraper beyond keeping it working with the
  new import pipeline.
- Moving secrets out of `get_movs.py` / `local_settings.py` (see §8).

## 5. Design decisions

### 5.1 Data model

Extend `Detalle` (keeps the 5,072 rows; migration back-fills defaults) rather
than creating a parallel model. Owner's call is "preserve data"; the extend vs.
new-model choice is mine and the builder may change it if a migration is
cleaner, as long as no data is lost.

Fields to add (names are the builder's choice):

- `fuente` — `cuenta` or a card (`visa`, `amex`, `master`), plus card last 4.
- `moneda` — `ARS` / `USD`.
- `tipo` — `ingreso` / `gasto` / `interno` (§3.3).
- `categoria` FK → `Categoria` (nullable) and `categoria_manual` bool.
- `fecha_compra` (cards: original purchase date) and `cuota` text ("6 de 9").
- `comercio` — normalized payee key (§3.5).
- `huella` — dedup fingerprint (§5.3), indexed.
- `archivo` FK → `DataFile` (nullable for legacy rows).

**Card installments**: `fecha` = statement closing date month (the month the
owner actually pays it); `fecha_compra` = original purchase date. Rationale:
"how my expenses evolve" should reflect when money is spent, not inflate the
original purchase month. *My call.*

Remove dead models `MovimientoIgnorado` and `Tarjeta` only if replaced by the
rule model; otherwise leave them.

### 5.2 Rules

New model `Regla`: pattern, match type (`contiene` case-insensitive, or
`regex`), optional `fuente`/`moneda` filter, priority, target `categoria`,
optional target `tipo`. First matching rule by priority wins. Seed migration
or management command imports the current `local_settings` dicts.

### 5.3 Deduplication

Hard requirement: importing the same real movement from any two formats yields
one row. Known traps found in the sample files:

- Receipt numbers differ in zero padding between formats (`000011839` vs
  `11839`) and are **missing** in some PDF rows.
- PDF rows sometimes carry the date on the detail line.
- Descriptions differ between formats (xlsx: `"Pago de servicios \t Edesur:
  0001928991"`; PDF: type and detail on separate lines; legacy xls: uppercase,
  different wording).
- Same-day identical movements are real (3 Federación Patronal debits of
  $15.337 on the same day differ only by receipt/contract number).
- Legacy dedup used ±2 days tolerance between formats.

Suggested fingerprint: `(fuente, moneda, fecha, monto, normalized receipt)`,
with fallback to `(fuente, moneda, fecha, monto, normalized description)` when
the receipt is missing, plus a ±2-day tolerant match against existing rows.
The exact algorithm is the **builder's call**, validated by the tests in §7.

A PDF's "Tarjeta de débito" and services sections are never imported (they
repeat account movements).

### 5.4 Parsers

One module per format under e.g. `movimientos/parsers/`, a common output
(a list of plain dicts/dataclass, not pandas-specific), and a `detect(file)`
that returns the parser or "unknown". Keep `pandas` only if useful.
New dependency: `pdfplumber`. Do not depend on the `es_AR` locale for number
parsing; parse `1.234,56` / `-1234.56` / `$-1.234,56` / `U$S20,00` explicitly.

### 5.5 Stack

- Pin dependencies in `requirements.txt` (Django currently unpinned).
  Target: latest Django LTS that runs on the Dockerfile's Python; bumping the
  Dockerfile Python version is allowed. *Builder's call.*
- Charts: Chart.js 4 from cdnjs/jsDelivr, exact version pinned.
- Keep Materialize for consistency unless the builder has a strong reason.

### 5.6 Resolved during builder review (my calls)

- **Card payments without card detail.** Card detail exists only for part
  of the history (embedded Visa in PDFs 2024-10 → 2026-09, one Amex cycle;
  legacy DB has ~152 card-payment rows since 2016). Rule: an account-side card
  payment is `gasto` (category "Tarjeta sin detalle") **unless** consumptions
  for that card covering that payment's cycle have been imported, in which
  case it is `interno`. Re-evaluated after every import, so importing a card
  statement later flips the matching payment to `interno` automatically.
  Cards are linked by the `TARJETAS` codes (account side) and card last-4 /
  name (card side); the builder maps them in config/admin.
- **Legacy rows currency.** The legacy `.xls` export is the pesos account.
  The 65 rows mentioning `usd` / `dolar` are the ARS leg of transfers or FX
  between the owner's own accounts → back-fill all legacy rows as `ARS`, and
  those rows become `interno` via rules.
- **Recurring "today" and "period".** Status is judged against the **latest
  imported date of the source the item comes from** (account, or each card's
  latest closing date), not the calendar — so a not-yet-imported statement
  never produces `falta`. Each item uses its own expected window derived from
  its frequency (monthly item → next month; bimonthly → +2 months; etc.),
  not the calendar month.
- **Additional cardholders.** All holders' consumptions count toward the
  owner's totals; store the holder name on the movement so it can be filtered.
  Samples only contain the titular.

## 6. Anti-goals (would be failure even if it "works")

- Any double-counted money (card payment + card consumptions, dollar purchase
  as expense, the same movement imported from PDF and xlsx).
- Silently dropping movements a file contains (every skipped row must be
  counted in the import report).
- Losing or corrupting the existing 5,072 rows.
- Mixing ARS and USD in one total.
- Detection overwriting owner edits to categories or recurring items.

## 7. Verification

- Parser tests per format using the real sample files. Those files contain
  personal data (name, CUIL, CBU, address, counterparties) — tests must not
  commit them raw: either trimmed **anonymized** fixtures committed to the
  repo, or tests that read the originals from a path given by an env var and
  skip when absent. *My call: anonymized fixtures for unit tests + optional
  env-var integration test over all 24 PDFs.*
- Parser assertions: row counts and totals match the statement's own totals
  (card "Total de … terminada en NNNN", PDF "Saldo total", running balance in
  B/C where present).
- Dedup tests: import the same file twice; import B and C covering the same
  dates; import A and C's embedded Visa for the same cycle → no duplicates.
- Rule and kind tests (card payment is `interno`, refund is negative `gasto`).
- Recurring detection tests on synthetic series: monthly, bimonthly,
  same-provider-different-contract, missed month → `falta`.
- Manual check: run the server, import all sample files, dashboard renders
  with data in both currencies.

## 8. Notes for the owner (out of scope, flagged)

- `movimientos/get_movs.py` has a Pushover token and user key **committed**.
- `local_settings.py` holds the bank password in plaintext (gitignored).
- `SECRET_KEY` hardcoded and `DEBUG=True`.

## 9. Sample files (owner's machine)

`~/Downloads/`: `Último resumen - American Express 4623.xlsx`,
`Último resumen - Visa 5544.xlsx`, `movimientos (3).xlsx`,
`movimientos (4).xlsx`, and 24 monthly PDFs
`YYYY-MM-DD_<account-number>*.pdf` (2024-10-24 … 2026-09-24).

## 10. Left to the builder

Field names, parser internals, exact dedup algorithm (subject to §7 tests),
recurring-detection thresholds/tolerances, page structure/JS, whether old
pages are deleted or redirected, Django/Python versions.
