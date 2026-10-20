# Finance Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import Santander AR statements in 4 formats without duplicates, classify every movement (category + kind), detect recurring payments and their status, and show it all on a one-page dashboard with charts, per currency.

**Architecture:** Format-specific parsers (pure Python, no Django) emit a common `ParseResult`; an import service deduplicates and saves into the extended `Detalle` model; a classification service applies admin-editable rules plus a card-payment resolver; a recurring service detects and evaluates recurring items; pure aggregation functions feed one dashboard view rendered with Chart.js.

**Tech Stack:** Python 3.12, Django 5.2 LTS, django-mptt, openpyxl, xlrd + pandas (legacy `.xls` only), pdfplumber, Chart.js 4.4.1 (cdnjs), Materialize 0.100.2 (existing), SQLite.

**Spec:** `docs/superpowers/specs/2026-10-19-finance-dashboard-design.md`

## Global Constraints

- ARS and USD are never mixed or converted; every total, chart and recurring item is per currency.
- Sign convention for `Detalle.monto`: effect on the owner's money. Outflow negative, inflow positive. Card consumption `$159.654,11` → `-159654.11`; card refund `$-31.041,94` → `+31041.94`; card-side payment `$-165.101,84` → `+165101.84` (kind `interno`).
- `tipo` ∈ {`ingreso`, `gasto`, `interno`}; `interno` never counts in income/expense totals.
- Precedence when classifying: `categoria_manual=True` > card-payment resolver > first matching `Regla` by priority > default.
- Default kind: card sources → `gasto` regardless of sign; account source → `ingreso` if `monto > 0` else `gasto`.
- Never parse numbers via `locale`; use `parse_amount` (Task 2).
- No personal sample files committed. Real files are only read by integration tests via env var `BCOADMIN_SAMPLES_DIR`; tests skip when it is unset.
- Every view requires login (Django `LoginRequiredMiddleware`).
- UI copy in Spanish; code identifiers follow the existing Spanish domain names (`Detalle`, `Categoria`, `monto`, `fecha`…); comments in English.
- Detection and rule re-runs must never overwrite owner edits (`categoria_manual`, `Recurrente.estado_admin`, `Recurrente.nombre`, `Recurrente.manual`).
- Before Task 5's migration runs on the real DB: `cp db.sqlite3 db.sqlite3.pre-dashboard.bak`.

## Review Focus

1. Same movement imported from PDF and from `movimientos.xlsx` with different zero padding of the receipt (`000011839` vs `11839`) → one row. Test in Task 6.
2. Two genuinely distinct movements, same day, same amount, different receipt (3 × Federación Patronal $15.337 / $16.324) → kept separate; and two same-day same-amount rows with **no** receipt inside one file → both kept on first import, none added on re-import. Test in Task 6.
3. Card installment re-appearing next month with the same receipt but next cuota (`14 de 18` → `15 de 18`) → new row, not a duplicate. Test in Task 6.
4. PDF row whose date is printed on the detail line, and PDF row without comprobante → parsed with the right date, running balance consistent. Test in Task 4.
5. Uploading an unrecognised file (e.g. a `.docx`, an empty xlsx) together with valid ones → that file reported as rejected, others imported, page renders. Test in Task 7.

---

## File Structure

```
movimientos/
  parsers/
    __init__.py          # detect(path) -> parser callable | None; parse(path) -> ParseResult
    base.py              # ParsedMovement, ParsedStatement, ParseResult, parse_amount, parse_date, norm_ref, norm_cuota
    card_xlsx.py         # Format A
    account_xlsx.py      # Format B
    account_pdf.py       # Format C (account ARS + USD sections, embedded card section)
    legacy_xls.py        # Format D (adapter over old xls_parser logic)
  services/
    __init__.py
    importer.py          # import_files(paths) -> list[ImportReport]; dedup + persist
    classify.py          # clasificar(qs=None); resolver_pagos_tarjeta(); payee_key()
    recurring.py         # detectar_recurrentes(); estado(recurrente, ref_date)
    stats.py             # monthly_totals, spending_by_category, category_evolution
  management/commands/
    importar.py          # ./manage.py importar <paths...>
    seed_reglas.py       # ./manage.py seed_reglas
  views.py               # dashboard, movimientos_list, upload, sync (rewritten)
  forms.py               # MultipleFileField
  admin.py               # rewritten
  models.py              # Detalle extended, Regla, Resumen, Recurrente, Tarjeta reused
  tests/
    __init__.py, test_base.py, test_card_xlsx.py, test_account_xlsx.py,
    test_account_pdf.py, test_detect.py, test_importer.py, test_classify.py,
    test_recurring.py, test_stats.py, test_views.py, test_samples_integration.py
    factories.py         # helpers building synthetic xlsx files with openpyxl
templates/movimientos/
  dashboard.html         # rewritten (layout A)
  movimientos_list.html  # new
  upload.html            # replaces add_attachment*.html
```

Delete at the end (Task 12): `movimientos/tests.py`, `movimientos/xls_parser.py` (logic moved), `movimientos/xls_parser.py_bkp`, `templates/movimientos/{balance,tarjetas,categorias,categorias_back,add_attachment,add_attachment_done,add_attachment_error,detalle_archive_month}.html`, `movimientos/static/js/bcoadmin.js` (Chartist demo).

---

### Task 1: Baseline environment, settings, login

**Files:**
- Modify: `requirements.txt`, `Dockerfile`, `.gitignore`, `bcoadmin/settings.py`, `movimientos/forms.py`, `movimientos/views.py` (only revert debug prints/typos from the uncommitted diff)
- Create: `movimientos/tests/__init__.py`, `movimientos/tests/test_views.py`
- Delete: `movimientos/tests.py`

**Interfaces:**
- Produces: working `.venv`; test command `./.venv/bin/python manage.py test movimientos`; `MultipleFileField` in `movimientos/forms.py`.

