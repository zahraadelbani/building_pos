# Prioritized client clarification questions

Review date: 2026-11-09. These questions address unresolved policy, not whether code exists. Existing code already confirms manual payment recording, split checkout, stock deduction at pay-later, negative sales stock, and manual thermal printing. No gateway integration is assumed. Approval of this analysis does not supply missing business decisions.

## Critical — block the affected implementation or release

| ID | Question / recommended decision to discuss | Affected work |
|---|---|---|
| C1 | **CR-07 discussion required:** Do local purchase orders normally precede delivery? Approve separate PO and goods receipt, with stock added only for received quantities; if goods are already in hand, may staff use an explicit “create and receive now” action? | Local PO inventory design |
| C2 | What does “purchase order payments deducted from sales” mean in the desired report: cash available after supplier payments, profit, or another figure? Recommended: show supplier cash outflows separately and leave gross sales unchanged. | CR-07 accounting/reports |
| C3 | At which event is a supplier obligation recognized: accepted delivery, supplier invoice, or another agreed event? How are delivery/invoice cost differences handled? | Supplier obligations and CR-07 |
| C4 | May invoices be paid in installments on different dates, and should deposits/overpayments become customer credit or be rejected? Recommended starting scope: installments, no overpayment; invoice payment never issues stock again. | Settlement/balance model |
| C5 | Should reports recognize supplied sales at invoice issuance while showing collections by payment date? If a cash-basis management report is also wanted, should it be shown separately? Confirm how historic reports should remain comparable. | Invoice launch and reporting |
| C6 | Can proforma conversion change quantities/prices/discounts? Is conversion always one complete document, or may one proforma produce several partial invoices/sales? Recommended first scope: one full conversion, retained source, explicitly recorded agreed edits. | Proforma conversion constraints |
| C7 | What is each existing product's stock unit and product-specific carton/pair conversion, and are fractional PCS/Pair/Sheet allowed? What meter precision/rounding is needed? Provide a reviewed mapping; unknown legacy units remain unspecified. | Unit migration and procurement |
| C8 | Should sales continue allowing negative stock, which current code/tests explicitly permit, or block shortage at invoice issue/direct sale? If overrides are allowed, which role approves them? | Stock issue validation and rollout |
| C9 | For issued invoices/paid receipts, approve a correction policy: void/credit/reversal with reasons rather than destructive edits/deletions? Can goods be credited without being physically returned, and can unpaid returned goods reduce the debt without a money refund? | Lifecycle/audit and compatibility |
| C10 | How should existing unpaid bills be classified: drafts versus goods already supplied? Who will review ambiguous stock markers, balances, and opening supplier debts? | Safe historical backfill |

C1–C3 must be resolved through the explicitly requested CR-07 client discussion before that feature is implemented. Unrelated approved stages may proceed without waiting for CR-07.

## Important — determine business logic

| ID | Question | Why it matters |
|---|---|---|
| I1 | For cheques, does receipt of the cheque settle the invoice immediately, or only bank clearance? What information and bounced-cheque reversal process are required? | Payment state and date, cash/receivable reports |
| I2 | Are GTBank and Ecobank the only allowed future banks, or should users retain an “other bank” entry? Should transfers to both banks be split in one checkout? | Preserve legacy free text while designing new tender input |
| I3 | Should supplier payments be allocated manually to purchases or oldest obligation first? Are advances, partial payments, supplier returns/credits, or multiple currencies needed? | Payable allocation, balances and cash flow |
| I4 | Can a local PO be partially received, over-received with approval, or cancelled after a partial delivery? Is a link to a particular customer invoice mandatory or optional? | Procurement states and fulfillment references |
| I5 | What customer/company details must appear on documents: address, phone, contact, company registration/tax identifier? Are invoice due dates/payment terms required? | Party snapshots and receivable aging |
| I6 | What currency, tax treatment, and rounding rules must new documents use? Current totals have no tax/currency fields. | Financial totals and printed legal/business content |
| I7 | Do proformas need expiry dates or deposits, and must stock availability be checked without reserving it? | Quote lifecycle; no deduction is already required |
| I8 | Should an existing completed sale be given an invoice number/document on request? Recommended: same Order, no second sale or inventory posting. | Retrospective document workflow |
| I9 | Current discounted refunds return original item prices and cap payout to remaining sale balance. Keep this policy or approve proportional discount allocation? How should zero-value remaining goods returns be recorded? | Credit/refund compatibility |
| I10 | Which roles may issue documents, override prices/stock, record/reverse payments, receive stock, amend suppliers, view profit, and approve corrections? | POS currently lacks role enforcement |
| I11 | How many concurrent cashier terminals and which database/deployment are actually in use? Is changing the production database acceptable if serialization cannot meet requirements on SQLite? | Proving concurrent posting correctness |
| I12 | Confirm supplier names/contact/country details and supported purchase types; “Turkey/Tunis” and name spellings should be reviewed before entry. | Reliable supplier records and opening balances |
| I13 | Can issued document numbers have gaps, and are separate proforma/invoice/receipt/PO prefixes or annual resets required? | Atomic number allocation and historical references |

## Optional — usability and printing preferences

1. Is browser A4 print/save-as-PDF sufficient, or is an automatic server-generated downloadable PDF mandatory? Which actual A4 and thermal printers/paper widths must be supported?
2. Provide approved business header/logo/footer/signature fields and sample layouts for each document. Should payment receipts include all invoice items or just payment details and balance?
3. Keep current manual printing, or offer an optional automatic print after posting? Should print failures show a retry action? Reprinting will never repost stock or payments.
4. Should fully refunded/void documents remain reprintable with clear status labels? Current system blocks fully refunded sale reprints.
5. Preferred searches and filters beyond required document number: customer, supplier, date, status, outstanding amount?

Record decisions against these IDs before the dependent stage begins. Do not ask again whether Cash/Wave/APS, split tender, or fractional hundredths exist; those were verified in the repository. Do not ask whether proformas deduct stock or invoices deduct stock: the supplied requirements already answer both.
