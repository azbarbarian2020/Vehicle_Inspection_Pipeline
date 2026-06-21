"""
Vehicle Inspection Pipeline - FastAPI Backend
Handles PDF processing (AI_EXTRACT + AI_PARSE_DOCUMENT + PyMuPDF images),
background queue polling, and REST API for the React dashboard.
"""
import os
import re
import json
import time
import tempfile
import asyncio
import logging
from io import BytesIO
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
import snowflake.connector
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

from image_extractor import extract_failure_images

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# Suppress noisy OCSP/urllib3 warnings in SPCS
logging.getLogger("snowflake.connector.vendored.urllib3").setLevel(logging.ERROR)
logging.getLogger("snowflake.connector.ocsp_snowflake").setLevel(logging.ERROR)

# Snowflake connection config from environment
SF_ACCOUNT = os.environ.get("SNOWFLAKE_ACCOUNT", "")
SF_USER = os.environ.get("SNOWFLAKE_USER", "")
SF_WAREHOUSE = os.environ.get("SNOWFLAKE_WAREHOUSE", "INSPECTION_WH")
SF_DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "VEHICLE_INSPECTIONS")
SF_SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "PUBLIC")
SF_PRIVATE_KEY = os.environ.get("SNOWFLAKE_PRIVATE_KEY", "")

POLL_INTERVAL = 30  # seconds

# Cached database name (discovered at first connection in OAuth/token mode)
_discovered_database = None


def get_private_key_bytes():
    """Parse the PEM private key from environment variable."""
    key_str = SF_PRIVATE_KEY.replace("\\n", "\n")
    if not key_str:
        return None
    private_key = serialization.load_pem_private_key(
        key_str.encode(), password=None, backend=default_backend()
    )
    return private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def get_connection():
    """Create a Snowflake connection. Supports OAuth token (SPCS) and key-pair auth."""
    global _discovered_database

    db = SF_DATABASE or _discovered_database
    schema = SF_SCHEMA

    # In SPCS context, use OAuth token if available
    oauth_token_path = "/snowflake/session/token"
    if os.path.exists(oauth_token_path):
        with open(oauth_token_path) as f:
            token = f.read().strip()
        conn = snowflake.connector.connect(
            host=os.environ.get("SNOWFLAKE_HOST", f"{SF_ACCOUNT.lower()}.snowflakecomputing.com"),
            account=SF_ACCOUNT,
            authenticator="oauth",
            token=token,
            warehouse=SF_WAREHOUSE,
            database=db if db else None,
            schema=schema,
        )
        # Ensure warehouse and schema are set in the session
        cur = conn.cursor()
        cur.execute(f"USE WAREHOUSE {SF_WAREHOUSE}")
        if not db:
            cur.execute("SELECT CURRENT_DATABASE()")
            _discovered_database = cur.fetchone()[0]
            db = _discovered_database
            logger.info(f"Discovered database: {_discovered_database}")
        cur.execute(f"USE SCHEMA {db}.{schema}")
        cur.close()
    elif SF_PRIVATE_KEY:
        pk_bytes = get_private_key_bytes()
        conn = snowflake.connector.connect(
            account=SF_ACCOUNT,
            user=SF_USER,
            private_key=pk_bytes,
            warehouse=SF_WAREHOUSE,
            database=db if db else "VEHICLE_INSPECTIONS",
            schema=schema,
        )
    else:
        # Fallback: use connection name from environment or default
        conn_name = os.environ.get("SNOWFLAKE_CONNECTION_NAME", "")
        if conn_name:
            conn = snowflake.connector.connect(
                connection_name=conn_name,
                database=db if db else "VEHICLE_INSPECTIONS",
                schema=schema,
            )
        else:
            raise RuntimeError(
                "No authentication method available. Set SNOWFLAKE_PRIVATE_KEY env var, "
                "SNOWFLAKE_CONNECTION_NAME, or run inside SPCS."
            )
    return conn