- [ ] **Step 1: Review the uncommitted diff** (`git diff`). Keep: forms multi-upload intent, `.xlsx` accept in the upload template, `DEFAULT_AUTO_FIELD`, `ALLOWED_HOSTS` entries, openpyxl in requirements. Drop: added `print(...)` lines and the typos `inválidoo` / `iinválido`.
- [ ] **Step 2: Pin `requirements.txt`**: `Django>=5.2,<5.3`, `django-mptt>=0.16`, `django-extensions`, `django-debug-toolbar`, `pandas`, `numpy>=1.26`, `xlrd>=2`, `openpyxl>=3.1`, `pdfplumber>=0.11`, `selenium`. Dockerfile: `FROM python:3.12`. `.gitignore`: add `.superpowers/`, `db.sqlite3*`, `media/`.
- [ ] **Step 3: Create venv and install**: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`. Then `cp db.sqlite3 db.sqlite3.pre-dashboard.bak` and `.venv/bin/python manage.py migrate` — expected: no pending migrations, no errors.
- [ ] **Step 4: Write failing tests** in `movimientos/tests/test_views.py`:

```python
class LoginRequiredTests(TestCase):
    def test_dashboard_redirects_anonymous_to_login(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])

    def test_login_page_is_public(self):
        self.assertEqual(self.client.get("/accounts/login/").status_code, 200)
```

- [ ] **Step 5: Run** `.venv/bin/python manage.py test movimientos` → FAIL on the first test (200 instead of 302).
- [ ] **Step 6: Implement**: add `"django.contrib.auth.middleware.LoginRequiredMiddleware"` after `AuthenticationMiddleware` in `MIDDLEWARE`. Replace `FileFieldForm` with the Django 5 pattern: `MultipleFileInput(ClearableFileInput)` with `allow_multiple_selected = True`, `MultipleFileField(FileField)` whose `clean` returns a list; `FileFieldForm.file_field = MultipleFileField()`.
- [ ] **Step 7: Run tests** → PASS. Run `.venv/bin/python manage.py check` → no issues.
- [ ] **Step 8: Commit**

```bash
git add requirements.txt Dockerfile .gitignore bcoadmin/settings.py movimientos/forms.py movimientos/views.py movimientos/tests/ templates/movimientos/add_attachment.html docs/superpowers/
git rm movimientos/tests.py
git commit -m "chore: pin deps on Django 5.2, require login, add test package"
```

---

### Task 2: Parser common types and number/date helpers

**Files:**
- Create: `movimientos/parsers/__init__.py` (empty for now), `movimientos/parsers/base.py`
- Test: `movimientos/tests/test_base.py`

**Interfaces:**
- Produces (in `movimientos/parsers/base.py`):

```python
@dataclass
class ParsedMovement:
    fecha: date                 # account: movement date; card: purchase date, or closing date for installments
    descripcion: str            # "<type> <detail>", whitespace collapsed, original case
    monto: Decimal              # signed per Global Constraints
    moneda: str                 # "ARS" | "USD"
    fuente: str                 # "cuenta" | "visa" | "amex" | "master"
    codigo: str = ""            # raw receipt/reference as printed
    cuota: str = ""             # normalized "7/9" or ""
    fecha_compra: date | None = None
    titular: str = ""
    cuenta: str = ""            # account: branch or "Caja de Ahorro"/"Cuenta Corriente"; card: "Visa 5544"
    seccion: str = ""           # card: "consumo" | "pago" | "impuesto"; account: ""

@dataclass
class ParsedStatement:
    fuente: str
    desde: date | None
    hasta: date                 # account: period end; card: closing date (cierre)
    vencimiento: date | None    # card due date
    totales: dict[str, Decimal] # e.g. {"ARS": Decimal("700925.41"), "USD": Decimal("334.96")} as printed by the bank

@dataclass
class ParseResult:
    formato: str                # "card_xlsx" | "account_xlsx" | "account_pdf" | "legacy_xls"
    movimientos: list[ParsedMovement]
    resumenes: list[ParsedStatement]
    omitidas: int = 0           # rows seen but intentionally skipped (saldo inicial, section totals…)
    errores: list[str] = field(default_factory=list)

def parse_amount(value: str | float | int | None) -> Decimal | None
def parse_date(value: str | date | datetime) -> date      # dd/mm/yy, dd/mm/yyyy, "dd/mm/yyyy HH:MM", date, datetime
def norm_ref(codigo: str) -> str                           # digits only, leading zeros stripped; "" if none
def norm_cuota(cuota: str) -> str                          # "07 de 09" -> "7/9"; "" -> ""
```

- [ ] **Step 1: Write failing tests** (`test_base.py`):

```python
def test_parse_amount_formats(self):
    cases = {
        "$159.654,11": Decimal("159654.11"), "$-31.041,94": Decimal("-31041.94"),
        "U$S20,00": Decimal("20.00"), "U$S-77,89": Decimal("-77.89"),
        "-$ 67.000,00": Decimal("-67000.00"), "-U$S 77,89": Decimal("-77.89"),
        "$ 2.939.505,16": Decimal("2939505.16"), "-82212.63": Decimal("-82212.63"),
        "2110004.00": Decimal("2110004.00"), 79999.93: Decimal("79999.93"),
    }
    for raw, expected in cases.items():
        self.assertEqual(parse_amount(raw), expected, raw)
    for empty in ("", None, "-", "   "):
        self.assertIsNone(parse_amount(empty))

def test_parse_date(self):
    self.assertEqual(parse_date("24/08/26"), date(2026, 8, 24))
    self.assertEqual(parse_date("17/04/2026"), date(2026, 4, 17))
    self.assertEqual(parse_date("07/10/2026 10:48"), date(2026, 10, 7))

def test_norm_ref_and_cuota(self):
    self.assertEqual(norm_ref("000011839"), "11839")
    self.assertEqual(norm_ref("367178K"), "367178")
    self.assertEqual(norm_ref("024309*"), "24309")
    self.assertEqual(norm_ref("-"), "")
    self.assertEqual(norm_ref("000000000"), "")
    self.assertEqual(norm_cuota("07 de 09"), "7/9")
    self.assertEqual(norm_cuota("14 de 18"), "14/18")
    self.assertEqual(norm_cuota(""), "")
