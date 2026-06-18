#!/bin/bash

# Vehicle Inspection Pipeline - Automated Setup
# Deploys the complete pipeline to any Snowflake account with SPCS

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
header() { echo -e "\n${BLUE}=== $1 ===${NC}\n"; }

# ============================================================================
# CONFIGURATION
# ============================================================================
DATABASE="VEHICLE_INSPECTIONS"
SCHEMA="PUBLIC"
WAREHOUSE="COMPUTE_WH"
COMPUTE_POOL="INSPECTION_POOL"
SERVICE_NAME="INSPECTION_SERVICE"
IMAGE_NAME="inspection-pipeline"
IMAGE_TAG="latest"
EMAIL_RECIPIENT=""
CONNECTION_NAME=""

# ============================================================================
# PREREQUISITES CHECK
# ============================================================================
check_prereqs() {
    header "Checking Prerequisites"
    
    command -v snow >/dev/null 2>&1 || error "Snowflake CLI (snow) not found. Install: https://docs.snowflake.com/en/developer-guide/snowflake-cli"
    command -v docker >/dev/null 2>&1 || error "Docker not found. Install Docker Desktop."
    command -v openssl >/dev/null 2>&1 || error "OpenSSL not found."
    
    log "All prerequisites satisfied"
}

# ============================================================================
# CONNECTION SETUP
# ============================================================================
setup_connection() {
    header "Setting Up Connection"
    
    # List available connections
    echo "Available connections:"
    snow connection list 2>/dev/null | grep -E "^\|" | awk -F'|' '{print $2}' | tr -d ' ' | grep -v "^$" | grep -v "connection_name" | grep -v "^-" | nl
    echo ""
    
    if [ -z "$CONNECTION_NAME" ]; then
        read -p "Enter connection name to use: " CONNECTION_NAME
    fi
    
    # Test connection
    snow connection test --connection "$CONNECTION_NAME" >/dev/null 2>&1 || error "Connection '$CONNECTION_NAME' failed. Check your connections.toml"
    log "Connection '$CONNECTION_NAME' verified"
    
    # Extract account info using JSON output for reliable parsing
    ACCOUNT=$(snow sql -q "SELECT CURRENT_ACCOUNT() as val" --connection "$CONNECTION_NAME" --format json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['VAL'])" 2>/dev/null)
    SF_USER=$(snow sql -q "SELECT CURRENT_USER() as val" --connection "$CONNECTION_NAME" --format json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['VAL'])" 2>/dev/null)
    ORG_ACCOUNT=$(snow sql -q "SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME() as val" --connection "$CONNECTION_NAME" --format json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['VAL'])" 2>/dev/null)
    
    # Get registry URL from existing repo or construct from org-account
    REGISTRY_URL=$(snow sql -q "SHOW IMAGE REPOSITORIES IN ACCOUNT" --connection "$CONNECTION_NAME" --format json 2>/dev/null | python3 -c "
import sys,json
try:
    repos = json.load(sys.stdin)
    if repos:
        url = repos[0].get('repository_url','')
        # Extract host from full URL (e.g. org-acct.registry.snowflakecomputing.com/db/schema/repo)
        print(url.split('/')[0])
    else:
        print('')
except:
    print('')
" 2>/dev/null)
    
    if [ -z "$REGISTRY_URL" ]; then
        REGISTRY_URL="$(echo "$ORG_ACCOUNT" | tr '[:upper:]' '[:lower:]').registry.snowflakecomputing.com"
    fi
    
    HOST="$(echo "$ORG_ACCOUNT" | tr '[:upper:]' '[:lower:]').snowflakecomputing.com"
    
    log "Account: $ORG_ACCOUNT (locator: $ACCOUNT)"
    log "User: $SF_USER"
    log "Host: $HOST"
    log "Registry: $REGISTRY_URL"
}

# ============================================================================
# GATHER CONFIGURATION
# ============================================================================
gather_config() {
    header "Configuration"
    
    read -p "Email recipient for alerts [$SF_USER@snowflake.com]: " EMAIL_RECIPIENT
    EMAIL_RECIPIENT=${EMAIL_RECIPIENT:-"$SF_USER@snowflake.com"}
    
    read -p "Database name [$DATABASE]: " input
    DATABASE=${input:-$DATABASE}
    
    read -p "Warehouse [$WAREHOUSE]: " input
    WAREHOUSE=${input:-$WAREHOUSE}
    
    read -p "Compute pool name [$COMPUTE_POOL]: " input
    COMPUTE_POOL=${input:-$COMPUTE_POOL}
    
    log "Database: $DATABASE"
    log "Warehouse: $WAREHOUSE"
    log "Compute Pool: $COMPUTE_POOL"
    log "Email: $EMAIL_RECIPIENT"
}

# ============================================================================
# SNOWFLAKE SQL HELPER
# ============================================================================
snow_sql() {
    snow sql -q "$1" --connection "$CONNECTION_NAME" --database "$DATABASE" --schema "$SCHEMA" 2>/dev/null
    if [ $? -ne 0 ]; then
        warn "SQL command may have had issues (non-fatal)"
    fi
}

# ============================================================================
# CREATE INFRASTRUCTURE
# ============================================================================
create_infrastructure() {
    header "Creating Infrastructure"
    
    snow sql -q "CREATE DATABASE IF NOT EXISTS $DATABASE" --connection "$CONNECTION_NAME" 2>/dev/null
    
    # Stages
    snow_sql "CREATE STAGE IF NOT EXISTS INSPECTION_PDFS ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE') DIRECTORY = (ENABLE = TRUE, AUTO_REFRESH = TRUE)"
    snow_sql "CREATE STAGE IF NOT EXISTS INSPECTION_IMAGES ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')"
    
    # Image repository
    snow_sql "CREATE IMAGE REPOSITORY IF NOT EXISTS INSPECTION_REPO"
    
    # Compute pool
    snow_sql "CREATE COMPUTE POOL IF NOT EXISTS $COMPUTE_POOL MIN_NODES = 1 MAX_NODES = 1 INSTANCE_FAMILY = CPU_X64_XS AUTO_SUSPEND_SECS = 300 AUTO_RESUME = TRUE"
    
    # Tables
    snow_sql "CREATE TABLE IF NOT EXISTS INSPECTION_SUMMARY (
        INSPECTION_ID VARCHAR DEFAULT UUID_STRING() PRIMARY KEY,
        FILE_NAME VARCHAR NOT NULL UNIQUE,
        COMPANY VARCHAR, FLEET VARCHAR, LOCATION VARCHAR,
        SERIAL_NUM VARCHAR, UNIT_NUM VARCHAR, MODEL_NUM VARCHAR,
        INSPECTOR VARCHAR, ORDER_DATE VARCHAR, COMPLETE_DATE VARCHAR,
        TROUBLE_TICKET VARCHAR, INSPECTION_NUM VARCHAR, STATUS VARCHAR,
        INVOICE_NUM VARCHAR, RAW_EXTRACT VARIANT,
        EMAIL_SENT_AT TIMESTAMP_NTZ,
        PROCESSED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
    )"
    
    snow_sql "CREATE TABLE IF NOT EXISTS FAILED_LINE_ITEMS (
        ITEM_ID VARCHAR DEFAULT UUID_STRING() PRIMARY KEY,
        INSPECTION_ID VARCHAR NOT NULL REFERENCES INSPECTION_SUMMARY(INSPECTION_ID),
        LINE_NUM VARCHAR NOT NULL, DESCRIPTION VARCHAR, COMMENTS VARCHAR,
        PROCESSED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
    )"
    
    snow_sql "CREATE TABLE IF NOT EXISTS FAILURE_IMAGES (
        IMAGE_ID VARCHAR DEFAULT UUID_STRING() PRIMARY KEY,
        ITEM_ID VARCHAR REFERENCES FAILED_LINE_ITEMS(ITEM_ID),
        INSPECTION_ID VARCHAR NOT NULL REFERENCES INSPECTION_SUMMARY(INSPECTION_ID),
        LINE_NUM VARCHAR NOT NULL, STAGE_PATH VARCHAR NOT NULL,
        IMAGE_FORMAT VARCHAR, IMAGE_SEQUENCE INT,
        PROCESSED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
    )"
    
    snow_sql "CREATE TABLE IF NOT EXISTS PROCESSING_QUEUE (
        FILE_PATH VARCHAR NOT NULL,
        QUEUED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
        PROCESSED_AT TIMESTAMP_NTZ
    )"
    
    snow_sql "CREATE TABLE IF NOT EXISTS PIPELINE_SETTINGS (
        SETTING_KEY VARCHAR PRIMARY KEY,
        SETTING_VALUE VARCHAR,
        UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
    )"
    
    # Insert default email recipient
    snow_sql "INSERT INTO PIPELINE_SETTINGS (SETTING_KEY, SETTING_VALUE) SELECT 'email_recipients', '$EMAIL_RECIPIENT' WHERE NOT EXISTS (SELECT 1 FROM PIPELINE_SETTINGS WHERE SETTING_KEY = 'email_recipients')"
    
    # Stream and Task
    snow_sql "CREATE STREAM IF NOT EXISTS PDF_STREAM ON STAGE INSPECTION_PDFS"
    
    snow_sql "CREATE OR REPLACE TASK QUEUE_NEW_PDFS
        USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE = 'SMALL'
        SCHEDULE = '1 MINUTE'
        WHEN SYSTEM\$STREAM_HAS_DATA('$DATABASE.$SCHEMA.PDF_STREAM')
        AS INSERT INTO PROCESSING_QUEUE (FILE_PATH) SELECT RELATIVE_PATH FROM PDF_STREAM WHERE METADATA\$ACTION = 'INSERT'"
    
    snow_sql "ALTER TASK QUEUE_NEW_PDFS RESUME"
    
    # Email notification integration
    snow_sql "CREATE NOTIFICATION INTEGRATION IF NOT EXISTS INSPECTION_EMAIL_INT TYPE=EMAIL ENABLED=TRUE ALLOWED_RECIPIENTS=('$EMAIL_RECIPIENT')"
    
    # Email stored procedure
    cat "$SCRIPT_DIR/scripts/email_procedure.sql" | \
        sed "s/__DATABASE__/$DATABASE/g" | \
        sed "s/__SCHEMA__/$SCHEMA/g" | \
        sed "s/__EMAIL__/$EMAIL_RECIPIENT/g" | \
        snow sql -i --connection "$CONNECTION_NAME" 2>/dev/null
    
    log "Infrastructure created"
}

