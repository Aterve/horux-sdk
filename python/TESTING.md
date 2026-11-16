# Testing Horux Data Ingestion Authentication

This directory contains test scripts to verify the data-ingestor service authentication and permission system.

## Prerequisites

1. **Backend Service Running**
   ```bash
   cd backend
   npm run dev
   # Should be running on http://localhost:3000
   ```

2. **Data-Ingestor Service Running**
   ```bash
   cd services/data-ingestor
   go run cmd/ingestor/main.go
   # Should be running on http://localhost:3434
   ```

3. **Python Dependencies**
   ```bash
   cd python
   pip install -r requirements.txt
   pip install -e .  # Install the SDK in development mode
   ```

## Quick Start

### Option 1: Random Data Generator (Simplest)

If you just want to test the ingestor with random data:

1. **Create an API token** (see instructions below for getting session cookie)
2. **Run the random data generator:**
   ```bash
   python random_ingestion.py <your_api_token>
   ```

This will continuously generate and ingest random metrics and logs. Press Ctrl+C to stop.

**Options:**
```bash
# Only generate metrics
python random_ingestion.py <token> --metrics-only

# Only generate logs
python random_ingestion.py <token> --logs-only

# Custom interval (default: 1 second)
python random_ingestion.py <token> --interval 0.5

# Limited iterations instead of infinite
python random_ingestion.py <token> --count 100

# Custom resource ID
python random_ingestion.py <token> --resource-id <your-resource-uuid>
```

**Resource ID:** required — pass `--resource-id` or set `HORUX_RESOURCE_ID`. There is
deliberately no default: a wrong-but-plausible UUID makes every push fail resource lookup.

**Note:** Project ID is automatically fetched by the ingestor based on the resource ID.

### Option 2: Automated Testing (Recommended)

1. **List available roles:**
   ```bash
   python create_test_token.py --list-roles --session-cookie <your_session_cookie>
   ```

2. **Create a test token with Developer role** (has metrics.ingest and logs.ingest permissions):
   ```bash
   python create_test_token.py \
     --session-cookie <your_session_cookie> \
     --role-id <developer_role_uuid> \
     --name "Test Ingestion Token"
   ```

   **How to get your session cookie:**
   - Log in to Horux console at http://localhost:4200
   - Open Browser DevTools (F12)
   - Go to Application → Storage → Cookies
   - Copy the value of `connect.sid`

3. **Run the authentication tests:**
   ```bash
   python test_auth.py <api_token_from_step_2>
   ```

### Option 3: Manual API Token Creation

If you prefer to create tokens via the Horux console UI:

1. Log in to Horux console
2. Go to Settings → API Tokens
3. Click "Create Token"
4. Select a role with `metrics.ingest` and `logs.ingest` permissions (e.g., Developer)
5. Copy the generated token (shown only once!)
6. Run tests:
   ```bash
   python test_auth.py <your_api_token>
   ```

## Test Suite

The `test_auth.py` script runs the following tests:

### Test 1: Invalid Token (401 Unauthorized)
Tests that requests with invalid tokens are rejected:
```bash
python test_auth.py horux_invalid_token_12345
```
**Expected:** 401 Unauthorized error

### Test 2: Missing Permissions (403 Forbidden) - Optional
Tests that tokens without ingestion permissions are rejected.

To test this, create a Viewer role token (read-only permissions):
```bash
python create_test_token.py \
  --session-cookie <cookie> \
  --role-id <viewer_role_uuid> \
  --name "Viewer Token"
```

Then run:
```bash
python test_auth.py <developer_token> <viewer_token>
```
**Expected:** 403 Forbidden for ingestion endpoints

### Test 3: Metrics Ingestion
Tests metrics ingestion with valid permissions:
- Counter metrics
- Gauge metrics
- Histogram metrics with buckets
- Summary metrics with quantiles

**Expected:** All metrics successfully ingested to TimescaleDB

### Test 4: Logs Ingestion
Tests logs ingestion with valid permissions:
- Different log levels (TRACE, DEBUG, INFO, WARN, ERROR)
- Structured log fields
- Distributed tracing (trace_id, span_id, request_id)