```

- [ ] **Step 2: Run** `.venv/bin/python manage.py test movimientos.tests.test_base` → FAIL (import error).
- [ ] **Step 3: Implement** `base.py`. `parse_amount`: a string with a comma is AR format (dots = thousands, comma = decimal); without comma it's dot-decimal; strip `$`, `U$S`, spaces; a `-` anywhere before the digits makes it negative.
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** `feat(parsers): add common parse types and amount/date helpers`.

---

### Task 3: Format A (card xlsx) and Format B (account xlsx) parsers

**Files:**
- Create: `movimientos/parsers/card_xlsx.py`, `movimientos/parsers/account_xlsx.py`, `movimientos/tests/factories.py`
- Test: `movimientos/tests/test_card_xlsx.py`, `movimientos/tests/test_account_xlsx.py`

**Interfaces:**
- Consumes: Task 2 types/helpers.
- Produces: `card_xlsx.matches(path) -> bool`, `card_xlsx.parse(path) -> ParseResult`; same pair in `account_xlsx`. `factories.make_card_xlsx(tmpdir, rows_spec) -> Path` and `factories.make_account_xlsx(tmpdir, rows, moneda="Pesos") -> Path` that reproduce the real layouts (synthetic data, no personal data).

Layout facts (from the real files, see spec §3.1):
- A: A1 empty, A2 `Movimientos del resumen`, A3 `Tarjeta Visa Crédito terminada en 5544` (or `American Express …`), row 5 cierre/vencimiento values. Sections start with a title row in col A: `Pago de tarjeta y devoluciones`, `Tarjeta de <holder> - <card name>`, `Otros conceptos`; each followed by a header row `Fecha | Descripción | Cuotas | Comprobante | Monto en pesos | Monto en dólares` (Otros conceptos: `Descripción | Monto en pesos`). Section ends at an empty row or a `Total de …` row (whose pesos/dólares cells are the statement totals for that card → `ParsedStatement.totales`). Empty `Fecha` → carry forward previous row's date. A row can have amount in pesos **or** dólares → one `ParsedMovement` per non-empty amount column.
- Card name → `fuente`: contains `Visa` → `visa`, `American Express` → `amex`, `Master` → `master`. Last 4 from "terminada en NNNN".
- Installments (`Cuotas` non-empty): `fecha` = closing date, `fecha_compra` = row date. Otherwise `fecha` = `fecha_compra` = row date. `Otros conceptos` rows: `fecha` = closing date, `seccion="impuesto"`. Payment section rows: `seccion="pago"`.
- B: sheet `UltimosMovimientos`; data starts at column B; meta rows `Cuenta`, `Moneda` (`Pesos`→ARS, `Dólares`→USD), `Fecha` (`dd/mm/yyyy - dd/mm/yyyy` → statement desde/hasta); header row has `Fecha` in col B and `Sucursal origen`, `Descripción`, `Referencia`, `Caja de Ahorro`, `Cuenta Corriente`, `Saldo`. Amount is whichever of Caja de Ahorro / Cuenta Corriente is non-empty; `cuenta` = branch (`Sucursal origen`). Descripción `"Pago de servicios \t Edesur: 0001928991"` → `"Pago de servicios Edesur: 0001928991"`. Stop at the first row with empty col B after data.

- [ ] **Step 1: Write failing tests.** Card (synthetic Visa with one installment row, one USD row with empty date, one refund, one payment, one `Otros conceptos` row, total row):

```python
def test_card_xlsx_parses_sections_and_signs(self):
    res = card_xlsx.parse(path)
    self.assertEqual(res.formato, "card_xlsx")
    st = res.resumenes[0]
    self.assertEqual((st.fuente, st.hasta, st.vencimiento), ("visa", date(2026, 9, 24), date(2026, 10, 5)))
    inst = [m for m in res.movimientos if m.cuota]
    self.assertEqual((inst[0].cuota, inst[0].fecha, inst[0].fecha_compra, inst[0].monto),
                     ("14/18", date(2026, 9, 24), date(2025, 7, 24), Decimal("-31041.94")))
    usd = [m for m in res.movimientos if m.moneda == "USD" and m.seccion == "consumo"]
    self.assertEqual(usd[1].fecha, usd[0].fecha)          # carried-forward date
    refund = [m for m in res.movimientos if m.monto == Decimal("31041.94")]
    self.assertEqual(refund[0].seccion, "consumo")
    pago = [m for m in res.movimientos if m.seccion == "pago"]
    self.assertTrue(all(m.monto > 0 for m in pago))
    imp = [m for m in res.movimientos if m.seccion == "impuesto"]
    self.assertEqual(imp[0].fecha, date(2026, 9, 24))
    consumos_ars = -sum(m.monto for m in res.movimientos if m.seccion == "consumo" and m.moneda == "ARS")
    self.assertEqual(consumos_ars, st.totales["ARS"])
    self.assertTrue(card_xlsx.matches(path)); self.assertFalse(account_xlsx.matches(path))
```

Account: synthetic file with one CA row, one CC row (`Cuenta Corriente` column), tab in description, three same-day `-15337.00` debits with different references:

```python
def test_account_xlsx(self):
    res = account_xlsx.parse(path)
    self.assertEqual(res.formato, "account_xlsx")
    self.assertEqual(res.resumenes[0].hasta, date(2026, 10, 19))
    m = res.movimientos[0]
    self.assertEqual((m.fuente, m.moneda, m.descripcion, m.monto, m.codigo),
                     ("cuenta", "ARS", "Pago de servicios Edesur: 0001928991", Decimal("-82212.63"), "45645645"))
    cc = [m for m in res.movimientos if m.monto == Decimal("-8.92")]
    self.assertEqual(len(cc), 1)
    self.assertEqual(len([m for m in res.movimientos if m.monto == Decimal("-15337.00")]), 3)
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** both parsers with openpyxl (`load_workbook(read_only=True, data_only=True)`). `matches` reads only the first ~15 rows.
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** `feat(parsers): parse card and account xlsx statements`.

---

### Task 4: Format C (monthly account PDF) parser

**Files:**
- Create: `movimientos/parsers/account_pdf.py`
- Test: `movimientos/tests/test_account_pdf.py`

**Interfaces:**
- Consumes: Task 2.
- Produces: `account_pdf.matches(path) -> bool`, `account_pdf.parse(path) -> ParseResult`, and the pure core `account_pdf.parse_account_lines(words: list[dict], moneda: str) -> tuple[list[ParsedMovement], list[str]]` where each word dict has pdfplumber keys `text, x0, x1, top, bottom` and `page` (int), already restricted to one "Movimientos en pesos/dólares" section, header line included.

