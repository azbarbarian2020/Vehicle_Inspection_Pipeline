# Vehicle Inspection Pipeline

Automated pipeline that extracts data and images from Vector Fleet Management vehicle inspection PDFs, loads them into Snowflake, sends formatted email alerts, and provides a web dashboard for browsing results.

## Architecture

```
PDF lands on stage
    |
    v
@INSPECTION_PDFS (AUTO_REFRESH enabled)
    |
    v
PDF_STREAM (directory table stream)
    |
    v
QUEUE_NEW_PDFS (serverless task, 1-min schedule)
    |
    v
PROCESSING_QUEUE (table)
    |
    v
SPCS Service (background poller, every 30s)
    |
    +---> AI_EXTRACT (summary fields: company, fleet, unit#, etc.)
    +---> AI_PARSE_DOCUMENT (LAYOUT mode -> regex for P/F=F rows)
    +---> PyMuPDF (spatial Y-coordinate image extraction)
    |
    v
Tables: INSPECTION_SUMMARY, FAILED_LINE_ITEMS, FAILURE_IMAGES
Stage: @INSPECTION_IMAGES
    |
    v
GENERATE_INSPECTION_EMAIL (HTML with presigned image URLs)
    |
    v
Email to jason.drew@snowflake.com
```

## Components

### Snowflake Objects (VEHICLE_INSPECTIONS.PUBLIC)

| Object | Type | Purpose |
|--------|------|---------|
| INSPECTION_PDFS | Stage | Source PDFs. Directory table + AUTO_REFRESH enabled |
| INSPECTION_IMAGES | Stage | Extracted failure photos (PNG) |
| INSPECTION_SUMMARY | Table | One row per inspection (13 summary fields) |
| FAILED_LINE_ITEMS | Table | Only items with P/F = F |
| FAILURE_IMAGES | Table | Image metadata + stage paths |
| PROCESSING_QUEUE | Table | Tracks files pending/processed |
| PIPELINE_SETTINGS | Table | Configurable settings (email recipients, etc.) |
| PDF_STREAM | Stream | On directory table, detects new files |
| QUEUE_NEW_PDFS | Task | Consumes stream into queue (1-min) |
| INSPECTION_SERVICE | Service | SPCS container (FastAPI + PyMuPDF + React) |
| INSPECTION_POOL | Compute Pool | CPU_X64_XS, auto-suspend 5min |
| INSPECTION_EMAIL_INT | Integration | Email notification (configured recipients) |
| INSPECTION_EXTERNAL_ACCESS | Integration | Allows SPCS to reach Snowflake API + S3 |

### SPCS Service

- **Image**: `/vehicle_inspections/public/inspection_repo/inspection-pipeline:latest`
- **Endpoint**: https://fsamnbh-sfsenorthamerica-jdrew.snowflakecomputing.app
- **Stack**: FastAPI (Python 3.11) + React (Vite) + nginx + supervisord
- **Port**: 8080 (nginx reverse proxy)
  - `/api/*` -> FastAPI on port 8000
  - `/*` -> React static files

### Email Notifications

- Sent via `SYSTEM$SEND_SNOWFLAKE_NOTIFICATION` with HTML format
- Contains summary header per inspection, failed items with descriptions/comments, and inline photos via presigned URLs (7-day expiry)
- Only sends for new inspections (tracked by `EMAIL_SENT_AT` column)
- One email per batch of new inspections

## How to Use

### Adding New Inspection PDFs

Simply upload PDFs to the stage. Everything else is automatic.

**Via SQL (SnowSQL or Snowsight worksheet):**
```sql
PUT 'file:///path/to/Report (4).pdf' @VEHICLE_INSPECTIONS.PUBLIC.INSPECTION_PDFS AUTO_COMPRESS=FALSE;
```

**Via Snowsight UI:**
1. Navigate to Data > Databases > VEHICLE_INSPECTIONS > PUBLIC > Stages > INSPECTION_PDFS
2. Click "+ Files" and upload the PDF(s)

**What happens next (automatic):**
1. AUTO_REFRESH detects the new file (or stream picks it up within 1 minute)
2. Task queues the file for processing
3. SPCS service picks it up within 30 seconds
4. Extracts summary, failed items, and photos
5. Sends email alert
6. Dashboard updates

### Viewing Results

**Dashboard:** https://fsamnbh-sfsenorthamerica-jdrew.snowflakecomputing.app
- Inspection list with failure/image counts
- Click any inspection to see details + photos