# ============================================================================
# KEY-PAIR AUTHENTICATION (SAFE - reuses existing keys)
# ============================================================================
create_secrets() {
    header "Setting Up Key-Pair Authentication"
    
    PRIVATE_KEY_PATH=""
    
    # Check if user already has RSA_PUBLIC_KEY set
    EXISTING_KEY=$(snow_sql "DESCRIBE USER $SF_USER" 2>/dev/null | grep -i "RSA_PUBLIC_KEY " | grep -v "RSA_PUBLIC_KEY_2" | awk -F'|' '{print $3}' | tr -d ' ')
    
    if [ -n "$EXISTING_KEY" ] && [ "$EXISTING_KEY" != "null" ] && [ ${#EXISTING_KEY} -gt 10 ]; then
        log "Existing RSA_PUBLIC_KEY detected for user $SF_USER"
        log "Looking for matching private key..."
        
        # Try to find existing private key from connections.toml
        PRIVATE_KEY_PATH=$(python3 -c "
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib
import os

paths_to_check = [
    os.path.expanduser('~/.snowflake/connections.toml'),
    os.path.expanduser('~/.snowflake/config.toml'),
]
for p in paths_to_check:
    if os.path.exists(p):
        with open(p, 'rb') as f:
            data = tomllib.load(f)
        # Check connection-specific config
        if '$CONNECTION_NAME' in data:
            pkf = data['$CONNECTION_NAME'].get('private_key_file', '')
            if pkf:
                print(os.path.expanduser(pkf))
                sys.exit(0)
        # Check all connections
        for name, cfg in data.items():
            if isinstance(cfg, dict) and 'private_key_file' in cfg:
                pkf = cfg['private_key_file']
                expanded = os.path.expanduser(pkf)
                if os.path.exists(expanded):
                    print(expanded)
                    sys.exit(0)

# Check well-known paths
for key_dir in [os.path.expanduser('~/.snowflake/keys/')]:
    if os.path.isdir(key_dir):
        for f in os.listdir(key_dir):
            if f.endswith('.p8'):
                print(os.path.join(key_dir, f))
                sys.exit(0)
" 2>/dev/null)
        
        if [ -n "$PRIVATE_KEY_PATH" ] && [ -f "$PRIVATE_KEY_PATH" ]; then
            log "Found existing private key: $PRIVATE_KEY_PATH"
            log "Reusing existing key pair (no changes to user's RSA_PUBLIC_KEY)"
        else
            warn "Could not find private key file. Options:"
            echo "  1) Enter path to existing .p8 private key file"
            echo "  2) Generate new key pair using RSA_PUBLIC_KEY_2 slot (safe, won't break existing apps)"
            echo "  3) Generate new key pair (WARNING: will overwrite existing RSA_PUBLIC_KEY)"
            read -p "Choice [1/2/3]: " KEY_CHOICE
            
            case $KEY_CHOICE in
                1)
                    read -p "Path to .p8 file: " PRIVATE_KEY_PATH
                    [ -f "$PRIVATE_KEY_PATH" ] || error "File not found: $PRIVATE_KEY_PATH"
                    ;;
                2)
                    generate_key_slot_2
                    ;;
                3)
                    warn "This will break other SPCS apps using the current key!"
                    read -p "Are you sure? [y/N]: " CONFIRM
                    [ "$CONFIRM" = "y" ] || error "Aborted"
                    generate_new_key
                    ;;
                *)
                    error "Invalid choice"
                    ;;
            esac
        fi
    else
        log "No existing RSA key detected. Generating new key pair..."
        generate_new_key
    fi
    
    # Create the Snowflake secret from the private key
    PRIVATE_KEY_ESCAPED=$(awk '{printf "%s\\n", $0}' "$PRIVATE_KEY_PATH")
    snow_sql "CREATE OR REPLACE SECRET $DATABASE.$SCHEMA.SNOWFLAKE_PRIVATE_KEY_SECRET TYPE = GENERIC_STRING SECRET_STRING = '$PRIVATE_KEY_ESCAPED'"
    
    log "Secret created: $DATABASE.$SCHEMA.SNOWFLAKE_PRIVATE_KEY_SECRET"
}

