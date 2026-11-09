# Existing POS system analysis

Review date: 2026-11-09. Repository: `zahraadelbani/building_pos`; local checkout: `building_pos`, HEAD `9e3f494` (`updates`). This report describes inspected implementation, not proposed functionality. Companion documents contain requirements, gaps, implementation stages, and questions.

## Scope and verification

Inspected application models, function views, routes, all 21 migrations, admin registrations, templates, template filters, CSS, printing queue/API/USB agent, dependencies, settings, entry points, and existing test coverage. No application code, migrations, dependencies, or production records were changed. The five requested Markdown documents are the only intended file changes. No commits or pushes were made.

Executed `.\.venv\Scripts\python.exe manage.py test pos --verbosity 1` with bytecode writing disabled: **56 tests passed**, Django system check reported no issues. Django created and destroyed its isolated test database. This establishes the existing suite's baseline, not verification of concurrent cashiers or physical printing.

Limitations: review is of this local revision, not a fresh remote comparison. Local `db.sqlite3` exists but customer/payment contents and production migration state were not inspected. Secrets in `.env` were not read. Logs were inventoried, not used as proof of current behavior; virtual environment and generated files are not application source. No hardware, running deployment, browser visual checks, external accounting policy, backup restore, or load test was verified. Missing features below mean absent from inspected models, views, routes, and UI; not claims about external systems.

## Architecture and dependencies

- A Django monolith, single business app `pos`; no separate frontend application, Django REST Framework, serializer layer, or dedicated business service layer. Most business rules live in `pos/views.py`, with calculations in `pos/models.py` and printing in `pos/printing.py` / `pos/receipt.py`.
- `requirements.txt`: Django 5.1.1, asgiref 3.11.0, sqlparse 0.5.5, tzdata 2025.3, requests 2.32.3, python-dotenv 1.0.1, python-escpos 3.1, pyusb 1.2.1, libusb1 3.1.0, Pillow 10.4.0. These are checked-in pins, not a dependency security assessment.
- `building_pos/settings.py`: SQLite `db.sqlite3`, UTC timezone, Django sessions/auth/CSRF middleware and template rendering, local static directory, environment-loaded print token. `building_pos/urls.py` mounts POS at `/` and Django admin at `/admin/`. `manage.py`, `asgi.py`, and `wsgi.py` are conventional entry points.
- Frontend: `templates/base.html` provides navigation and shared scripts; `static/css/app.css` is a local utility stylesheet. Base template also references Bootstrap Icons CDN and provides fallbacks. Inline JavaScript in POS/products/payment templates supplies product search, selection, modal controls, fetch requests, and payment totals. POST forms use CSRF tokens. Server validation remains authoritative.
- Settings contain development defaults: debug enabled, empty allowed hosts, checked-in secret key. No checked-in container, production process manager, CI pipeline, or deployment manifest was found. Deployment security and hosting topology remain unverified.

## Database and relationships

All listed models are implemented in `pos/models.py`.

| Model | Actual fields and relationships | Consequence |
|---|---|---|
| `Product` | Unique name, optional unique integer code, cost/selling prices, decimal stock and threshold, creation date | One unlabelled stock quantity; no supplier or unit FK. `save()` assigns code using maximum + 1. |
| `Order` | Unique integer number; unpaid/paid/refunded status; active flag; free-text customer name; amount/percent discount; stock-deduction timestamp | Draft and supplied unpaid bills share status. No explicit invoice/proforma type, issuance date, due date, customer/company model, or immutable document snapshot. |
| `OrderItem` | Order CASCADE; nullable Product SET_NULL; name, free-text box, decimal quantity, selling price and cost snapshot | Historical line price/name/cost survive product deletion, but units are only text. |
| `Payment` | Order PROTECT; method choices; nullable free-text bank; tendered amount, change, timestamp | Many payments per order supports split tender structurally. No payment receipt number, reference, allocation ledger, reversal link, or operator. |
| `Refund`, `RefundItem` | Order PROTECT; method/bank/amount/profit/date/note; item links original OrderItem PROTECT and Product SET_NULL, stores quantity/price/cost/amount | Partial returns have a recorded history; no general credit-note/invoice adjustment model. |
| `StockMovement` | Product CASCADE; optional Order SET_NULL; restock/adjustment/sale/refund; signed quantity, reason/note, occurrence/creation dates | Movement history exists, but no unique posting identity, original movement reversal FK, supplier, receipt, or batch allocation link. |
| `StockBatch` | Product CASCADE; initial/remaining quantities, cost/selling prices, receipt date | FIFO layers ordered by ID, not receipt date. No supplier provenance or persisted link from consumed batch to sale. |
| `Expense` | Date/category/description/amount, cash/wave/bank/aps, bank and notes | Cash outflow and operating expense recording; not a supplier obligation/payment ledger. |
| `PrintJob` | JSON payload; type/copy/status, device targeting/claiming, timestamps, attempts/error/priority | Persisted print queue, not a canonical business document archive. |