**SQL:**
```sql
-- All inspections with counts
SELECT s.INSPECTION_NUM, s.COMPANY, s.UNIT_NUM, s.INSPECTOR,
       COUNT(DISTINCT f.ITEM_ID) as failures,
       COUNT(DISTINCT i.IMAGE_ID) as images
FROM VEHICLE_INSPECTIONS.PUBLIC.INSPECTION_SUMMARY s
LEFT JOIN VEHICLE_INSPECTIONS.PUBLIC.FAILED_LINE_ITEMS f ON s.INSPECTION_ID = f.INSPECTION_ID
LEFT JOIN VEHICLE_INSPECTIONS.PUBLIC.FAILURE_IMAGES i ON s.INSPECTION_ID = i.INSPECTION_ID
GROUP BY 1,2,3,4 ORDER BY s.INSPECTION_NUM;

-- Failed items for a specific inspection
SELECT f.LINE_NUM, f.DESCRIPTION, f.COMMENTS
FROM FAILED_LINE_ITEMS f
JOIN INSPECTION_SUMMARY s ON f.INSPECTION_ID = s.INSPECTION_ID
WHERE s.INSPECTION_NUM = '299047'
ORDER BY f.LINE_NUM;

-- Get presigned URLs for failure images
SELECT f.LINE_NUM, f.DESCRIPTION,
       GET_PRESIGNED_URL(@INSPECTION_IMAGES, i.STAGE_PATH, 86400) as photo_url
FROM FAILED_LINE_ITEMS f
JOIN FAILURE_IMAGES i ON f.ITEM_ID = i.ITEM_ID
JOIN INSPECTION_SUMMARY s ON f.INSPECTION_ID = s.INSPECTION_ID
WHERE s.INSPECTION_NUM = '299047';
```

### Reprocessing a Report

If you need to reprocess a report (e.g., after a bug fix):
```sql
-- Delete existing data
DELETE FROM FAILURE_IMAGES WHERE INSPECTION_ID = (SELECT INSPECTION_ID FROM INSPECTION_SUMMARY WHERE FILE_NAME = 'Report (X).pdf');
DELETE FROM FAILED_LINE_ITEMS WHERE INSPECTION_ID = (SELECT INSPECTION_ID FROM INSPECTION_SUMMARY WHERE FILE_NAME = 'Report (X).pdf');
DELETE FROM INSPECTION_SUMMARY WHERE FILE_NAME = 'Report (X).pdf';

-- Re-queue for processing
INSERT INTO PROCESSING_QUEUE (FILE_PATH) VALUES ('Report (X).pdf');
```

### Manually Triggering Email

```sql
CALL VEHICLE_INSPECTIONS.PUBLIC.GENERATE_INSPECTION_EMAIL();
```

### Configuring Email Recipients

Email recipients are stored in the `PIPELINE_SETTINGS` table and can be managed via the dashboard or SQL.

**Via Dashboard:**
1. Go to the dashboard URL
2. Click **Settings** (top-right)
3. Edit the email recipients field (comma-separated for multiple)
4. Click **Save Settings**

**Via SQL:**
```sql
UPDATE VEHICLE_INSPECTIONS.PUBLIC.PIPELINE_SETTINGS 
SET SETTING_VALUE = 'user1@company.com, user2@company.com'
WHERE SETTING_KEY = 'email_recipients';
```

**Adding a new recipient requires TWO steps:**

1. Update the settings (dashboard or SQL above)
2. Add the address to the notification integration's allowed list:
```sql
ALTER NOTIFICATION INTEGRATION INSPECTION_EMAIL_INT 
SET ALLOWED_RECIPIENTS = ('user1@company.com', 'user2@company.com');
```

**Important email behavior:**
- All recipients must be verified Snowflake users in the same account (verify email in Snowsight under user profile)
- If ANY address in the recipient list is not in `ALLOWED_RECIPIENTS` or not verified, **NO email is sent to anyone** — it's all-or-nothing
- Always verify a new address works before adding it to production by testing with just that address first
- Emails are sent from `no-reply@snowflake.net` (AWS accounts)
- Presigned image URLs in emails expire after 7 days — use the dashboard for persistent access

## Technical Details

### PDF Extraction Strategy

