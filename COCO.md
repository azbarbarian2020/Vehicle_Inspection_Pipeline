# Vehicle Inspection Pipeline

## Project Overview
This is a Snowflake SPCS (Snowpark Container Services) application that automatically processes vehicle inspection PDFs. When PDFs are uploaded to a Snowflake stage, the pipeline:
1. Extracts summary information using AI_EXTRACT
2. Identifies failed inspection items using AI_PARSE_DOCUMENT
3. Extracts failure photos using PyMuPDF spatial image mapping
4. Sends formatted HTML email alerts with embedded photos
5. Provides a React web dashboard for browsing all results

## Deployment Instructions

When a user asks to deploy this to their Snowflake account, follow these steps in order. The user needs:
- Docker Desktop running
- ACCOUNTADMIN role on the target account
- Either a CoCo Desktop Snowflake connection OR a Snowflake CLI connection

### Step 0: Verify Connectivity

**If user has CoCo Desktop connected to their target account** (check the connection in the status bar):
- Use `snowflake_sql_execute` for all SQL commands directly
- You still need a CLI connection for Docker registry login

**Check if a CLI connection exists:**
```bash
snow connection list
```

**If no CLI connection exists**, help the user set one up:
1. Generate a key pair:
   ```bash
   mkdir -p ~/.snowflake/keys
   openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out ~/.snowflake/keys/<name>.p8
   openssl rsa -in ~/.snowflake/keys/<name>.p8 -pubout -out /tmp/<name>_key.pub
   ```