Prices generally have two decimal places; stock is decimal(12,2), item quantities decimal(10,2), payments decimal(10,2). No explicit domain `CheckConstraint` declarations for positive quantities, valid conversions, or unique business postings are present. Validation is primarily in views; direct admin writes need separate consideration.

### Migration history

`0001` creates orders/items/payments. `0002` adds active state; `0003–0005` add products/default quantity/product links/stock. `0006` changes pricing, removes product unit price/default quantity, renames item price and backfills cost snapshots using then-current product cost. `0007` adjusts item price metadata; `0008` removes stock. `0009` adds print jobs; `0010` adds box text; `0011` product code; `0012–0013` selling price and print report types; `0014` expenses; `0015` removes expense vendor. `0016` reintroduces stock with default zero, stock movements, and deduction marker. `0017` adds customer and discounts; `0018` expense payment/bank; `0019` refunds/items; `0020` renames refund indexes; `0021` adds stock batches and seeds positive stock at current product prices. Historical deleted fields and approximate backfills cannot reconstruct original purchases or original historical costs. Do not rewrite these migrations.

## Sales, checkout, and payment behavior

`create_new_order()` uses `Max(order_number)+1`; uniqueness is enforced by the database but allocation is not serialized. `pos_home()` lists active unpaid bills, searches numeric product code or product name, and returns `_product_results.html` for partial search. `add_product_to_order()` locks order/product, splits lines using available FIFO costs/prices, and merges matching allocations through `_merge_or_create_product_line()`. Batches are consumed later, so cart-time cost snapshots can become stale across simultaneous carts. `add_custom_item_to_order()` permits non-stock items without creating a Product.

`update_order_details()` validates customer text and order-wide fixed/percentage discounts. `Order.subtotal_amount()`, `discount_amount()`, `total_amount()`, and `total_profit()` calculate from line snapshots; discount is capped to subtotal in model calculations. No tax or currency configuration exists in these models. `Order.net_amount()` means sale total less refunds, **not receivables balance**.

`pay_later()` requires customer name, makes the bill inactive, and invokes `_deduct_order_stock()` while keeping status unpaid. `unpaid_orders()` searches number/customer. `resume_order()` restores remaining stock, clears the marker, and reopens the bill. This is not safe invoice editing once goods have actually left the shop.

`_parse_payments_from_post()` records cash, bank, wave, aps. Choices and split field names are hardcoded in models, views, templates, and reports. Bank is already a separate free-text field; GTBank and Ecobank can be typed, but have no controlled selection or separate report grouping. Cheque is absent. No Wave/APS gateway call or confirmation workflow exists; these are recorded tenders. Requests usage in the print agent is unrelated to payment processing.

Split tender allows several Payment rows in one checkout, one bank-name input, non-cash total no greater than order total, sufficient cash for the remainder, and cash change. A single non-cash payment must exactly equal total; cash must cover total. There is **no supported partial payment over time** or remaining-balance calculation. `pay_order()` and `mark_order_paid()` make an order paid and deduct stock; neither automatically enqueues receipts. `mark_order_paid()` rechecks status under lock; `pay_order()` does not recheck status after acquiring its lock. Both parse amounts before locking/recomputing the total, creating stale-total and duplicate-submission risks.

## Inventory and valuation

`_deduct_order_stock()` aggregates product quantities, locks products, subtracts stock, consumes FIFO layers, writes sale movements, and sets `stock_deducted_at`. Repeating it on a marked order does nothing. Existing tests explicitly cover pay-later followed by payment without duplicate deduction. This marker does not protect payment creation itself or all edit paths.

Negative stock is deliberately allowed by sale deduction and confirmed in `StockLogicTests.test_mark_paid_allows_negative_stock`; adding out-of-stock and excessive quantities is also tested. Manual reductions in `adjust_product_stock()` reject reductions below zero. The inconsistency is existing behavior to discuss, not silently change.

