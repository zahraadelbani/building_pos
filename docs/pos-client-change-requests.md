# Client change requests and proposed business rules

Review date: 2026-11-09. Planning only; no features implemented. Evidence is catalogued in [existing-system analysis](pos-existing-system-analysis.md); delivery work is in [implementation plan](pos-implementation-plan.md). Proposed rules below require the decisions identified in [client questions](pos-client-clarification-questions.md).

## CR-01 — Sales payment methods

Required: Cash, Cheque, Wave, APS, bank transfer to GTBank, bank transfer to Ecobank. Existing Cash/Wave/APS/bank recording and checkout split tender must remain functional. `Payment.PAYMENT_METHODS`, `_parse_payments_from_post()`, POS/payment templates, and report breakdowns implement four hardcoded methods. No gateway integration is evidenced or requested.

Extend with cheque recording and optional reference metadata; retain `method=bank` and separate bank field. Offer GTBank/Ecobank selections while preserving historical free-text names. Prefer a small shared method definition over introducing an administration subsystem without need. If banks become managed records, add nullable bank FK/code alongside original text and retain the original display snapshot. Never merge historical names without reviewed mapping.

Support multiple tender rows and later invoice installments through one receipt event with payment rows. Same-event split tender is already supported; installments are new. Net cash collection is tender less change. A bank payment row has its own bank; new input should allow GTBank and Ecobank within the same event, which the current single bank input cannot express. Cheque received/cleared/bounced policy is unresolved. Reports need method and bank breakdowns with receipts, refunds, expenses, and supplier settlements distinguished.

## CR-02 — Proforma invoice

Required: customer/company identity, items/quantities/prices, existing order-wide discounts, unique number, persistence/search, A4 print/export, conversion to invoice or paid direct sale, and retained source document. No implemented quotation/proforma model/workflow exists. An unpaid Order is unsuitable: pay-later deducts stock and resume reverses it.

Propose separate `Proforma` / `ProformaItem` records reusing calculation/validation patterns. Draft → issued → converted (or cancelled/expired if agreed). An issued snapshot stores party details, item names, unit/factor/price and discount; no inventory or sales posting. Conversion locks the proforma, validates current stock/policies, creates one canonical Order, and records a unique conversion link. Invoice conversion issues it unpaid; direct-sale conversion also records full settlement. Both retain the original proforma. Conversion edits, expiry, deposits and partial/multiple conversions are decision points; default design is one complete conversion with explicitly recorded differences if edits are approved.

## CR-03 — Invoice management

Required: direct invoice or proforma conversion; customer/company; unique invoice number; issue, search, A4, status/outstanding balance; issuance deducts stock; payment may follow. Existing inactive unpaid orders already implement stock-at-pay-later and later settlement without another deduction, but lack formal identity/issuance/balance workflow.

Extend `Order` as the single supplied-sales transaction; add explicit document kind, lifecycle/issuance timestamp, unique invoice number, party snapshot, and optional proforma origin. Keep legacy order numbers/status contract. Separate document lifecycle (draft/issued/void) from settlement (unpaid/partial/paid) and return/credit state; avoid overloading the legacy three-value status. One issue operation posts inventory once; payment operations never post stock. Issue freezes financial lines; amendments use linked audited corrections/credits, not `resume_order()` or destructive paid correction.

Outstanding = issued invoice total − valid credits − net allocated customer settlements, with reversals restoring balances. Reject accidental overpayment; customer-credit handling needs agreement. A cancellation before issue has no stock effect; after issue, reverse quantities only when goods are returned or supply did not occur. Money refunds and goods returns must be independently representable. Existing paid sales may receive an invoice representation referencing the same Order; never create another sale or movement. Whether retrospective invoice issuance is needed remains open.

## CR-04 — Receipts and direct sales

Required: direct sale, approved proforma paid sale, and invoice payment receipts; small/A4 formats; unique retrieval/reprint references; originating transaction and method/bank/reference details. `sale_receipt_text()` and queue already print sales and refunds, but no separately numbered customer settlement receipt exists.

Retain Order-based sale receipts and add persistent `PaymentReceipt` event identity linked to Order and its Payment rows. Direct sale: issue once + settle once in one database transaction. Invoice settlement: append payments/receipt only. Proforma direct sale: convert once + issue + settle. Receipt records show amount received this event, cash change, net amount applied, invoice reference, previous/current balance, method/bank/reference, and immutable party/document data. A sale receipt states items/sale total; a payment receipt proves a particular collection and must not imply a new sale.

Reuse thermal renderer/queue with a new payment receipt job type and event IDs. Add routed A4 templates and print CSS; browser print/save-as-PDF is the lowest-dependency export option, subject to acceptance. Server-generated downloadable PDFs would be separate agreed work. Reprinting reads snapshots and creates print jobs only. Never expose internal costs/profit on customer documents. Fully refunded historical documents should remain viewable and visibly labelled; whether to permit their reprint needs confirmation.

## CR-05 — Inventory units

Required vocabulary: CTN (Carton), PCS (Piece), Pair, Sheet, Meter. Current decimal stock and free-text box do not implement structured units or conversion.

Propose `Unit` and per-product `ProductUnit` conversion rows. Each product has one base stock unit; factor means base units per selected unit. A product's CTN factor is configurable, e.g. 12 PCS for one product and 24 for another. Purchase and sale defaults can differ. Pair/Sheet/Meter are not universally interchangeable dimensions; permit only explicitly compatible product mappings. No global carton or automatic meter-to-piece assumption.