generate_new_key() {
    TEMP_DIR=$(mktemp -d)
    PRIVATE_KEY_PATH="$TEMP_DIR/snowflake_key.p8"
    
    openssl genrsa 2048 2>/dev/null | openssl pkcs8 -topk8 -nocrypt -out "$PRIVATE_KEY_PATH"
    openssl rsa -in "$PRIVATE_KEY_PATH" -pubout -out "$TEMP_DIR/snowflake_key.pub" 2>/dev/null
    
    PUBLIC_KEY=$(grep -v "BEGIN\|END" "$TEMP_DIR/snowflake_key.pub" | tr -d '\n')
    snow_sql "ALTER USER $SF_USER SET RSA_PUBLIC_KEY='$PUBLIC_KEY'"
    
    # Copy to persistent location
    mkdir -p ~/.snowflake/keys
    cp "$PRIVATE_KEY_PATH" ~/.snowflake/keys/${CONNECTION_NAME}.p8
    PRIVATE_KEY_PATH=~/.snowflake/keys/${CONNECTION_NAME}.p8
    
    log "New key pair generated and assigned"
    log "Private key saved: $PRIVATE_KEY_PATH"
}

generate_key_slot_2() {
    TEMP_DIR=$(mktemp -d)
    PRIVATE_KEY_PATH="$TEMP_DIR/snowflake_key2.p8"
    
    openssl genrsa 2048 2>/dev/null | openssl pkcs8 -topk8 -nocrypt -out "$PRIVATE_KEY_PATH"
    openssl rsa -in "$PRIVATE_KEY_PATH" -pubout -out "$TEMP_DIR/snowflake_key2.pub" 2>/dev/null
    
    PUBLIC_KEY=$(grep -v "BEGIN\|END" "$TEMP_DIR/snowflake_key2.pub" | tr -d '\n')
    snow_sql "ALTER USER $SF_USER SET RSA_PUBLIC_KEY_2='$PUBLIC_KEY'"
    
    # Copy to persistent location
    mkdir -p ~/.snowflake/keys
    cp "$PRIVATE_KEY_PATH" ~/.snowflake/keys/${CONNECTION_NAME}_slot2.p8
    PRIVATE_KEY_PATH=~/.snowflake/keys/${CONNECTION_NAME}_slot2.p8
    
    log "Key pair generated using RSA_PUBLIC_KEY_2 slot (existing apps unaffected)"
    log "Private key saved: $PRIVATE_KEY_PATH"
}