`create_product()` can create opening stock/batch/movement; these writes are not wrapped in one explicit atomic block. `adjust_product_stock()` atomically locks product, adds priced batches on restock or consumes them on reduction, and writes reasoned movements. `products_page()` exposes product management; `stock_alerts_page()` filters low/out stock; `stock_movements_page()` filters movement history and computes stock-in/sold/remaining summaries.

Quantities support hundredths; no per-unit integrality, purchase/sale unit, conversion factor, or dimensional validation exists. `OrderItem.box` is just optional receipt text. `StockBatch` and cost snapshots support FIFO cost tracking, not a complete valuation ledger or supplier source report. `_restore_order_stock()`, refunds, and paid line quantity corrections update product quantities/movements **without restoring corresponding batch quantities**. `_ensure_legacy_batch()` creates a synthetic layer only when no positive layer exists; it cannot reconcile partial batch shortages. Backdated restocks still use batch ID ordering. These are material valuation and provenance risks.

## Returns, corrections, deletion, audit

`refund_order()` locks order/items, validates remaining quantities, creates Refund/RefundItem history, restores returned stock, and optionally queues the refund receipt for a configured device. Refunds return original selling prices; order-wide discount remains with retained items and final payout is capped to remaining paid sale total. This is deliberate and tested, not proportional discount allocation. A refund may exhaust the monetary balance before all quantities return; review zero-value return handling before expanding credit invoices.

`start_paid_order_correction()` restores stock and changes a paid order to unpaid while retaining payments. `save_paid_order_correction()` deletes/recreates payments with original earliest payment time, then deducts again. `correct_paid_order_item()` adjusts product stock directly and `_rebalance_order_payments()` changes payment amounts proportionally to corrected total. These are financially mutable workflows with no append-only correction audit.

`delete_order()` permits paid deletion, restores remaining stock when marked, explicitly deletes refunds/payments/print jobs and movement history, then deletes the order. `delete_empty_order` is an alias, not an empty-only guard. `delete_product()` removes product/batches/movements while item snapshots survive. `delete_expense()` permanently removes expenses. No formal invoice void/credit note or operator audit trail exists. Future issued documents must be protected from these existing generic paths.

## Documents and printing

Active receipt implementation is `pos/receipt.py` (42-column 80mm text) → `pos/printing.py` (PrintJob JSON snapshots) → `pos/printing_api.py` → `print_agent.py` (local USB ESC/POS, optional logo, cut, acknowledgments). `sale_receipt_text()` includes number, customer, line quantities/prices/box, discounts/totals, payment methods/bank/change. Profit report includes private cost/profit; customer grouping retains internal FIFO rows.

`reprint_orders()` searches paid/refunded orders by number; `view_order()` displays items, payments, refunds. `print_receipt()` POST enqueues only customer copy; rejects store copies and fully refunded orders. It does not require paid status, so direct requests can queue unpaid sales-style receipts. Refund receipts are distinct from sale receipts but no separate **invoice payment receipt** exists. Refund creation queues a customer copy only when a device is set. Daily/monthly bulk receipt printing filters paid orders.

`templates/pos/receipt.html` and `profit_receipt.html` contain browser `window.print()` layouts, but current print views queue jobs rather than render them. No routed A4 invoice/proforma/PO or PDF exporter exists. Ordinary browser printing is not evidence of completed A4 document support.

`docs/PRINTING_SETUP.md` is stale: it describes automatic customer/store copies; payment code and `payment_success.html` explicitly use manual printing and store copies have been removed. Preserve the actual current behavior unless changes are approved.

Print API requires `X-Device-Id` and configured shared `X-Print-Agent-Token`; poll claims targeted jobs first, then untargeted; printed/failed acknowledgments require claim ownership. Failure retries stop at configured attempt limit. No abandoned-claim timeout recovery is implemented. Physical print success followed by acknowledgment failure can cause duplicate paper output; reprints must never repost business transactions. SQLite and shared-token device isolation also require concurrency review.

## Frontend and route inventory

All POS routes are declared in `pos/urls.py`, rooted at `/`. They are HTML form views with selective AJAX JSON, not a versioned REST API. No serializers exist to extend.

