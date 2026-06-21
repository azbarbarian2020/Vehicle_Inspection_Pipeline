# Deployment Guide

Deploy the Vehicle Inspection Pipeline to any Snowflake account with SPCS enabled.

## Prerequisites

- **Docker Desktop** installed and running ([download](https://www.docker.com/products/docker-desktop/))
- **Snowflake CLI** installed ([download](https://docs.snowflake.com/en/developer-guide/snowflake-cli))
- **ACCOUNTADMIN** access on the target Snowflake account
- A configured Snowflake CLI connection (`snow connection add` or edit `~/.snowflake/connections.toml`)

## Option A: Automated (Mac/Linux)

```bash
git clone https://github.com/azbarbarian2020/Vehicle_Inspection_Pipeline.git
cd Vehicle_Inspection_Pipeline
./setup.sh
```

The script handles everything interactively.

## Option B: CoCo Desktop (Mac or Windows)

1. Clone this repo and open the folder in CoCo Desktop
2. Tell CoCo: "Deploy this to my Snowflake account"
3. CoCo reads `COCO.md` and walks you through each step

## Option C: Manual (Any Platform)

### 1. Set your connection

```bash
# Verify your connection works
snow connection test --connection <YOUR_CONNECTION>

# Get your org-account name (needed for service spec)
snow sql -q "SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME()" --connection <YOUR_CONNECTION>
```

### 2. Create database and objects

Run in a Snowsight worksheet or via CLI:

```sql
CREATE DATABASE IF NOT EXISTS VEHICLE_INSPECTIONS;
USE DATABASE VEHICLE_INSPECTIONS;
USE SCHEMA PUBLIC;

-- Warehouse
CREATE WAREHOUSE IF NOT EXISTS INSPECTION_WH
  WAREHOUSE_SIZE = 'SMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE;

-- Stages
CREATE STAGE IF NOT EXISTS INSPECTION_PDFS
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE') DIRECTORY = (ENABLE = TRUE);
CREATE STAGE IF NOT EXISTS INSPECTION_IMAGES
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

-- Image repository
CREATE IMAGE REPOSITORY IF NOT EXISTS INSPECTION_REPO;

-- Compute pool
CREATE COMPUTE POOL IF NOT EXISTS INSPECTION_POOL
  MIN_NODES = 1 MAX_NODES = 1 INSTANCE_FAMILY = CPU_X64_XS
  AUTO_SUSPEND_SECS = 300 AUTO_RESUME = TRUE;

-- Tables
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
  SELECT 'email_recipients', '' WHERE NOT EXISTS
  (SELECT 1 FROM PIPELINE_SETTINGS WHERE SETTING_KEY = 'email_recipients');
```

### 3. Set up key-pair authentication

Generate a key pair (skip if you already have one):

```bash
# Works on Mac, Linux, and Windows (Git Bash or PowerShell with OpenSSL)
openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out snowflake_key.p8
openssl rsa -in snowflake_key.p8 -pubout -out snowflake_key.pub
```

Assign the public key to your user and create a Snowflake secret:

```sql
-- Get public key content (everything between BEGIN/END lines)
-- Then assign:
ALTER USER <your_username> SET RSA_PUBLIC_KEY = '<public_key_content>';

-- Create secret with the private key (replace newlines with \n)
CREATE OR REPLACE SECRET VEHICLE_INSPECTIONS.PUBLIC.SNOWFLAKE_PRIVATE_KEY_SECRET
  TYPE = GENERIC_STRING
  SECRET_STRING = '-----BEGIN PRIVATE KEY-----\nMIIE...<your key>...\n-----END PRIVATE KEY-----';
```

### 4. Create network access

```sql
CREATE OR REPLACE NETWORK RULE VEHICLE_INSPECTIONS.PUBLIC.SNOWFLAKE_API_RULE
  MODE = EGRESS TYPE = HOST_PORT
  VALUE_LIST = ('*.snowflakecomputing.com:443', '*.s3.amazonaws.com:443', '*.s3.us-west-2.amazonaws.com:443');

CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION INSPECTION_EXTERNAL_ACCESS
  ALLOWED_NETWORK_RULES = (VEHICLE_INSPECTIONS.PUBLIC.SNOWFLAKE_API_RULE)
  ALLOWED_AUTHENTICATION_SECRETS = (VEHICLE_INSPECTIONS.PUBLIC.SNOWFLAKE_PRIVATE_KEY_SECRET)
  ENABLED = TRUE;
```

### 5. Create email integration

```sql
CREATE NOTIFICATION INTEGRATION IF NOT EXISTS INSPECTION_EMAIL_INT
  TYPE = EMAIL ENABLED = TRUE
  ALLOWED_RECIPIENTS = ('your.email@company.com');
```

### 6. Build and push the Docker image

```bash
# Get the repository URL
snow sql -q "SHOW IMAGE REPOSITORIES IN SCHEMA VEHICLE_INSPECTIONS.PUBLIC" --connection <YOUR_CONNECTION>
# Note the repository_url value

# Login to registry
snow spcs image-registry login --connection <YOUR_CONNECTION>

# Build (same command on Mac and Windows)
docker build --platform linux/amd64 -t <REPO_URL>/inspection-pipeline:latest .

# Push
docker push <REPO_URL>/inspection-pipeline:latest
```

### 7. Deploy the service

Replace `<ORG-ACCOUNT>` and `<USERNAME>` with your values:

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

### 8. Get dashboard URL

```sql
SHOW ENDPOINTS IN SERVICE VEHICLE_INSPECTIONS.PUBLIC.INSPECTION_SERVICE;
```

The `ingress_url` column is your dashboard (add `https://` prefix).

### 9. Test it

1. Open the dashboard URL in your browser
2. Go to **Settings** and enter your email address
3. Upload a test PDF:
   - **Snowsight**: Data > VEHICLE_INSPECTIONS > PUBLIC > Stages > INSPECTION_PDFS > + Files
   - **SQL**: `PUT 'file:///path/to/Report.pdf' @INSPECTION_PDFS AUTO_COMPRESS=FALSE;`
4. Wait 30-60 seconds — check the dashboard for results and your inbox for the email

## Costs

- **Compute Pool** (CPU_X64_XS): ~1 credit/hour when active, auto-suspends after 5 min idle
- **AI_EXTRACT/AI_PARSE_DOCUMENT**: ~15,000 tokens per 16-page PDF
- **Storage**: ~1-2 MB of images per inspection

## Cleanup

To remove everything:

```sql
DROP SERVICE IF EXISTS VEHICLE_INSPECTIONS.PUBLIC.INSPECTION_SERVICE;
DROP COMPUTE POOL IF EXISTS INSPECTION_POOL;
DROP DATABASE IF EXISTS VEHICLE_INSPECTIONS;
DROP INTEGRATION IF EXISTS INSPECTION_EXTERNAL_ACCESS;
DROP INTEGRATION IF EXISTS INSPECTION_EMAIL_INT;
```