# ============================================================================
# EXTERNAL ACCESS
# ============================================================================
create_external_access() {
    header "Creating External Access Integration"
    
    # Determine S3 stage host (account-specific)
    S3_HOST=$(snow_sql "LIST @$DATABASE.$SCHEMA.INSPECTION_PDFS" 2>/dev/null | head -1 | grep -oP 'https://[^/]+' | sed 's|https://||' || echo "")
    
    # Build network rule with Snowflake host + S3
    VALUE_LIST="'${HOST}:443'"
    if [ -n "$S3_HOST" ]; then
        VALUE_LIST="$VALUE_LIST, '${S3_HOST}:443'"
    fi
    # Add common S3 patterns for the account
    VALUE_LIST="$VALUE_LIST, '*.s3.amazonaws.com:443', '*.s3.us-west-2.amazonaws.com:443'"
    
    snow_sql "CREATE OR REPLACE NETWORK RULE $DATABASE.$SCHEMA.SNOWFLAKE_API_RULE MODE = EGRESS TYPE = HOST_PORT VALUE_LIST = ($VALUE_LIST)"
    
    snow_sql "CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION INSPECTION_EXTERNAL_ACCESS
        ALLOWED_NETWORK_RULES = ($DATABASE.$SCHEMA.SNOWFLAKE_API_RULE)
        ALLOWED_AUTHENTICATION_SECRETS = ($DATABASE.$SCHEMA.SNOWFLAKE_PRIVATE_KEY_SECRET)
        ENABLED = TRUE"
    
    log "External access integration created"
}