**Expected:** All logs successfully ingested to TimescaleDB

## Verifying Results

### Check Database for Ingested Data

**Metrics:**
```sql
-- Connect to database
psql -h localhost -U postgres -d horus

-- Check recent metrics
SELECT
  metric_name,
  metric_type,
  value,
  labels,
  timestamp
FROM metrics_data
WHERE timestamp > NOW() - INTERVAL '5 minutes'
ORDER BY timestamp DESC
LIMIT 20;
```

**Logs:**
```sql
-- Check recent logs
SELECT
  level,
  message,
  fields,
  timestamp
FROM logs_data
WHERE timestamp > NOW() - INTERVAL '5 minutes'
ORDER BY timestamp DESC
LIMIT 20;
```

### Check Data-Ingestor Stats

```bash
# Get metrics stats (requires authentication)
curl -H "Authorization: Bearer <your_token>" \
  http://localhost:3434/api/v1/metrics/stats

# Get logs stats (requires authentication)
curl -H "Authorization: Bearer <your_token>" \
  http://localhost:3434/api/v1/logs/stats
```

## Troubleshooting

### Error: "Cannot connect to data-ingestor"
- Ensure data-ingestor is running: `go run cmd/ingestor/main.go`
- Check it's listening on port 3434: `curl http://localhost:3434/health`

### Error: "401 Unauthorized" with valid token
- Check token hash in database matches (SHA256)
- Verify token status is 'active'
- Check token hasn't expired
- Ensure AUTH_ENABLED=true in data-ingestor config

### Error: "403 Forbidden" with Developer role
- Verify role has `metrics.ingest` and `logs.ingest` permissions
- Check role permissions in database:
  ```sql
  SELECT r.name, r.permissions
  FROM nexus_roles r
  JOIN nexus_api_tokens t ON t.role_id = r.uuid
  WHERE t.token_hash = '<sha256_hash>';
  ```

### Error: "Failed to create token"
- Ensure you're logged in (valid session cookie)
- Check backend is running on http://localhost:3000
- Verify you have permission to create API tokens

## Example Output

```
============================================================
Horux Data-Ingestor Authentication Test Suite
============================================================

Configuration:
  Ingestor URL: http://localhost:3434
  Resource ID: test-resource-uuid-1234
  API Token: horux_abc123def456...

[TEST] Checking data-ingestor health at http://localhost:3434
✓ Data-ingestor is healthy

[TEST] Testing invalid token (should get 401 Unauthorized)
✓ Got expected 401 Unauthorized: HTTP 401: Invalid authorization token

[TEST] Testing metrics ingestion with valid token
  → Sending counter metrics...
  → Sending gauge metric...
  → Sending histogram metric...
  → Sending summary metric...
  → Waiting for flush...
✓ Metrics ingestion successful

[TEST] Testing logs ingestion with valid token
  → Sending different log levels...
  → Sending log with distributed tracing...
  → Waiting for flush...
✓ Logs ingestion successful

============================================================
Test Results Summary
============================================================

✓ invalid_token: PASSED
✓ metrics: PASSED
✓ logs: PASSED

Total: 3 | Passed: 3 | Failed: 0 | Skipped: 0

All tests passed!
```

## Additional Tests

### Test with cURL

**Invalid token:**
```bash
curl -X POST http://localhost:3434/api/v1/metrics/push \
  -H "Authorization: Bearer invalid_token" \
  -H "Content-Type: application/json" \
  -d '{"resource_id":"test","metrics":[{"name":"test","type":"counter","value":1}]}'
# Expected: 401
```

**Valid token:**
```bash
curl -X POST http://localhost:3434/api/v1/metrics/push \
  -H "Authorization: Bearer <your_valid_token>" \
  -H "Content-Type: application/json" \
  -d '{"resource_id":"test-resource","metrics":[{"name":"test_metric","type":"counter","value":1,"timestamp":"2024-01-01T00:00:00Z"}]}'
# Expected: 200
```

## Notes

- Tokens are shown **only once** upon creation - save them securely
- Test tokens can be deleted via the Horux console after testing
- The data-ingestor updates `last_usage` timestamp on each successful request
- All ingested data is associated with the tenant from the API token