1. **Summary Fields** (AI_EXTRACT): Extracts 13 fields (Company, Fleet, Location, Serial#, Unit#, Model#, Inspector, Order Date, Complete Date, Trouble Ticket, Inspection#, Status, Invoice#) using natural language questions against the PDF.

2. **Failed Line Items** (AI_PARSE_DOCUMENT + regex): Parses the full document in LAYOUT mode (returns Markdown tables), then uses regex `|\s+(\d+\.\d+\w*)\s+|([^|]+)|\s*F\s*|([^|]*)|` to find rows where P/F column = F.

3. **Image Extraction** (PyMuPDF): Uses `page.get_text('dict')` to get spatially-positioned text blocks and image blocks. Sorts by Y-coordinate to determine which images follow which line numbers. This is the only reliable method — both `AI_PARSE_DOCUMENT extract_images` and `pypdf` failed to correctly map images.

### Key Design Decisions

- **PyMuPDF in SPCS** (not Snowflake stored procedure): PyMuPDF is not available in Snowflake's Python runtime. pypdf is available but lacks spatial coordinates needed for image-line mapping.
- **Presigned URL for PDF download**: SPCS containers can't use the `GET` command to download from stages directly. Instead, we generate a presigned URL and download via HTTP.
- **PUT for image upload**: Works from SPCS once S3 host is added to the network rule.
- **Two-task pattern for stream consumption**: A DML statement (INSERT INTO queue FROM stream) is required to advance the stream offset. Stored procedure calls don't consume streams.

### Network Configuration

The SPCS container needs egress to:
- `sfsenorthamerica-jdrew.snowflakecomputing.com:443` (Snowflake API for SQL + AI functions)
- `sfc-prod3-ds1-46-customer-stage.s3.amazonaws.com:443` (S3 for stage file access)
- `sfc-prod3-ds1-46-customer-stage.s3.us-west-2.amazonaws.com:443` (S3 regional)

### Service Management

```sql
-- Check status
SELECT SYSTEM$GET_SERVICE_STATUS('VEHICLE_INSPECTIONS.PUBLIC.INSPECTION_SERVICE');

-- View logs
SELECT SYSTEM$GET_SERVICE_LOGS('VEHICLE_INSPECTIONS.PUBLIC.INSPECTION_SERVICE', 0, 'inspection-service', 50);

-- Restart (after image update)
ALTER SERVICE VEHICLE_INSPECTIONS.PUBLIC.INSPECTION_SERVICE FROM SPECIFICATION $$ ... $$;

-- Get endpoint URL
SHOW ENDPOINTS IN SERVICE VEHICLE_INSPECTIONS.PUBLIC.INSPECTION_SERVICE;
```

### Rebuilding the Container

```bash
cd /Users/jdrew/coco_projects/poc_email_agent

# Login to registry
snow spcs image-registry login --connection jdrew

# Build for linux/amd64
docker buildx build --platform linux/amd64 \
  -t sfsenorthamerica-jdrew.registry.snowflakecomputing.com/vehicle_inspections/public/inspection_repo/inspection-pipeline:latest \
  --load .

# Push
docker push sfsenorthamerica-jdrew.registry.snowflakecomputing.com/vehicle_inspections/public/inspection_repo/inspection-pipeline:latest

# Restart service (ALTER preserves the URL)
# Run the ALTER SERVICE SQL from Snowsight or SnowSQL
```

## Project Files

```
poc_email_agent/
├── app/
│   ├── main.py              # FastAPI backend + queue poller + REST API
│   ├── image_extractor.py   # PyMuPDF spatial image extraction logic
│   └── requirements.txt     # Python dependencies
├── frontend/
│   ├── src/
│   │   ├── main.jsx         # React entry point with routing
│   │   ├── App.jsx          # Inspection list page
│   │   ├── InspectionDetail.jsx  # Detail view with photos
│   │   └── index.css        # Tailwind CSS
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   └── index.html
├── Dockerfile               # Multi-stage build (Node + Python + nginx)
├── nginx.conf               # Reverse proxy (8080 -> backend/frontend)
├── spec.yaml                # SPCS service specification
├── extract_inspections.py   # Legacy local script (superseded by SPCS)
└── extract_images.py        # Legacy local PyMuPDF script (superseded by SPCS)
```

## Costs

- **Compute Pool** (CPU_X64_XS): ~1 credit/hour when active. Auto-suspends after 5 minutes of inactivity.
- **AI_EXTRACT**: ~970 tokens per page. Each PDF is ~16 pages = ~15,500 tokens per document.
- **AI_PARSE_DOCUMENT**: Billed per page. ~16 pages per document.
- **Storage**: Minimal. Each inspection produces ~5-15 PNG images (~1-2 MB each).

## Limitations

- Presigned URLs in emails expire after 7 days. Use the dashboard for persistent access.
- Compute pool cold start takes ~60 seconds if fully suspended.
- AUTO_REFRESH on internal stages is a preview feature on AWS. If unreliable, the QUEUE_NEW_PDFS task (1-min schedule) serves as backup.
- Maximum 50 images per document for AI_PARSE_DOCUMENT (not used for final solution, but noted).