def process_single_pdf(file_path: str) -> dict:
    """Process a single PDF: extract summary, failed items, and images."""
    logger.info(f"Processing: {file_path}")
    conn = get_connection()
    cur = conn.cursor()
    result = {"file": file_path, "status": "success", "errors": []}

    try:
        # Check if already processed
        cur.execute(
            f"SELECT COUNT(*) FROM INSPECTION_SUMMARY WHERE FILE_NAME = %s",
            (file_path,),
        )
        if cur.fetchone()[0] > 0:
            result["status"] = "skipped"
            return result

        # Step 1: AI_EXTRACT for summary fields
        extract_sql = f"""
        SELECT AI_EXTRACT(
            file => TO_FILE('@INSPECTION_PDFS', '{file_path}'),
            responseFormat => {{
                'company': 'What is the Company name from the Summary Information section?',
                'fleet': 'What is the Fleet from the Summary Information section?',
                'location': 'What is the Location from the Summary Information section?',
                'serial_num': 'What is the Serial # from the Summary Information section?',
                'unit_num': 'What is the Unit # from the Summary Information section?',
                'model_num': 'What is the Model # from the Summary Information section?',
                'inspector': 'Who is the Inspector from the Summary Information section?',
                'order_date': 'What is the Order Date from the Summary Information section?',
                'complete_date': 'What is the Complete Date from the Summary Information section?',
                'trouble_ticket': 'What is the Trouble Ticket from the Summary Information section?',
                'inspection_num': 'What is the Inspection # from the Summary Information section?',
                'status': 'What is the Status from the Summary Information section?',
                'invoice_num': 'What is the Invoice # from the Summary Information section?'
            }}
        )::VARCHAR as result
        """
        cur.execute(extract_sql)
        summary = json.loads(cur.fetchone()[0]).get("response", {})
        logger.info(f"  Summary extracted: Inspection #{summary.get('inspection_num')}")

        # Step 2: AI_PARSE_DOCUMENT for failed items
        layout_sql = f"""
        SELECT AI_PARSE_DOCUMENT(
            TO_FILE('@INSPECTION_PDFS', '{file_path}'),
            {{'mode': 'LAYOUT', 'page_split': true}}
        )::VARCHAR as result
        """
        cur.execute(layout_sql)
        pages = json.loads(cur.fetchone()[0]).get("pages", [])

        failed_items = []
        for page in pages:
            content = page.get("content", "")
            for match in re.finditer(
                r"\|\s+(\d+\.\d+\w*)\s+\|([^|]+)\|\s*F\s*\|([^|]*)\|", content
            ):
                ln = match.group(1).strip()
                desc = match.group(2).strip()
                comm = match.group(3).strip()
                comm = re.sub(r"^\s*-\s*", "", comm)
                comm = re.sub(r"\s*-\s*$", "", comm)
                comm = comm.strip() if comm.strip() and comm.strip() != "-" else None
                failed_items.append(
                    {"line_num": ln, "description": desc, "comments": comm}
                )

        result["failed_count"] = len(failed_items)
        logger.info(f"  Found {len(failed_items)} failed items")

        # Step 3: Insert summary
        cur.execute("SELECT UUID_STRING()")
        inspection_id = cur.fetchone()[0]

        raw_json = json.dumps(summary).replace("'", "''")
        cur.execute(
            """INSERT INTO INSPECTION_SUMMARY
            (INSPECTION_ID, FILE_NAME, COMPANY, FLEET, LOCATION, SERIAL_NUM, UNIT_NUM,
             MODEL_NUM, INSPECTOR, ORDER_DATE, COMPLETE_DATE, TROUBLE_TICKET,
             INSPECTION_NUM, STATUS, INVOICE_NUM, RAW_EXTRACT)
            SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, PARSE_JSON(%s)""",
            (
                inspection_id,
                file_path,
                summary.get("company"),
                summary.get("fleet"),
                summary.get("location"),
                summary.get("serial_num"),
                summary.get("unit_num"),
                summary.get("model_num"),
                summary.get("inspector"),
                summary.get("order_date"),
                summary.get("complete_date"),
                summary.get("trouble_ticket"),
                summary.get("inspection_num"),
                summary.get("status"),
                summary.get("invoice_num"),
                raw_json,
            ),
        )

        # Step 4: Insert failed items
        item_ids = {}
        for item in failed_items:
            cur.execute("SELECT UUID_STRING()")
            item_id = cur.fetchone()[0]
            item_ids[item["line_num"]] = item_id
            cur.execute(
                """INSERT INTO FAILED_LINE_ITEMS
                (ITEM_ID, INSPECTION_ID, LINE_NUM, DESCRIPTION, COMMENTS)
                VALUES (%s, %s, %s, %s, %s)""",
                (
                    item_id,
                    inspection_id,
                    item["line_num"],
                    item["description"],
                    item["comments"],
                ),
            )

        # Step 5: Download PDF and extract images with PyMuPDF
        failed_line_nums = set(item["line_num"] for item in failed_items)

        # Download PDF via presigned URL (GET command can't reach S3 from SPCS)
        import requests as http_requests
        cur.execute(f"SELECT GET_PRESIGNED_URL(@INSPECTION_PDFS, '{file_path}', 3600)")
        presigned_url = cur.fetchone()[0]

        local_pdf = f"/tmp/inspection_dl/{file_path.replace(' ', '_')}"
        os.makedirs("/tmp/inspection_dl", exist_ok=True)

        try:
            resp = http_requests.get(presigned_url, timeout=120)
            resp.raise_for_status()
            with open(local_pdf, 'wb') as f_out:
                f_out.write(resp.content)
            logger.info(f"  Downloaded PDF: {len(resp.content)} bytes")
        except Exception as dl_err:
            logger.error(f"  PDF download failed: {dl_err}")
            local_pdf = None
            result["errors"].append(f"Download failed: {dl_err}")

        image_count = 0
        if local_pdf and os.path.exists(local_pdf):
            images = extract_failure_images(local_pdf, failed_line_nums)

            for line_num, img_list in images.items():
                for img in img_list:
                    # Write to temp file and PUT to stage
                    with tempfile.NamedTemporaryFile(
                        suffix=f".{img['ext']}", delete=False, dir="/tmp"
                    ) as tmp:
                        tmp.write(img["data"])
                        tmp_path = tmp.name

                    try:
                        stage_dir = f"@INSPECTION_IMAGES/{inspection_id}/{line_num}/"
                        cur.execute(
                            f"PUT 'file://{tmp_path}' '{stage_dir}' AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
                        )
                        basename = os.path.basename(tmp_path)
                        rel_path = f"{inspection_id}/{line_num}/{basename}"

                        item_id = item_ids.get(line_num)
                        cur.execute("SELECT UUID_STRING()")
                        img_id = cur.fetchone()[0]

                        cur.execute(
                            """INSERT INTO FAILURE_IMAGES
                            (IMAGE_ID, ITEM_ID, INSPECTION_ID, LINE_NUM, STAGE_PATH, IMAGE_FORMAT, IMAGE_SEQUENCE)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                            (
                                img_id,
                                item_id,
                                inspection_id,
                                line_num,
                                rel_path,
                                img["ext"],
                                img["seq"],
                            ),
                        )
                        image_count += 1
                    finally:
                        os.unlink(tmp_path)

            # Cleanup downloaded PDF
            os.unlink(local_pdf)
        else:
            result["errors"].append(f"Could not download PDF to local: {file_path}")

        result["image_count"] = image_count
        result["inspection_id"] = inspection_id
        logger.info(f"  Uploaded {image_count} images")

        conn.commit()
    except Exception as e:
        result["status"] = "error"
        result["errors"].append(str(e))
        logger.error(f"  Error: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

    return result


def send_inspection_email():
    """Generate and send email for unnotified inspections."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Check if email recipients are configured
        cur.execute("SELECT SETTING_VALUE FROM PIPELINE_SETTINGS WHERE SETTING_KEY = 'email_recipients'")
        row = cur.fetchone()
        if not row or not row[0]:
            return "No recipients configured"

        recipients = row[0]

        # Get unnotified inspections
        cur.execute("""
            SELECT s.INSPECTION_NUM, s.COMPANY, s.FLEET, s.UNIT_NUM, s.SERIAL_NUM,
                   s.INSPECTOR, s.STATUS, s.ORDER_DATE, s.COMPLETE_DATE,
                   s.FILE_NAME, s.INSPECTION_ID
            FROM INSPECTION_SUMMARY s
            WHERE s.EMAIL_SENT_AT IS NULL
            ORDER BY s.PROCESSED_AT
        """)
        inspections = cur.fetchall()
        if not inspections:
            return "No new inspections to notify"

        # Build HTML email
        html = """<html><body style="font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px;">
        <h1 style="color: #333; border-bottom: 2px solid #0066cc; padding-bottom: 10px;">Vehicle Inspection Report</h1>"""

        for insp in inspections:
            insp_num, company, fleet, unit, serial, inspector, status, order_date, complete_date, file_name, insp_id = insp
            html += f"""
            <div style="background: #f5f5f5; border: 1px solid #ddd; border-radius: 5px; padding: 15px; margin: 20px 0;">
                <h2 style="color: #0066cc; margin-top: 0;">Inspection #{insp_num}</h2>
                <table style="width: 100%; border-collapse: collapse;">
                    <tr><td style="padding: 5px;"><strong>Company:</strong> {company or ''}</td>
                        <td style="padding: 5px;"><strong>Fleet:</strong> {fleet or ''}</td></tr>
                    <tr><td style="padding: 5px;"><strong>Unit #:</strong> {unit or ''}</td>
                        <td style="padding: 5px;"><strong>Serial #:</strong> {serial or ''}</td></tr>
                    <tr><td style="padding: 5px;"><strong>Inspector:</strong> {inspector or ''}</td>
                        <td style="padding: 5px;"><strong>Status:</strong> {status or ''}</td></tr>
                    <tr><td style="padding: 5px;"><strong>Order Date:</strong> {order_date or ''}</td>
                        <td style="padding: 5px;"><strong>Complete Date:</strong> {complete_date or ''}</td></tr>
                </table>
            </div>
            """
            # Get failed items with images
            cur.execute("""
                SELECT f.LINE_NUM, f.DESCRIPTION, f.COMMENTS
                FROM FAILED_LINE_ITEMS f WHERE f.INSPECTION_ID = %s ORDER BY f.LINE_NUM
            """, (insp_id,))
            items = cur.fetchall()
            if items:
                html += "<h3 style='color: #cc3300;'>Failed Items</h3>"
                for item in items:
                    ln, desc, comments = item
                    html += f"""<div style="border-left: 4px solid #cc3300; padding: 10px 15px; margin: 10px 0; background: #fff5f5;">
                        <strong>{ln} - {desc or ''}</strong>"""
                    if comments:
                        html += f"<p style='color: #666; margin: 5px 0;'>{comments}</p>"
                    # Get images for this line
                    cur.execute("""
                        SELECT GET_PRESIGNED_URL(@INSPECTION_IMAGES, STAGE_PATH, 604800) as url
                        FROM FAILURE_IMAGES WHERE INSPECTION_ID = %s AND LINE_NUM = %s
                        ORDER BY IMAGE_SEQUENCE
                    """, (insp_id, ln))
                    img_rows = cur.fetchall()
                    if img_rows:
                        html += "<div style='margin-top: 8px;'>"
                        for r in img_rows:
                            html += f"<img src='{r[0]}' style='max-width: 300px; max-height: 250px; margin: 5px; border: 1px solid #ddd; border-radius: 3px;'/>"
                        html += "</div>"
                    html += "</div>"

        html += "<p style='color: #888; font-size: 12px; margin-top: 30px;'>Generated by Vehicle Inspection Pipeline | Images valid for 7 days</p></body></html>"

        # Send email using SYSTEM$SEND_EMAIL
        recipient_list = [r.strip() for r in recipients.split(',') if r.strip()]

        # Get the notification integration name from settings (or use default)
        cur.execute("SELECT SETTING_VALUE FROM PIPELINE_SETTINGS WHERE SETTING_KEY = 'notification_integration'")
        int_row = cur.fetchone()
        integration_name = int_row[0] if int_row and int_row[0] else 'INSPECTION_EMAIL_INT'

        recipients_str = ','.join(recipient_list)
        cur.execute(
            f"CALL SYSTEM$SEND_EMAIL('{integration_name}', $${recipients_str}$$, 'Vehicle Inspection Report - New Results', $${html}$$, 'text/html')"
        )

        # Mark as notified
        for insp in inspections:
            cur.execute(
                "UPDATE INSPECTION_SUMMARY SET EMAIL_SENT_AT = CURRENT_TIMESTAMP() WHERE INSPECTION_ID = %s",
                (insp[10],)
            )
        conn.commit()
        logger.info(f"Email sent to {recipients} for {len(inspections)} inspection(s)")
        return f"Email sent for {len(inspections)} inspection(s)"

    except Exception as e:
        logger.error(f"Email error: {e}")
        return f"Error: {e}"
    finally:
        cur.close()
        conn.close()


async def poll_queue():
    """Background task: poll PROCESSING_QUEUE for new files."""
    logger.info("Queue poller started")
    # One-time catch-up for any unsent emails from before restart
    try:
        send_inspection_email()
    except Exception as e:
        logger.error(f"Startup email catch-up error: {e}")

    while True:
        try:
            conn = get_connection()
            cur = conn.cursor()

            # Refresh directory table to detect newly uploaded files
            cur.execute("ALTER STAGE INSPECTION_PDFS REFRESH")

            # Check directory table for new files not yet in queue
            cur.execute("""
                INSERT INTO PROCESSING_QUEUE (FILE_PATH)
                SELECT RELATIVE_PATH FROM DIRECTORY(@INSPECTION_PDFS)
                WHERE RELATIVE_PATH NOT IN (SELECT FILE_PATH FROM PROCESSING_QUEUE)
            """)
            conn.commit()

            # Get unprocessed files from queue
            cur.execute(
                "SELECT FILE_PATH FROM PROCESSING_QUEUE WHERE PROCESSED_AT IS NULL ORDER BY QUEUED_AT LIMIT 5"
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()

            if rows:
                logger.info(f"Found {len(rows)} files to process")
                for row in rows:
                    file_path = row[0]
                    result = process_single_pdf(file_path)

                    # Only mark as processed on success or skip
                    if result.get("status") in ("success", "skipped"):
                        conn2 = get_connection()
                        cur2 = conn2.cursor()
                        cur2.execute(
                            "UPDATE PROCESSING_QUEUE SET PROCESSED_AT = CURRENT_TIMESTAMP() WHERE FILE_PATH = %s",
                            (file_path,),
                        )
                        conn2.commit()
                        cur2.close()
                        conn2.close()
                    else:
                        logger.error(f"  Skipping queue update for failed file: {file_path}")

                # Send email for newly processed inspections
                send_inspection_email()

            else:
                # No new files — nothing to do
                pass

        except Exception as e:
            logger.error(f"Poller error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background poller on app startup."""
    task = asyncio.create_task(poll_queue())
    yield
    task.cancel()


# FastAPI app
app = FastAPI(title="Vehicle Inspection Pipeline", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/inspections")
def list_inspections():
    """List all inspections with failed item counts."""
    conn = get_connection()
    cur = conn.cursor(snowflake.connector.DictCursor)
    cur.execute("""
        SELECT s.INSPECTION_ID, s.INSPECTION_NUM, s.FILE_NAME, s.COMPANY, s.FLEET,
               s.UNIT_NUM, s.SERIAL_NUM, s.INSPECTOR, s.ORDER_DATE, s.COMPLETE_DATE,
               s.STATUS, s.LOCATION, s.PROCESSED_AT, s.EMAIL_SENT_AT,
               COUNT(DISTINCT f.ITEM_ID) as FAILED_COUNT,
               COUNT(DISTINCT i.IMAGE_ID) as IMAGE_COUNT
        FROM INSPECTION_SUMMARY s
        LEFT JOIN FAILED_LINE_ITEMS f ON s.INSPECTION_ID = f.INSPECTION_ID
        LEFT JOIN FAILURE_IMAGES i ON s.INSPECTION_ID = i.INSPECTION_ID
        GROUP BY s.INSPECTION_ID, s.INSPECTION_NUM, s.FILE_NAME, s.COMPANY, s.FLEET,
                 s.UNIT_NUM, s.SERIAL_NUM, s.INSPECTOR, s.ORDER_DATE, s.COMPLETE_DATE,
                 s.STATUS, s.LOCATION, s.PROCESSED_AT, s.EMAIL_SENT_AT
        ORDER BY s.PROCESSED_AT DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"inspections": rows}


@app.get("/api/inspections/{inspection_id}")
def get_inspection(inspection_id: str):
    """Get full inspection detail with failed items and image URLs."""
    conn = get_connection()
    cur = conn.cursor(snowflake.connector.DictCursor)

    # Get summary
    cur.execute(
        "SELECT * FROM INSPECTION_SUMMARY WHERE INSPECTION_ID = %s", (inspection_id,)
    )
    summary = cur.fetchone()
    if not summary:
        cur.close()
        conn.close()
        raise HTTPException(404, "Inspection not found")

    # Get failed items
    cur.execute(
        """SELECT ITEM_ID, LINE_NUM, DESCRIPTION, COMMENTS
        FROM FAILED_LINE_ITEMS WHERE INSPECTION_ID = %s ORDER BY LINE_NUM""",
        (inspection_id,),
    )
    items = cur.fetchall()

    # Get images - use proxy URLs instead of direct S3 (CSP blocks external img src in SPCS)
    cur.execute(
        """SELECT IMAGE_ID, ITEM_ID, LINE_NUM, STAGE_PATH, IMAGE_FORMAT, IMAGE_SEQUENCE
           FROM FAILURE_IMAGES WHERE INSPECTION_ID = %s ORDER BY LINE_NUM, IMAGE_SEQUENCE""",
        (inspection_id,),
    )
    images = cur.fetchall()
    # Add proxy URL for each image
    for img in images:
        img["IMAGE_URL"] = f"/api/images/{img['STAGE_PATH']}"

    cur.close()
    conn.close()

    return {"summary": summary, "failed_items": items, "images": images}


@app.get("/api/stats")
def get_stats():
    """Get pipeline statistics."""
    conn = get_connection()
    cur = conn.cursor(snowflake.connector.DictCursor)
    cur.execute("""
        SELECT
            (SELECT COUNT(*) FROM INSPECTION_SUMMARY) as total_inspections,
            (SELECT COUNT(*) FROM FAILED_LINE_ITEMS) as total_failures,
            (SELECT COUNT(*) FROM FAILURE_IMAGES) as total_images,
            (SELECT COUNT(*) FROM PROCESSING_QUEUE WHERE PROCESSED_AT IS NULL) as pending_files
    """)
    stats = cur.fetchone()
    cur.close()
    conn.close()
    return stats


@app.get("/api/settings")
def get_settings():
    """Get pipeline settings."""
    conn = get_connection()
    cur = conn.cursor(snowflake.connector.DictCursor)
    cur.execute("SELECT SETTING_KEY, SETTING_VALUE FROM PIPELINE_SETTINGS")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {row["SETTING_KEY"]: row["SETTING_VALUE"] for row in rows}


@app.post("/api/settings")
def update_settings(settings: dict):
    """Update pipeline settings."""
    conn = get_connection()
    cur = conn.cursor()
    for key, value in settings.items():
        cur.execute(
            """MERGE INTO PIPELINE_SETTINGS t USING (SELECT %s as k, %s as v) s
               ON t.SETTING_KEY = s.k
               WHEN MATCHED THEN UPDATE SET SETTING_VALUE = s.v, UPDATED_AT = CURRENT_TIMESTAMP()
               WHEN NOT MATCHED THEN INSERT (SETTING_KEY, SETTING_VALUE) VALUES (s.k, s.v)""",
            (key, value),
        )
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "updated"}


@app.get("/api/images/{path:path}")
def proxy_image(path: str):
    """Proxy images from Snowflake stage to avoid CSP blocking direct S3 URLs."""
    import requests as http_requests
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT GET_PRESIGNED_URL(@INSPECTION_IMAGES, '{path}', 3600)")
        url = cur.fetchone()[0]
    finally:
        cur.close()
        conn.close()

    resp = http_requests.get(url, timeout=30)
    resp.raise_for_status()

    # Determine content type from extension
    ext = path.rsplit('.', 1)[-1].lower() if '.' in path else 'png'
    content_type = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'gif': 'image/gif'}.get(ext, 'image/png')

    return Response(content=resp.content, media_type=content_type)