| Routes | Functions and UI |
|---|---|
| `/`, `/order/<id>/`, `/new-order/` | `pos_home`, `create_new_order`; `pos.html`, `_product_results.html` |
| `/order/<id>/add-product/<product>/`, `add-custom-item/`, `remove-item/<item>/`, `update-item/<item>/`, `details/` | Add/edit/remove lines and details; POS forms and inline JS |
| `/order/<id>/delete/`, `delete-empty/` | `delete_order`, alias `delete_empty_order` |
| `/unpaid-orders/`, `/pay-later/<id>/`, `/resume-order/<id>/` | `unpaid_orders`, `pay_later`, `resume_order`; `unpaid_orders.html` |
| `/pay/<id>/`, `/mark-paid/<id>/` | `pay_order`, `mark_order_paid`; `payment.html`, `payment_success.html`, POS payment modal |
| `/reprint/`, `/order/<id>/view/` | `reprint_orders`, `view_order`; `reprint.html`, `order_view.html` |
| `/order/<id>/edit-paid/`, `save-paid-correction/`, `correct-paid-item/<item>/` | Paid correction functions; POS and order-view forms |
| `/print/<id>/<copy>/`, `/print-profit/<id>/`, `/refund/<id>/` | Receipt/profit queue and returns; `refund_confirm.html`, `refunded_notice.html` |
| `/products/`, `/products/create/`, `/products/<id>/edit/`, `delete/`, `stock/`, `barcode/` | Product CRUD, adjustments and barcode view; products/edit/barcode templates |
| `/stock-alerts/`, `/stock-movements/` | Stock filters/summaries and matching templates |
| `/expenses/`, `/expenses/create/`, `/expenses/<id>/delete/` | Expense list/create/delete and `expenses.html` |
| `/day/`, `/month/`, `/day/print-summary/`, `/day/print-receipts/`, `/month/print-summary/`, `/month/print-receipts/` | Daily/monthly reporting and queued reports; `day.html`, `month.html` |
| `/device/` | `device_setup`; session/cookie device ID, `device_setup.html` |
| `/api/printing/jobs/poll/`, `/api/printing/jobs/<id>/ack-printed/`, `ack-failed/` | `poll_jobs`, `ack_printed`, `ack_failed` JSON agent protocol |

Most mutations check POST inside functions. Exceptions include order creation and resume behavior reachable by GET; these should be included in future state-transition hardening. Existing AJAX response examples: payment `{ok, order_id, order_number, total, method, amount_received, change}`, errors `{ok:false,error}`; product create/delete/stock routes also selectively return JSON. Preserve current keys and browser redirects.

## Authentication and authorization

Django auth middleware/admin are installed, but POS functions have no login/role permission checks. Default admin authentication protects `/admin/` only. Device ID is printer routing, not identity. `pos/admin.py` registers Order with editable item inline, Payment, Product, Expense, StockMovement, PrintJob; Refund/RefundItem/StockBatch are not registered there. Generic admin writes bypass stock/payment services. No cashier/manager/accountant role implementation or record-level audit actor was found.

## Financial reports and supplier gaps

`day_view()` / `month_view()` select non-unpaid orders by any payment date in the period, use `distinct()`, sum each complete order total and profit, subtract period refund amount/profit, then subtract period expenses for net profit. `_payment_breakdown()` sums **all payments of selected orders**, cash less change, then deducts refunds and expenses by method. `pos/receipt.py` duplicates breakdown logic for printed reports. Bank reports combine every bank. Adding installments without changing these queries would count full invoice revenue and out-of-period payments repeatedly.

No supplier, purchase order, goods receipt, purchase obligation, supplier payment, or payable account models/routes/UI exist. Expense vendor existed only historically and was removed by `0015`; it is not supplier management. Restock layers know prices and quantities but not who supplied them. Current profit is sales margin less expenses, not a general ledger. Supplier settlement must not be inserted as a negative sale or double-counted as an expense after inventory cost is recognized.

## Existing automated coverage

`pos/tests.py` contains `ExpensePageTests`, `MonthSummaryTests`, `ProductSearchTests`, `CustomItemTests`, `DiscountAndCustomerTests`, `StockLogicTests`, `OrderDeleteTests`, and `OrderViewTests`. Passing tests cover report periods/pagination/refunds/expenses, customer discounts, receipts, pay-later deduction guards, Wave exact payment, FIFO cart splitting, negative stock, partial refunds and original-price discount treatment, manual stock adjustments, paid corrections/deletions, and read-only order viewing. No implemented tests establish proforma/invoice/supplier/unit workflows, multi-session races, authenticated POS roles, or physical/A4 printing. Some passing tests intentionally lock in risky existing behavior; future policy changes need approved replacement expectations.