Layout facts (real PDF, spec §3.1):
- Page 1 header: `Período de movimientos … Desde: 21/08/26 … Hasta: 24/09/26` → `ParsedStatement(fuente="cuenta", desde, hasta, vencimiento=None, totales={"ARS": …, "USD": …})` from the `Saldo total $ …` / `Saldo total U$S …` lines.
- ARS section: from `Movimientos en pesos` to `Saldo total $`. Header words `Fecha Comprobante Movimiento Caja de Ahorro en pesos Cuenta Corriente en pesos Saldo en cuenta` repeat on each page; use their x positions on each page as column boundaries. USD section: `Movimientos en dólares` → `Saldo total U$S`; columns `Fecha Comprobante Movimiento Caja de Ahorro en dólares Saldo en cuenta`.
- A movement = one line holding an amount in an amount column (+ the saldo), followed by 0..n detail lines (no amounts) until the next amount line. Description = movement text + " " + detail text. The date word may sit on the amount line or on its own line vertically between the amount line and the detail line: assign each date word to the movement whose vertical span `[amount line top, last detail line bottom]` contains its vertical center; when none, carry forward the previous date. Comprobante = digits in the Comprobante column, may be absent.
- `Saldo Inicial` line → skipped (counted in `omitidas`) but its saldo seeds the running-balance check: for each movement, `saldo_prev + monto == saldo` (2-decimal); a mismatch appends to `errores` (`"saldo inconsistente en <fecha> <descripcion>"`) and does not abort.
- Amount column (Caja de Ahorro vs Cuenta Corriente) → `cuenta` = `"Caja de Ahorro"` / `"Cuenta Corriente"`.
- Embedded credit card (`Tarjeta Santander` / `Visa crédito` page): cierre/vencimiento from `Cierre Vencimiento` line values; card last 4 from `Tarjeta terminada en NNNN`; sections `Pago anterior y devoluciones` (skip `Saldo anterior`; `Tu pago en pesos/dólares` → `seccion="pago"`, other rows e.g. `Cr.rg 5617 30% m` → `seccion="impuesto"`), `Consumos del mes` (columns `Fecha Comprobante Descripción Cuota Pesos Dólares`; `Consumos totales $ … U$S …` → `totales`), `Impuestos` (→ `seccion="impuesto"`). Installment rule as in Task 3. In descriptions the PDF prints `/` where xlsx prints `*` (`Dlo/duvet home` vs `Dlo*duvet home`); keep as printed (payee_key in Task 8 normalizes both).
- **Never** import the `Tarjeta de débito` / `Compras` / `Pagos` pages (they repeat account rows).

- [ ] **Step 1: Write failing unit tests** with hand-built word lists (helper `w(text, x0, top, page=0)` with `x1 = x0 + 5*len(text)`, `bottom = top + 8`), columns at x: Fecha 40, Comprobante 80, Movimiento 140, Caja de Ahorro 330, Cuenta Corriente 420, Saldo 500:

```python
def test_date_on_detail_line_and_missing_comprobante(self):
    words = header() + [
        w("21/08/26", 40, 100), w("Saldo", 140, 100), w("Inicial", 170, 100), w("$", 330, 100), w("2.939.505,16", 340, 100), w("$", 500, 100), w("2.939.505,16", 510, 100),
        w("27344390", 80, 120), w("Pago", 140, 120), w("de", 165, 120), w("servicios", 180, 120), w("-$", 330, 120), w("69.226,31", 340, 120), w("$", 500, 120), w("2.870.278,85", 510, 120),
        w("24/08/26", 40, 127),                                   # date between the two lines
        w("Edesur:", 140, 134), w("0001928991", 180, 134),
        w("25/08/26", 40, 150), w("Pago", 140, 150), w("de", 165, 150), w("tarjeta", 180, 150), w("de", 215, 150), w("credito", 230, 150), w("-$", 330, 150), w("10.679,16", 340, 150), w("$", 500, 150), w("2.859.599,69", 510, 150),
    ]
    movs, errs = parse_account_lines(words, "ARS")
    self.assertEqual(errs, [])
    self.assertEqual([(m.fecha, m.descripcion, m.monto, m.codigo) for m in movs], [
        (date(2026, 8, 24), "Pago de servicios Edesur: 0001928991", Decimal("-69226.31"), "27344390"),
        (date(2026, 8, 25), "Pago de tarjeta de credito", Decimal("-10679.16"), ""),
    ])

def test_running_balance_mismatch_is_reported(self):
    # same as above but saldo of the 2nd movement off by 1 peso
    movs, errs = parse_account_lines(words_bad, "ARS")
    self.assertEqual(len(movs), 2); self.assertEqual(len(errs), 1)
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `parse_account_lines` (group words into lines by `top` within 2 pt), then `parse` (open with pdfplumber, `page.extract_words(keep_blank_chars=False, use_text_flow=False)`, slice sections by marker lines, call `parse_account_lines` for ARS and USD, parse the card pages, build `ParseResult(formato="account_pdf")`). `matches`: first page text contains `Mi resumen de cuenta`.
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** `feat(parsers): parse monthly account PDF incl. embedded card`.

---

### Task 5: Format D adapter, `detect()`, models and data migration

**Files:**
- Create: `movimientos/parsers/legacy_xls.py`, migrations `0008_dashboard_fields.py` (schema) and `0009_backfill_legacy.py` (data)
- Modify: `movimientos/parsers/__init__.py`, `movimientos/models.py`
- Test: `movimientos/tests/test_detect.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces:
  - `parsers.detect(path) -> module | None` trying `card_xlsx`, `account_xlsx`, `account_pdf`, `legacy_xls` `.matches` in that order, swallowing exceptions as non-match; `parsers.parse(path) -> ParseResult | None`.
  - `legacy_xls.matches(path)` (extension `.xls` and the old header row `Fecha, Sucursal origen/Origen, Referencia, Descripción`), `legacy_xls.parse(path) -> ParseResult(formato="legacy_xls")` reusing the column logic of `xls_parser.get_movimientos` but returning `ParsedMovement(fuente="cuenta", moneda="ARS")` and using `parse_amount`.
  - Models (fields exact; Django field options at builder's discretion):

```python
class Detalle(TimeStampedModel):           # existing fields kept; monto becomes DecimalField(max_digits=14, decimal_places=2, null=True)
    fuente = CharField(max_length=10, default="cuenta", db_index=True)
    moneda = CharField(max_length=3, default="ARS", db_index=True)
    tipo = CharField(max_length=8, choices=[("ingreso",…),("gasto",…),("interno",…)], default="gasto", db_index=True)
    categoria = ForeignKey(Categoria, null=True, blank=True, on_delete=SET_NULL)
    categoria_manual = BooleanField(default=False)
    cuota = CharField(max_length=10, blank=True)
    fecha_compra = DateField(null=True, blank=True)
    titular = CharField(max_length=100, blank=True)
    ref = CharField(max_length=30, blank=True, db_index=True)   # norm_ref(codigo)
    comercio = CharField(max_length=120, blank=True, db_index=True)  # payee_key, filled by Task 8
    archivo = ForeignKey(DataFile, null=True, blank=True, on_delete=SET_NULL)
    # Meta: index on (fuente, moneda, monto)

class Tarjeta(TimeStampedModel):           # existing model reused; fix __str__ to return self.nombre
    nombre, codigo                          # existing
    fuente = CharField(max_length=10, unique=True)      # "visa" | "amex" | "master"
    patron_pago = CharField(max_length=200)            # regex matched (re.I) against account Detalle.descripcion

class Resumen(TimeStampedModel):
    fuente = CharField(max_length=10); desde = DateField(null=True); hasta = DateField()
    vencimiento = DateField(null=True); totales = JSONField(default=dict)
    archivo = ForeignKey(DataFile, null=True, on_delete=SET_NULL)
    # unique_together (fuente, hasta)

class Regla(TimeStampedModel):
    patron = CharField(max_length=200)
    es_regex = BooleanField(default=False)          # False: case-insensitive "contains"
    fuente = CharField(max_length=10, blank=True)   # "" = any; "tarjeta" = any card
    moneda = CharField(max_length=3, blank=True)
    prioridad = IntegerField(default=100)           # lower runs first
    categoria = ForeignKey(Categoria, null=True, blank=True, on_delete=CASCADE)
    tipo = CharField(max_length=8, blank=True)      # "" = keep default kind

class Recurrente(TimeStampedModel):
    nombre = CharField(max_length=120); clave = CharField(max_length=120, blank=True)
    patron = CharField(max_length=200, blank=True)  # manual items: case-insensitive contains on descripcion
    moneda = CharField(max_length=3); fuente = CharField(max_length=10)
    frecuencia_meses = PositiveSmallIntegerField(default=1); dia_habitual = PositiveSmallIntegerField(default=1)
    monto_ref = DecimalField(max_digits=14, decimal_places=2, null=True); ultima_fecha = DateField(null=True)
    estado_admin = CharField(max_length=10, choices=sugerido/confirmado/silenciado, default="sugerido")
    manual = BooleanField(default=False); nombre_editado = BooleanField(default=False)
    # unique_together (clave, moneda) when clave != ""
```

  - Remove `MovimientoIgnorado` (unused; replaced by `Regla`). Remove the unused `get_movimientos` import from `models.py`.
  - `0009_backfill_legacy`: for all existing rows set `fuente="cuenta"`, `moneda="ARS"`, `ref=norm_ref(codigo)`, `tipo` by sign. (Classification runs later in Task 8.)

- [ ] **Step 1: Write failing tests** (`test_detect.py`): `detect()` returns `card_xlsx` / `account_xlsx` for factory files; returns `None` for an empty xlsx, a `.txt` and a random binary file; a `Detalle` created with `monto=Decimal("-15337.00")` round-trips exactly.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** adapter, `detect`, models; `makemigrations movimientos` then hand-write `0009` with `RunPython` (+ noop reverse).
- [ ] **Step 4: Run tests** → PASS. Then on the real DB (backup made in Task 1): `.venv/bin/python manage.py migrate` and verify `sqlite3 db.sqlite3 "select count(*), sum(moneda='ARS') from movimientos_detalle"` → `5072|5072`.
- [ ] **Step 5: Commit** `feat(models): extend Detalle, add Regla/Resumen/Recurrente, legacy adapter and detect()`.

---

### Task 6: Import service with deduplication + `importar` command

**Files:**
- Create: `movimientos/services/__init__.py`, `movimientos/services/importer.py`, `movimientos/management/__init__.py`, `movimientos/management/commands/__init__.py`, `movimientos/management/commands/importar.py`
- Test: `movimientos/tests/test_importer.py`

**Interfaces:**
- Consumes: `parsers.detect/parse`, models.
- Produces:

```python
@dataclass
class ImportReport:
    archivo: str; formato: str | None; nuevos: int = 0; duplicados: int = 0
    omitidas: int = 0; errores: list[str] = field(default_factory=list)
    rechazado: bool = False

def import_file(path: str, datafile: DataFile | None = None) -> ImportReport
def import_files(paths: list[str]) -> list[ImportReport]   # creates a DataFile per path, never raises; after all files: classify.clasificar_todo(); recurring.detectar_recurrentes()
```

  (`clasificar_todo` and `detectar_recurrentes` come from Tasks 8–9; until then `import_files` calls them only if importable — or the builder wires them in those tasks.)
- Matching algorithm (pinned):

```
for each ParsedMovement p (in file order), inside one transaction per file:
    candidates = Detalle rows with same fuente, moneda, monto, cuota,
                 fecha within p.fecha ± 2 days (compare by date),
                 not already consumed by this file,
                 and (row.ref == norm_ref(p.codigo) or row.ref == "" or norm_ref(p.codigo) == "")
    if candidates: consume the closest by |date diff|, prefer equal ref → duplicados += 1
    else: create Detalle(..., ref=norm_ref(p.codigo), archivo=datafile) and mark it consumed → nuevos += 1
```

  `Resumen` upserted by `(fuente, hasta)`. A file with `formato None` → `rechazado=True`, nothing saved. Any exception inside one file → transaction rolled back, error message in `errores`, `rechazado=True`, continue with next file.
- `./manage.py importar <paths...>` prints one line per report: `<archivo>: <formato> nuevos=N duplicados=N omitidas=N errores=N`.

- [ ] **Step 1: Write failing tests** (all with factory files / `ParseResult` stubs patched into `parsers.parse`):
  - `test_reimport_same_file_adds_nothing` — second import: `nuevos == 0`, `duplicados == len(movs)`.
  - `test_zero_padding_cross_format` — row `ref "11839"` from file 1, same movement with codigo `"000011839"` and date +1 day from file 2 → `duplicados == 1`.
  - `test_same_day_same_amount_different_ref_kept` — three `-15337.00` rows on one date, refs `406029/406030/406031` → `nuevos == 3`; re-import → `nuevos == 0`.
  - `test_same_day_same_amount_no_ref_kept_then_deduped` — two `-24.50` rows, same date, no codigo → first import `nuevos == 2`, re-import `nuevos == 0, duplicados == 2`.
  - `test_installment_next_cuota_is_new` — same ref `760275`, cuota `14/18` then `15/18` → 2 rows.
  - `test_currency_separates` — same amount/date/ref in ARS and USD → 2 rows.
  - `test_matches_legacy_row` — existing legacy row (fecha with time `10:48`, ref `"45645645"`) vs parsed movement same date/amount/codigo → duplicate.
  - `test_unrecognised_file_rejected_others_imported` — `import_files([bad_txt, good_xlsx])` → first `rechazado`, second `nuevos > 0`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** importer and command.
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** `feat(import): dedup-aware import service and importar command`.

---

### Task 7: Upload and sync views on the new importer

**Files:**
- Modify: `movimientos/views.py` (`add_attachment` → `upload`, `sync`), `bcoadmin/urls.py`
- Create: `templates/movimientos/upload.html`
- Test: `movimientos/tests/test_views.py`

**Interfaces:**
- Consumes: `importer.import_files`, `forms.FileFieldForm`.
- Produces: URL name `upload` at `/upload/` (GET form, POST multi-file → same page showing one row per `ImportReport`: archivo, formato, nuevos, duplicados, omitidas, errores / "Formato no reconocido"). `sync` keeps calling `get_movs.download_movs`, then `import_files([new_path])` and renders `upload.html` with the report. Every `render` passes `request`.

- [ ] **Step 1: Write failing tests** (logged-in client): POST two files (valid factory account xlsx + `notes.txt`) → 200, response contains `Formato no reconocido` and the valid file's `nuevos` count; `Detalle` count increased.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** views/template (Materialize, Spanish copy, accept `.xls,.xlsx,.pdf`).
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** `feat(upload): multi-format upload with per-file import report`.

---

### Task 8: Classification — rules, kinds, card-payment resolver, payee key, seed

**Files:**
- Create: `movimientos/services/classify.py`, `movimientos/management/commands/seed_reglas.py`
- Modify: `movimientos/services/importer.py` (call `clasificar_todo()` after imports)
- Test: `movimientos/tests/test_classify.py`

**Interfaces:**
- Produces:

```python
def payee_key(descripcion: str) -> str
def clasificar_todo() -> int                  # re-classifies all rows with categoria_manual=False; fills comercio; returns rows changed
def resolver_pagos_tarjeta() -> None          # called by clasificar_todo after rules
CAT_SIN_DETALLE = "Tarjeta sin detalle"; CAT_PAGO_TARJETA = "Pagos de tarjeta"; CAT_INTERNOS = "Movimientos internos"
```

- `payee_key` (pinned behaviour, exact string is builder's choice): lowercase, strip accents, `*` and `/` → space; remove `tarj nro. \d+`; remove dates (`dd/mm/yy(yy)`, and a trailing 6-digit `ddmmyy` glued after a ≥10-digit run); remove amounts (`$…`, `u$s…`, `%…`, `sobre $…`); remove tokens that mix letters and digits with length ≥ 6 (debin ids, `in1u6bzsbusd`); digit runs ≥ 10 → keep their first 10 digits; drop a trailing `usd`; collapse whitespace.
- Rules: `Regla` rows ordered by `(prioridad, id)`; `contiene` = case-insensitive substring of `descripcion`; `es_regex` = `re.search(patron, descripcion, re.I)`; filters `fuente` (`"tarjeta"` = any of visa/amex/master) and `moneda`. First match sets `categoria` and, if `tipo` non-empty, `tipo`. No match → `categoria=None`, default kind (Global Constraints). `ParsedMovement.seccion` is not persisted; card-side payments are classified by the seeded regex rule `su pago en|tu pago en` (fuente `tarjeta`, tipo `interno`, categoría `Pagos de tarjeta`).
- `resolver_pagos_tarjeta`: for every non-manual `fuente="cuenta"` row matching some `Tarjeta.patron_pago` (re.I): if a `Resumen` with that `Tarjeta.fuente` has `vencimiento` within ±10 days of the row's date → `tipo="interno"`, categoría `Pagos de tarjeta`; else `tipo="gasto"`, categoría `Tarjeta sin detalle`.
- `seed_reglas` (idempotent): builds the `Categoria` tree and `Regla(contiene)` rows from `settings.CATEGORIAS_GASTOS` (nested dicts → child categories; list values → one rule per pattern; key `Tarjetas` skipped); `Tarjeta` rows from `settings.TARJETAS` with `patron_pago` = codes joined by `|` plus: visa adds `pago de tarjeta de credito$` and `^pago de tarjeta de credito`, amex adds `amex`; interno rules (`prioridad=10`, categoría `Movimientos internos`): `compra de dolares`, `venta de dolares`, `op cambio`, `transf online banking propia`, each entry of `settings.IGNORAR`, and each entry of optional `settings.INTERNOS` (owner-specific, e.g. own-name transfers — not in code); card payment rule above; Impuestos rules (`prioridad=50`): `impuesto`, `iva `, `iibb`, `percep`, `rg 5617`, `sellos`, `sircreb`. All settings read with `getattr(settings, NAME, default)`.
- Admin hook (Task 10) calls `clasificar_todo()` after saving a `Regla` or `Tarjeta`.

- [ ] **Step 1: Write failing tests:**

```python
def test_payee_key_groups_and_separates(self):
    same = [
        ("Debito automatico Federacion patro-0600123436000000111026", "Debito automatico Federacion patro-0600123436000000110926"),
        ("Pago de servicios Edesur: 0001928991", "PAGO DE SERVICIOS EDESUR: 0001928991"),
        ("Dlo*duvet home", "Dlo/duvet home"),
        ("Anthropic* claud in1u", "Anthropic/ claud in1u6bzsbusd"),
        ("Compra con tarjeta de debito Merpago*mercadolibre - tarj nro. 0698", "Compra con tarjeta de debito Merpago*mercadolibre - tarj nro. 4659"),
        ("Debito debin Id debin r7zg0qndk7m003yv9exypo cuit 30703 088534", "Debito debin Id debin jmqkyz9qgeqwy5v8nv50p3 cuit 30703 088534"),
    ]
    for a, b in same: self.assertEqual(payee_key(a), payee_key(b), (a, b))
    self.assertNotEqual(payee_key("Debito automatico Federacion patro-0600123436000000111026"),
                        payee_key("Debito automatico Federacion patro-0600123439000000111026"))

def test_card_payment_is_interno_only_when_card_detail_exists(self): ...
    # account row "Pago tarjeta de credito amex Deb. automatico 13/10/2026 part 00012312312", -165101.84, 2026-10-13
    # without Resumen(amex) → tipo gasto, categoria "Tarjeta sin detalle"
    # add Resumen(fuente="amex", hasta=2026-10-01, vencimiento=2026-10-13) → clasificar_todo() → tipo interno

def test_manual_category_survives(self): ...   # categoria_manual=True row keeps categoria/tipo after clasificar_todo()
def test_card_refund_is_negative_gasto(self): ...  # fuente visa, monto +31041.94 → tipo gasto
def test_dollar_purchase_is_interno(self): ...     # "Debito por compra de dolares Compraste u$s 1.563,52 a $ 1.534,99" → interno (after seed)
def test_rule_priority(self): ...                  # two matching rules → lower prioridad wins
def test_seed_is_idempotent(self): ...             # override_settings(CATEGORIAS_GASTOS=…, TARJETAS=…, IGNORAR=[…]); run twice → same counts
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `classify.py`, `seed_reglas`; wire into `import_files`.
- [ ] **Step 4: Run tests** → PASS. Then on real DB: `./manage.py seed_reglas && ./manage.py shell -c "from movimientos.services.classify import clasificar_todo; print(clasificar_todo())"`; check `select tipo, count(*) from movimientos_detalle group by tipo` is plausible (gasto majority, some interno).
- [ ] **Step 5: Commit** `feat(classify): rule engine, card-payment resolver, payee key and seed`.

---

### Task 9: Recurring detection and status

**Files:**
- Create: `movimientos/services/recurring.py`
- Modify: `movimientos/services/importer.py` (call `detectar_recurrentes()` after `clasificar_todo()`)
- Test: `movimientos/tests/test_recurring.py`

**Interfaces:**
- Produces:

```python
TOLERANCIA_DIAS = 7
def fecha_referencia(fuente: str) -> date | None     # max(Resumen.hasta for fuente), else max(Detalle.fecha for fuente)
def detectar_recurrentes(hoy: date | None = None) -> int     # upserts Recurrente; returns count of candidates
def movimientos_de(rec: Recurrente) -> QuerySet[Detalle]     # by comercio == clave (auto) or descripcion icontains patron (manual)
def estado(rec: Recurrente, ref: date | None = None) -> str  # "pagado" | "pendiente" | "falta" | "atrasado" | "silenciado"
```

- Detection (pinned): consider `tipo="gasto"`, `activo=True` rows; group by `(comercio, moneda, fuente)`; look at the 13 months up to `fecha_referencia(fuente)`; candidate if present in ≥ 3 distinct `YYYY-MM`. `frecuencia_meses` = median gap in months between consecutive distinct months, snapped to the nearest of {1, 2, 3, 6, 12}; `dia_habitual` = median day of month; `monto_ref` = last amount (absolute); `ultima_fecha` = last date. Upsert by `(clave=comercio, moneda)`: always update computed fields unless `manual=True`; set `nombre` (title-cased key) only when `nombre_editado=False`; never touch `estado_admin`. Groups that are no longer candidates are left as they are (not deleted).
- Status (pinned): `silenciado` if `estado_admin == "silenciado"`. Else `ref = ref or fecha_referencia(rec.fuente)`; `esperado` = `ultima_fecha` + `frecuencia_meses` months, day = `min(dia_habitual, month length)`; `limite = esperado + TOLERANCIA_DIAS`. `ref < esperado` → `pagado` unless the last occurrence came later than its own expected date + tolerance (computed from the previous occurrence) → `atrasado`; `esperado ≤ ref < limite` → `pendiente`; `ref ≥ limite` → `falta`.

- [ ] **Step 1: Write failing tests** (synthetic `Detalle` rows with `comercio` set directly):
  - `test_monthly_detected` — Edesur on day 17 for 2026-05…2026-09 → one `Recurrente`, `frecuencia_meses == 1`, `dia_habitual == 17`.
  - `test_bimonthly_detected` — Aysa 2026-01, 03, 05, 07, 09 → `frecuencia_meses == 2`.
  - `test_two_contracts_two_items` — two `comercio` keys from the same provider → 2 items.
  - `test_missing_month_is_falta` — monthly day 10, last 2026-08-10, `ref=2026-09-24` → `falta`; `ref=2026-09-12` → `pendiente`; `ref=2026-09-05` → `pagado`.
  - `test_reference_is_latest_import_not_calendar` — `Resumen(cuenta, hasta=2026-09-05)` and no `ref` passed → status computed against 2026-09-05.
  - `test_late_payment_is_atrasado` — monthly day 10; occurrences …07-10, 08-25 (late beyond 7 days), `ref=2026-09-01` → `atrasado`.
  - `test_detection_keeps_owner_edits` — item with `nombre_editado=True, nombre="Luz"`, `estado_admin="silenciado"` → re-run keeps both.
  - `test_fewer_than_three_months_ignored`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `recurring.py` (month arithmetic with `calendar.monthrange`, no new dependency); wire into `import_files`.
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** `feat(recurring): detect recurring payments and compute status`.

---

### Task 10: Admin

**Files:**
- Modify: `movimientos/admin.py`
- Test: `movimientos/tests/test_views.py` (admin part)

**Interfaces:**
- Consumes: `classify.clasificar_todo`, `recurring.estado`.
- Produces:
  - `DetalleAdmin`: list `fecha, fuente, moneda, descripcion, monto, tipo, categoria, categoria_manual`; filters `fuente, moneda, tipo, categoria, categoria_manual, ("categoria", EmptyFieldListFilter)`; search `descripcion, codigo`; `list_editable = ("categoria", "tipo")`; `save_model` sets `categoria_manual=True` when `categoria` or `tipo` changed; action "Volver a reglas automáticas" (sets `categoria_manual=False` then `clasificar_todo()`).
  - `ReglaAdmin`, `TarjetaAdmin`: `save_model`/`delete_model` → `clasificar_todo()`; `ReglaAdmin` action "Re-aplicar reglas".
  - `RecurrenteAdmin`: list `nombre, fuente, moneda, frecuencia_meses, dia_habitual, monto_ref, ultima_fecha, estado_admin, estado_actual` (computed with `estado`); actions "Confirmar", "Silenciar"; `save_model` sets `nombre_editado=True` when `nombre` changed; adding one from the admin sets `manual=True`.
  - `ResumenAdmin` read-only list. `CategoriaAdmin` keeps `DraggableMPTTAdmin`. `DataFileAdmin` without the custom form (remove the broken `clean`).

- [ ] **Step 1: Write failing tests**: superuser client POSTs a `Detalle` change setting `categoria` → `categoria_manual` becomes `True`; changing a `Recurrente.nombre` sets `nombre_editado`; changelist pages for all registered models return 200.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** `feat(admin): manage movements, rules, cards and recurring items`.

---

### Task 11: Aggregations for the dashboard

**Files:**
- Create: `movimientos/services/stats.py`
- Test: `movimientos/tests/test_stats.py`

**Interfaces:**
- Produces (all exclude `tipo="interno"` and `activo=False`, filter by `moneda`; months as `"YYYY-MM"`; amounts `Decimal`, expenses positive):

```python
def month_range(desde: str, hasta: str) -> list[str]
def monthly_totals(moneda: str, desde: str, hasta: str) -> list[dict]          # [{"mes","ingresos","gastos","balance"}] one per month, zeros included
def spending_by_category(moneda: str, mes: str) -> list[tuple[str, int | None, Decimal]]   # (root category name or "Sin categoría", root id or None, total), desc
def category_evolution(moneda: str, desde: str, hasta: str, top_n: int = 6) -> dict      # {"meses": [...], "series": [{"nombre","id","valores":[...]}]}, rest summed as "Otros"
def ultimo_mes_con_datos(moneda: str) -> str | None
```

  `gastos` = `-sum(monto where tipo="gasto")` (refunds net out). Category grouping uses the level-0 ancestor (`categoria.get_root()`).

- [ ] **Step 1: Write failing tests:** a month with ingreso `+1000`, gastos `-300` and `-200`, refund `+50` (gasto), interno `-500`, plus a USD gasto → `monthly_totals("ARS", …)` = `ingresos 1000, gastos 450, balance 550`; USD isolated; empty middle month present with zeros; months of different years never merge (`2025-09` vs `2026-09`); `spending_by_category` rolls a child category up to its root and puts uncategorized under `"Sin categoría"`; `category_evolution(top_n=1)` returns the top series plus `"Otros"`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** with ORM aggregation (`TruncMonth`) + Python roll-up.
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** `feat(stats): per-currency monthly, category and evolution aggregations`.

---

### Task 12: Dashboard (layout A), movement list, cleanup

**Files:**
- Modify: `movimientos/views.py`, `bcoadmin/urls.py`, `templates/movimientos/base.html` (nav: Tablero, Movimientos, Importar, Sync, Admin; load Chart.js)
- Create: `templates/movimientos/dashboard.html` (rewrite), `templates/movimientos/movimientos_list.html`
- Delete: files listed in "File Structure → Delete at the end"
- Test: `movimientos/tests/test_views.py`

**Interfaces:**
- Consumes: `stats.*`, `recurring.estado`, `Recurrente`.
- Produces:
  - `dashboard(request)` at `/` (name `dashboard`). GET params: `moneda` (`ARS` default | `USD`), `desde`/`hasta` (`YYYY-MM`, default: last 12 months ending at `ultimo_mes_con_datos`), `mes` (default `hasta`). Context → template: KPI row (ingresos, gastos, balance of `mes`; count of recurrentes in `falta` for that moneda), three charts fed via `{{ data|json_script:"dash-data" }}`, recurring panel (non-silenced items of that moneda, ordered falta → pendiente → atrasado → pagado, icons ❌ ⏳ ⚠ ✅, `monto_ref`, `ultima_fecha`).
  - Chart.js `https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js`: grouped bars ingresos/gastos + balance line; doughnut for `spending_by_category(mes)`; lines for `category_evolution`.
  - Drill-down links to `movimientos_list` (name `movimientos`, `/movimientos/`) with GET filters `moneda`, `mes`, `categoria` (id, or `none` for "Sin categoría"), `recurrente` (id), `tipo`; clicking a bar → `?mes=`, a doughnut slice → `?mes=&categoria=`, a recurring row → `?recurrente=`. The list shows fecha, fuente, descripcion, cuota, monto, tipo, categoria, link to the admin change page, and a total row; tablesorter kept.
  - Old URLs `balance/`, `tarjetas/`, `categorias/`, `detalle/YYYY/MM/`, `YYYY/MM/` → `RedirectView` to the new pages (month URLs → `/movimientos/?mes=YYYY-MM`).
  - Remove now-dead code in `views.py` (`balance`, `wrapper_view`, `update_movimientos`, `DetalleMonthArchiveView`) and `xls_parser.py`.

- [ ] **Step 1: Write failing tests** (logged in, seeded with a few `Detalle` rows in ARS and USD + one `Recurrente` in `falta`): `/` → 200, context `kpis.balance` correct, `dash-data` script present, `❌` present; `/?moneda=USD` shows only USD totals; `/movimientos/?mes=2026-09&categoria=none` lists only uncategorized September rows; `/balance/` → 302 to `/`; `/2026/09/` → 302 to `/movimientos/?mes=2026-09`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** views, templates, URL cleanup, deletions.
- [ ] **Step 4: Run full suite** `.venv/bin/python manage.py test movimientos` → all PASS; `manage.py check` clean.
- [ ] **Step 5: Commit** `feat(dashboard): one-page dashboard with charts, movement list; remove legacy views`.

---

### Task 13: Real-sample integration test and end-to-end check

**Files:**
- Create: `movimientos/tests/test_samples_integration.py`

**Interfaces:**
- Consumes: everything.

- [ ] **Step 1: Write the integration test** (skipped unless `BCOADMIN_SAMPLES_DIR` is set; runs on a test DB):
  - Each `*.pdf` parses with `errores == []` (running balance consistent) and its embedded card `Consumos totales` equals `-sum` of its card `consumo` rows per currency.
  - Each `Último resumen*.xlsx`: consumos per currency equal the `Total de … terminada en` row.
  - `import_files(all files)` then `import_files(all files)` again → second run `nuevos == 0` for every file.
  - Importing `Último resumen - Visa 5544.xlsx` and the `2026-09-24` PDF → the Visa 2026-09 cycle has no duplicate rows (count of `fuente="visa"` rows with `fecha_compra` in that cycle equals the xlsx consumption count).
- [ ] **Step 2: Run** `BCOADMIN_SAMPLES_DIR=~/Downloads .venv/bin/python manage.py test movimientos.tests.test_samples_integration` → PASS (fix parsers if not; each fix gets a unit test in the parser's test file).
- [ ] **Step 3: Real import**: `./manage.py importar ~/Downloads/2024-1*.pdf ~/Downloads/2025-*.pdf ~/Downloads/2026-*.pdf ~/Downloads/movimientos*.xlsx ~/Downloads/"Último resumen"*.xlsx` → no `rechazado`; then `./manage.py runserver`, log in, open `/` in ARS and USD: charts render, recurring panel lists Edesur/Metrogas/Aysa/Federación Patronal items, no console errors.
- [ ] **Step 4: Commit** `test: real-sample integration test for parsers and dedup`.