Persist selected unit, factor snapshot, entered quantity, normalized base quantity, price per selected unit, and cost per base unit on transaction lines. Example: purchase 2 CTN at 120 each with factor 12 adds 24 PCS at base cost 10; sell 3 PCS at 15 deducts 3, leaving 21. Later factor edits cannot change these historical quantities or values. Inventory and FIFO use normalized quantities; UI/docs use entered units. Configure fractional permission per unit/product and precision/rounding explicitly. Current hundredths may be inadequate for some meter conversions.

Legacy rows retain implicit factor 1 and original quantities/prices/box text; unknown legacy unit stays labelled legacy/unspecified until reviewed. Never guess PCS from names or rescale existing balances. Base-unit changes for stocked products require a controlled reconciliation rather than editing a dropdown.

## CR-06 — Suppliers and obligations

Required: profiles, purchase sources, obligations, settlements, credits, outstanding balance, and transaction history. None is implemented. Retain names/countries supplied as examples, pending spelling confirmation: Ali Salehne—China; Mahre—Turkey/Tunis; Yuproon—China; Ndongo—Senegal. Do not silently seed assumed identities or geographical values.

Propose `Supplier`, `Purchase` / `PurchaseItem`, `SupplierPayment`, payment allocations, and explicit credit/adjustment records. Supplier balance = posted purchase obligations + debit adjustments − allocated payments − credits, with void/reversal effects recorded and unapplied advances shown separately. Opening balances are dated reviewed ledger entries with provenance, not editable profile balances. Supplier payments settle liabilities; inventory acquisition cost becomes cost of goods sold when goods sell, under the agreed accounting treatment.

Purchase/receipt lines link supplier to stock batches; one Product may originate from many suppliers over time. Preserve source documents, per-delivery unit/cost snapshots, and protected relationships. No permanent Product.supplier exclusivity. Support standalone purchases before CR-07 if supplier accounting is approved. Payment allocation across invoices and advance/currency policies need decisions.

## CR-07 — Local purchase orders: client clarification required

**Discussion with the client is explicitly required before implementation.** No PO or payable workflow exists to reuse directly; stock adjustments/batches are useful primitives, not a complete purchase flow.

Recommended sequence:

1. Create numbered local PO, select supplier, enter product/unit/quantity/cost, optional customer demand link; save/search/A4 print. This authorizes procurement, with no physical stock change.
2. Record goods receipt against PO; accept partial receipts and reject excess unless approved. Only received quantities create batches and positive stock movements once.
3. Post supplier obligation at the agreed receipt/supplier-bill milestone, referencing receipt/purchase. A PO commitment alone is not silently treated as the final bill.
4. Record supplier settlement and allocations; reduce payable and the chosen cash/bank balance, retain history and printable reference. Handle advances separately if needed.
5. Fulfill the customer through the normal single Order issue/sale flow. Reconcile cancellations, returns, short deliveries and supplier credits with linked reversals.

Client conflict: automatically adding goods on PO creation creates fictitious available stock if not received. Alternative: a clearly labelled **create and receive now** action requiring actual delivery confirmation, atomically creating PO + receipt + stock + agreed obligation. Ordinary PO creation still does not add stock. A third option is displaying incoming stock separately from on-hand, never selling it as received stock by accident.

Client conflict: supplier payments being “deducted from sales” should mean a cash-outflow/cash-available report if that is the intent. Do not reduce gross sales or create a negative sale. Do not count payment again as operating expense when purchased stock cost is already recognized. A provisional management cash report can show collections less expenses and supplier settlements, with revenue/profit shown separately. Confirm intended report and obligation timing before CR-07 begins.

## Cross-feature invariants

| Event | Stock | Revenue / receivable | Cash / payable | Traceability |
|---|---|---|---|---|
| Issue proforma | None | None | None | Retain issued snapshot |
| Issue unpaid invoice | Decrease once | Recognize supplied sale under approved report policy; create receivable | No collection | Order + invoice number + posting identity |
| Direct paid sale | Decrease once | One sale, settled receivable | Collection | Same Order, receipt, payments |
| Invoice installment | None | Reduce receivable only | Collection | Payment receipt linked to existing Order |
| Print/reprint | None | None | None | Document/event snapshot, PrintJob |
| PO creation | None recommended | None | Commitment only | Supplier + PO |
| Receive purchase | Increase once | Inventory asset/cost layer | Obligation at agreed milestone | Receipt + purchase + supplier + batch |
| Supplier settlement | None | No reduction to sales | Cash out, payable down | SupplierPayment and allocations |
| Return/credit/reversal | Only actual physical return/reversal quantity | Explicit credit/refund accounting | Money only when settled | Reference original lines/postings |

Atomic issue/conversion/receipt/settlement services must lock relevant parent then products/batches in deterministic ID order, recompute balances under lock, validate current state, and enforce unique posting/conversion/request keys at database level. Rollback must leave no partial movement, obligation or receipt. Preserve prices, factors, method/bank names, party details and timestamps historically. UI double-click protection complements server idempotency; it cannot replace it. SQLite's current locking does not establish row-lock concurrency guarantees; prove a supported serialization strategy or agree a database change before multi-cashier rollout.