# ============================================================================
# BUILD AND PUSH DOCKER IMAGE
# ============================================================================
build_and_push() {
    header "Building and Pushing Docker Image"
    
    FULL_IMAGE="$REGISTRY_URL/$DATABASE/$SCHEMA/inspection_repo/$IMAGE_NAME:$IMAGE_TAG"
    FULL_IMAGE=$(echo "$FULL_IMAGE" | tr '[:upper:]' '[:lower:]')
    
    # Login to registry
    snow spcs image-registry login --connection "$CONNECTION_NAME"
    
    # Build
    log "Building image (linux/amd64)..."
    docker buildx build --platform linux/amd64 -t "$FULL_IMAGE" --load "$SCRIPT_DIR"
    
    # Push
    log "Pushing image..."
    docker push "$FULL_IMAGE"
    
    log "Image pushed: $FULL_IMAGE"
}

# ============================================================================
# DEPLOY SERVICE
# ============================================================================
deploy_service() {
    header "Deploying SPCS Service"
    
    FULL_IMAGE="/$DATABASE/$SCHEMA/inspection_repo/$IMAGE_NAME:$IMAGE_TAG"
    FULL_IMAGE=$(echo "$FULL_IMAGE" | tr '[:upper:]' '[:lower:]')
    
    snow_sql "CREATE SERVICE IF NOT EXISTS $DATABASE.$SCHEMA.$SERVICE_NAME
        IN COMPUTE POOL $COMPUTE_POOL
        FROM SPECIFICATION \$\$
spec:
  containers:
    - name: inspection-service
      image: $FULL_IMAGE
      env:
        SNOWFLAKE_ACCOUNT: $ORG_ACCOUNT
        SNOWFLAKE_USER: $SF_USER
        SNOWFLAKE_WAREHOUSE: $WAREHOUSE
      secrets:
        - snowflakeSecret: $DATABASE.$SCHEMA.SNOWFLAKE_PRIVATE_KEY_SECRET
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
\$\$
        EXTERNAL_ACCESS_INTEGRATIONS = (INSPECTION_EXTERNAL_ACCESS)
        MIN_INSTANCES = 1 MAX_INSTANCES = 1"
    
    log "Service created. Waiting for READY status..."
    
    for i in {1..30}; do
        STATUS=$(snow_sql "SELECT SYSTEM\$GET_SERVICE_STATUS('$DATABASE.$SCHEMA.$SERVICE_NAME')" 2>/dev/null | grep -o '"status":"[^"]*"' | head -1 | cut -d'"' -f4)
        if [ "$STATUS" = "READY" ]; then
            break
        fi
        sleep 10
    done
    
    ENDPOINT=$(snow_sql "SHOW ENDPOINTS IN SERVICE $DATABASE.$SCHEMA.$SERVICE_NAME" 2>/dev/null | grep "ingress_url" | awk -F'|' '{print $7}' | tr -d ' ' || echo "")
    if [ -z "$ENDPOINT" ]; then
        ENDPOINT=$(snow_sql "SHOW ENDPOINTS IN SERVICE $DATABASE.$SCHEMA.$SERVICE_NAME" 2>/dev/null | tail -2 | head -1 | awk -F'|' '{print $7}' | tr -d ' ')
    fi
    
    log "Service deployed!"
    echo ""
    echo -e "${GREEN}Dashboard URL: https://$ENDPOINT${NC}"
    echo ""
}

# ============================================================================
# MAIN
# ============================================================================
main() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║      Vehicle Inspection Pipeline - Automated Setup          ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    
    check_prereqs
    setup_connection
    gather_config
    create_infrastructure
    create_secrets
    create_external_access
    build_and_push
    deploy_service
    
    header "Setup Complete!"
    echo ""
    echo "Usage:"
    echo "  1. Upload PDFs to the stage:"
    echo "     PUT 'file:///path/to/report.pdf' @$DATABASE.$SCHEMA.INSPECTION_PDFS AUTO_COMPRESS=FALSE;"
    echo ""
    echo "  2. Processing is automatic (within 1-2 minutes)"
    echo "  3. Email alert sent to: $EMAIL_RECIPIENT"
    echo "  4. Dashboard: https://$ENDPOINT"
    echo ""
}

# Run
main "$@"
