# The Master Plumber - Service Agreement & CRM v4

A small-business field app for The Master Plumber. It keeps customer/property/job history, builds flat-rate job pricing, captures a signed service agreement, supports independent sewer/water-line assessments, creates invoices/PDFs, and schedules customer email follow-ups.

## Current features

- Customer CRM with permanent customer/job history.
- Ordinary plumbing jobs and independent assessment jobs are kept distinct.
- Job pricing from the full service catalog in `data/service_catalog.csv`.
- Custom-job calculator using editable assumptions: labor benchmark, service-call overhead, parts markup, and target gross margin.
- Price & Scope screen that becomes the signed Agreement for Services.
- Customer signature capture and printable Agreement PDF.
- Independent-assessment terms preserve the no-repair/no-referral model for underground sewer and water-service-line work.
- Follow-up email scheduler and send-now action.
- Optional SMTP configuration through environment variables.
- Existing assessment reports, photos, invoices, CSV exports, backups, and login remain available.

## Pricing model

Catalog prices are the normal flat-rate benchmark. For custom work, the suggested price is calculated as:

`((labor hours x labor benchmark) + (parts cost x (1 + parts markup)) + service-call overhead) / (1 - target gross margin)`

The result is rounded up to the nearest $5. All assumptions are editable under Settings. A technician can override the suggested price before it becomes part of the agreement.

Default starting assumptions are the same model used in The Master Plumber flat-rate book: $175/hr labor benchmark, $110 service-call overhead, 40% parts markup, and 45% target gross margin. These are management assumptions, not a guarantee of market pricing, and should be reviewed periodically against current Denver-area costs and competition.

## Email follow-ups

The app stores follow-up date, subject, message, status, and sent date. Sending requires SMTP environment variables:

- `TMP_SMTP_HOST`
- `TMP_SMTP_PORT` (default 587)
- `TMP_SMTP_USER`
- `TMP_SMTP_PASSWORD`
- `TMP_SMTP_FROM`

Without SMTP settings, follow-ups can still be scheduled and tracked but the Send Now button will report that email is not configured.

## Run locally

```bash
pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:8000`.

## Legal review

The agreement language is a practical service-authorization template. Before using it as the production customer contract, have Colorado counsel review warranty, cancellation, lien, permit, collection, and consumer-disclosure language that may apply to the business and the specific jobs performed.

## Service agreement update
The agreement workflow now includes a fuller ordinary-plumbing agreement and a separate independent sewer/water-line assessment agreement. It records the approved scope, flat-rate price, change-order acknowledgment, limitations, electronic-signature consent, and customer signature, then generates a PDF copy.

The agreement intentionally preserves any nonwaivable customer rights and does not treat the standard agreement as an emergency waiver of a cancellation right. Colorado law can impose special requirements on certain home-solicitation/consumer-credit transactions, including cancellation rights and separate emergency documentation in applicable circumstances. Have Colorado counsel review the final production wording and your actual sales/payment practices before relying on it as a universal form for every transaction.


## Warranty policy

For ordinary plumbing work, the customer agreement includes a one-year labor/workmanship warranty from the date the authorized work is completed. Products, fixtures, water heaters, valves, appliances, and other equipment carry the applicable manufacturer warranty rather than a separate product warranty from The Master Plumber. The agreement separately explains common exclusions and requires the customer to acknowledge the warranty terms before signing.