2. Get the public key body: `grep -v 'BEGIN\|END' /tmp/<name>_key.pub | tr -d '\n'`
3. Assign to user (run via CoCo's SQL or Snowsight):
   ```sql
   ALTER USER <username> SET RSA_PUBLIC_KEY='<public_key_body>';
   ```
4. Add to `~/.snowflake/connections.toml`:
   ```toml
   [<name>]
   account = "<ORG-ACCOUNT>"
   user = "<USERNAME>"
   authenticator = "SNOWFLAKE_JWT"
   private_key_file = "~/.snowflake/keys/<name>.p8"
   role = "ACCOUNTADMIN"
   ```
5. Test: `snow connection test --connection <name>`

### Step 1: Identify the Connection

Ask which Snowflake CLI connection to use. Run `snow connection list` to show available connections. The user picks one. Store it as `CONNECTION_NAME`.

Then get account info:
```bash
snow sql -q "SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME() as val" --connection <CONNECTION_NAME> --format json
```

### Step 2: Create Snowflake Objects

Run these SQL commands (replace VEHICLE_INSPECTIONS with user's preferred database name if different):

```sql
CREATE DATABASE IF NOT EXISTS VEHICLE_INSPECTIONS;
USE DATABASE VEHICLE_INSPECTIONS;
USE SCHEMA PUBLIC;

CREATE WAREHOUSE IF NOT EXISTS INSPECTION_WH WAREHOUSE_SIZE='SMALL' AUTO_SUSPEND=60 AUTO_RESUME=TRUE INITIALLY_SUSPENDED=TRUE;

CREATE STAGE IF NOT EXISTS INSPECTION_PDFS ENCRYPTION=(TYPE='SNOWFLAKE_SSE') DIRECTORY=(ENABLE=TRUE);
CREATE STAGE IF NOT EXISTS INSPECTION_IMAGES ENCRYPTION=(TYPE='SNOWFLAKE_SSE');

CREATE IMAGE REPOSITORY IF NOT EXISTS INSPECTION_REPO;

CREATE COMPUTE POOL IF NOT EXISTS INSPECTION_POOL MIN_NODES=1 MAX_NODES=1 INSTANCE_FAMILY=CPU_X64_XS AUTO_SUSPEND_SECS=300 AUTO_RESUME=TRUE;

CREATE TABLE IF NOT EXISTS INSPECTION_SUMMARY (
    INSPECTION_ID VARCHAR DEFAULT UUID_STRING() PRIMARY KEY,
    FILE_NAME VARCHAR NOT NULL UNIQUE,
    COMPANY VARCHAR, FLEET VARCHAR, LOCATION VARCHAR,
    SERIAL_NUM VARCHAR, UNIT_NUM VARCHAR, MODEL_NUM VARCHAR,
    INSPECTOR VARCHAR, ORDER_DATE VARCHAR, COMPLETE_DATE VARCHAR,
    TROUBLE_TICKET VARCHAR, INSPECTION_NUM VARCHAR, STATUS VARCHAR,
    INVOICE_NUM VARCHAR, RAW_EXTRACT VARIANT,
    EMAIL_SENT_AT TIMESTAMP_NTZ,
    PROCESSED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS FAILED_LINE_ITEMS (
    ITEM_ID VARCHAR DEFAULT UUID_STRING() PRIMARY KEY,
    INSPECTION_ID VARCHAR NOT NULL,
    LINE_NUM VARCHAR NOT NULL, DESCRIPTION VARCHAR, COMMENTS VARCHAR,
    PROCESSED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS FAILURE_IMAGES (
    IMAGE_ID VARCHAR DEFAULT UUID_STRING() PRIMARY KEY,
    ITEM_ID VARCHAR,
    INSPECTION_ID VARCHAR NOT NULL,
    LINE_NUM VARCHAR NOT NULL, STAGE_PATH VARCHAR NOT NULL,
    IMAGE_FORMAT VARCHAR, IMAGE_SEQUENCE INT,
    PROCESSED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS PROCESSING_QUEUE (
    FILE_PATH VARCHAR NOT NULL,
    QUEUED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PROCESSED_AT TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS PIPELINE_SETTINGS (
    SETTING_KEY VARCHAR PRIMARY KEY,
    SETTING_VALUE VARCHAR,
    UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

INSERT INTO PIPELINE_SETTINGS (SETTING_KEY, SETTING_VALUE)
  SELECT 'email_recipients', ''
  WHERE NOT EXISTS (SELECT 1 FROM PIPELINE_SETTINGS WHERE SETTING_KEY = 'email_recipients');
```

### Step 3: Set Up Key-Pair Authentication

The SPCS service needs a private key to authenticate. Check if the user already has one configured in their connection:

```bash
snow connection test --connection <CONNECTION_NAME>
```

If the connection uses key-pair auth, find the private key file from `~/.snowflake/connections.toml`. If not, generate one:

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out snowflake_key.p8
openssl rsa -in snowflake_key.p8 -pubout -out snowflake_key.pub
```

Then assign the public key and create a secret:
```sql
ALTER USER <username> SET RSA_PUBLIC_KEY='<public key content without headers>';

CREATE OR REPLACE SECRET VEHICLE_INSPECTIONS.PUBLIC.SNOWFLAKE_PRIVATE_KEY_SECRET
  TYPE = GENERIC_STRING
  SECRET_STRING = '<private key content with \n for newlines>';
```

### Step 4: Create Network Rule and External Access

```sql
CREATE OR REPLACE NETWORK RULE VEHICLE_INSPECTIONS.PUBLIC.SNOWFLAKE_API_RULE
  MODE = EGRESS TYPE = HOST_PORT
  VALUE_LIST = ('*.snowflakecomputing.com:443', '*.s3.amazonaws.com:443', '*.s3.us-west-2.amazonaws.com:443');

CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION INSPECTION_EXTERNAL_ACCESS
  ALLOWED_NETWORK_RULES = (VEHICLE_INSPECTIONS.PUBLIC.SNOWFLAKE_API_RULE)
  ALLOWED_AUTHENTICATION_SECRETS = (VEHICLE_INSPECTIONS.PUBLIC.SNOWFLAKE_PRIVATE_KEY_SECRET)
  ENABLED = TRUE;
```

### Step 5: Create Email Integration

Ask the user for their email address, then:
```sql
CREATE NOTIFICATION INTEGRATION IF NOT EXISTS INSPECTION_EMAIL_INT
  TYPE = EMAIL ENABLED = TRUE
  ALLOWED_RECIPIENTS = ('<user_email>');
```

### Step 6: Build and Push Docker Image

Get the image repository URL:
```sql
SHOW IMAGE REPOSITORIES IN SCHEMA VEHICLE_INSPECTIONS.PUBLIC;
```

The `repository_url` column gives the full path. Then:

```bash
# Login to Snowflake container registry
snow spcs image-registry login --connection <CONNECTION_NAME>

# Build (works on both Mac and Windows with Docker Desktop)
docker build --platform linux/amd64 -t <REPO_URL>/inspection-pipeline:latest .

# Push
docker push <REPO_URL>/inspection-pipeline:latest
```

### Step 7: Deploy the Service

Get the org-account name and user from the connection. Then create the service:

```sql
CREATE SERVICE IF NOT EXISTS VEHICLE_INSPECTIONS.PUBLIC.INSPECTION_SERVICE
  IN COMPUTE POOL INSPECTION_POOL
  FROM SPECIFICATION $$
spec:
  containers:
    - name: inspection-service
      image: /vehicle_inspections/public/inspection_repo/inspection-pipeline:latest
      env:
        SNOWFLAKE_ACCOUNT: <ORG-ACCOUNT>
        SNOWFLAKE_USER: <USERNAME>
        SNOWFLAKE_WAREHOUSE: INSPECTION_WH
      secrets:
        - snowflakeSecret: VEHICLE_INSPECTIONS.PUBLIC.SNOWFLAKE_PRIVATE_KEY_SECRET
          secretKeyRef: secret_string
          envVarName: SNOWFLAKE_PRIVATE_KEY
      resources:
        requests:
          cpu: 0.5
          memory: 1Gi
        limits:
          cpu: 2
          memory: 4Gi
  endpoints:
    - name: app
      port: 8080
      public: true
  networkPolicyConfig:
    allowInternetEgress: true
$$
  EXTERNAL_ACCESS_INTEGRATIONS = (INSPECTION_EXTERNAL_ACCESS)
  MIN_INSTANCES = 1 MAX_INSTANCES = 1;
```

### Step 8: Verify Deployment

```sql
-- Check service is running
SELECT SYSTEM$GET_SERVICE_STATUS('VEHICLE_INSPECTIONS.PUBLIC.INSPECTION_SERVICE');

-- Get dashboard URL
SHOW ENDPOINTS IN SERVICE VEHICLE_INSPECTIONS.PUBLIC.INSPECTION_SERVICE;
```

The `ingress_url` column (prefixed with `https://`) is the dashboard.

### Step 9: Configure and Test

1. Open the dashboard URL in a browser
2. Go to Settings, enter the email recipient
3. Upload a test PDF:
   ```sql
   PUT 'file:///path/to/Report.pdf' @VEHICLE_INSPECTIONS.PUBLIC.INSPECTION_PDFS AUTO_COMPRESS=FALSE;
   ```
4. Wait 30-60 seconds, then check the dashboard for results

## Architecture Notes
- The SPCS service polls every 30 seconds for new PDFs
- It auto-refreshes the stage directory table (no manual REFRESH needed)
- Images are proxied through the backend to avoid SPCS Content Security Policy restrictions
- Email uses SYSTEM$SEND_EMAIL with the INSPECTION_EMAIL_INT integration
- Multiple PDFs landing simultaneously are batched into a single email

## Troubleshooting
- If images don't appear: check that `*.s3.amazonaws.com:443` is in the network rule
- If AI_EXTRACT fails: ensure INSPECTION_WH is active and the account has Cortex AI enabled
- Service logs: `SELECT SYSTEM$GET_SERVICE_LOGS('VEHICLE_INSPECTIONS.PUBLIC.INSPECTION_SERVICE', 0, 'inspection-service', 50);`
