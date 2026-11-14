# FixFlow API Contract

**Status:** design contract for the weekend build. The handlers and SAM template are not
implemented at this commit. This document is the contract the frontend and backend should
build against.

Base URL:

```text
https://<api-id>.execute-api.<AWS_REGION>.amazonaws.com
```

The HTTP API uses the `$default` stage, so there is no `/prod` prefix. The API is
unversioned for the challenge. If it survives the weekend, add `/v1` before exposing it to
external clients.

Related documents:

- [Architecture](architecture.md)
- [Data model](data-model.md)
- [Security](security.md)

---

## 1. Conventions

### Content type and encoding

- Requests and responses are `application/json; charset=utf-8`, except S3 uploads, which use `multipart/form-data`.
- Timestamps are ISO 8601 UTC with a `Z` suffix, for example `2026-11-14T18:30:00Z`. The
  server stores and returns millisecond precision when available; clients must parse ISO
  8601 and must not assume a fixed fractional length.
- IDs are opaque strings. Clients must not parse them.
- Unknown request fields are rejected with `400 VALIDATION_ERROR`. This prevents accidental mass assignment.

### Authentication

| Route class | Authentication |
| --- | --- |
| Public | None. Rate-limited and capability-based where applicable. |
| Admin | `Authorization: Bearer <Cognito access token>` validated by the API Gateway JWT authorizer, **plus** a backend check that `cognito:groups` contains `admin`. |

The frontend must never decide authorization on its own. Hiding an admin button is a UX
convenience, not a security control.

### CORS

- Allowed origin: the exact CloudFront distribution origin and `http://localhost:5173` in development.
- Allowed methods: `GET`, `POST`, `OPTIONS`.
- Allowed headers: `Authorization`, `Content-Type`.
- Credentials: not used. The JWT travels in the `Authorization` header.
- Preflight max age: 600 seconds.

Wildcard origins are not allowed in the deployed environment.

### Rate limits

These are target limits. API Gateway stage throttling is the baseline; a WAF rate-based
rule adds per-IP enforcement if the budget allows it.

| Scope | Target |
| --- | --- |
| API stage default | 20 requests/second, burst 40 |
| `POST /reports/upload-url` | 10 requests/minute per IP |
| `POST /reports` | 5 requests/minute per IP |
| Public tracking reads | 30 requests/minute per IP |
| Clarification responses | 5 requests/minute per IP |
| Admin routes | 10 requests/second per administrator |

Exceeding a limit returns `429 RATE_LIMITED` with `Retry-After`.

### Idempotency and optimistic concurrency

- `POST /reports` is idempotent on `reportId`. The browser generates a UUIDv4 once and
  reuses it on retry. The same `reportId` with the same canonical payload returns the
  original result (`200` instead of `201`). A different payload for an existing `reportId`
  returns `409 CONFLICT`. The canonical payload is UTF-8 JSON with lexicographically sorted
  keys, no insignificant whitespace, and only the documented request fields. `requestHash`
  is the SHA-256 of those bytes.
- Every admin mutation requires `expectedVersion`. The server performs a conditional update
  on `reportId + version + status`. A stale version returns `409 CONFLICT`.
- Workflow callbacks are single-use. A repeated callback returns `409 CONFLICT` rather than
  applying a second transition.

### Pagination

- `limit` is an integer from 1 to 100. Default 25.
- `cursor` is an opaque base64url token returned as `nextCursor`. Clients must not parse or construct it.
- `nextCursor` is `null` on the last page.
- Admin lists are newest first. Public timelines are oldest first.

### Error envelope

All errors use the same shape:

```json
{
  "error": {
    "code": "INVALID_STATE_TRANSITION",
    "message": "Report is RESOLVED and cannot be assigned.",
    "requestId": "01J8Z3Y6Q7K5M2N4P6R8S0T2V4",
    "details": {
      "currentStatus": "RESOLVED",
      "allowedActions": []
    }
  }
}
```

Rules:

- `message` is safe to display to a user.
- `requestId` is the API Gateway request ID and is also written to logs.
- `details` is optional and must not contain stack traces, ARNs, bucket names, tokens, or PII.
- `5xx` responses never include internal exception messages.

### Error codes

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | `VALIDATION_ERROR` | Missing, malformed, or unknown field. |
| 401 | `UNAUTHENTICATED` | Missing or invalid bearer token. |
| 403 | `FORBIDDEN` | Valid token, but not an administrator. |
| 404 | `NOT_FOUND` | Report or public reference not found. Public responses use a generic message. |
| 409 | `CONFLICT` | Stale `expectedVersion`, duplicate `reportId` with a different payload, or already-consumed callback. |
| 409 | `INVALID_STATE_TRANSITION` | The requested action is not allowed from the current status. |
| 409 | `REVIEW_EXPIRED` | The review callback token is no longer valid. |
| 413 | `PAYLOAD_TOO_LARGE` | Request body or evidence exceeds the limit. |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | Evidence type is not JPEG, PNG, or WebP. |
| 422 | `EVIDENCE_NOT_FOUND` | The uploaded object does not exist or does not match the signed metadata. |
| 429 | `RATE_LIMITED` | Rate limit exceeded. |
| 500 | `INTERNAL_ERROR` | Unhandled server error. |
| 503 | `SERVICE_UNAVAILABLE` | Dependency unavailable after retries. |

---

## 2. Authorization matrix

| Endpoint | Public | Admin | Notes |
| --- | --- | --- | --- |
| `GET /health` | Yes | Yes | Shallow health check. |
| `POST /reports/upload-url` | Yes | Yes | Rate-limited; no database write. |
| `POST /reports` | Yes | Yes | Idempotent on `reportId`. |
| `GET /public/reports/{publicRef}` | Yes | Yes | Filtered projection only. |
| `GET /public/reports/{publicRef}/events` | Yes | Yes | `visibility = PUBLIC` only. |
| `POST /public/reports/{publicRef}/clarification` | Yes | Yes | Capability-based on `publicRef`; only when `NEEDS_INFO`. |
| `GET /admin/reports` | No | Yes | Requires `admin` group. |
| `GET /admin/reports/{reportId}` | No | Yes | Full record and audit history. |
| `POST /admin/reports/{reportId}/decision` | No | Yes | Approve, reject, or request information. |
| `POST /admin/reports/{reportId}/assign` | No | Yes | Assign an approved report. |
| `POST /admin/reports/{reportId}/resolution/upload-url` | No | Yes | Presigned resolution evidence upload. |
| `POST /admin/reports/{reportId}/resolution` | No | Yes | Submit resolution evidence and note. |
| `POST /admin/reports/{reportId}/verify` | No | Yes | Verify or reject resolution evidence. |
| `GET /admin/metrics` | No | Yes | Excludes demo data by default. |

The MVP has no maintenance-team login. Administrators act on behalf of teams. A future
`team` Cognito group can reuse the assignment and resolution endpoints without changing
the data model.

---

## 3. Report lifecycle

```text
SUBMITTED
  -> VALIDATING
     -> VALIDATION_FAILED        terminal, system
     -> REVIEW_PENDING
        -> APPROVED              admin
           -> IN_PROGRESS        admin assigns team
              -> RESOLUTION_PENDING
                 -> RESOLVED     admin verifies
                 -> IN_PROGRESS  admin rejects evidence
        -> REJECTED              terminal, admin
        -> NEEDS_INFO            admin requests information
           -> REVIEW_PENDING     resident responds
        -> REVIEW_EXPIRED        system timeout
           -> APPROVED           admin
           -> REJECTED           admin
           -> NEEDS_INFO         admin
```

### Transition matrix

| From | Action | Actor | To | Endpoint |
| --- | --- | --- | --- | --- |
| `SUBMITTED` | Validate | System | `VALIDATING` | Workflow |
| `VALIDATING` | Validation passes | System | `REVIEW_PENDING` | Workflow |
| `VALIDATING` | Validation fails | System | `VALIDATION_FAILED` | Workflow |
| `REVIEW_PENDING` | Approve | Admin | `APPROVED` | `POST /admin/reports/{id}/decision` |
| `REVIEW_PENDING` | Reject | Admin | `REJECTED` | `POST /admin/reports/{id}/decision` |
| `REVIEW_PENDING` | Request info | Admin | `NEEDS_INFO` | `POST /admin/reports/{id}/decision` |
| `REVIEW_PENDING` | Review timeout | System | `REVIEW_EXPIRED` | Workflow |
| `REVIEW_EXPIRED` | Approve | Admin | `APPROVED` | `POST /admin/reports/{id}/decision` |
| `REVIEW_EXPIRED` | Reject | Admin | `REJECTED` | `POST /admin/reports/{id}/decision` |
| `REVIEW_EXPIRED` | Request info | Admin | `NEEDS_INFO` | `POST /admin/reports/{id}/decision` |
| `NEEDS_INFO` | Respond | Resident | `REVIEW_PENDING` | `POST /public/reports/{publicRef}/clarification` |
| `NEEDS_INFO` | Reject | Admin | `REJECTED` | `POST /admin/reports/{id}/decision` |
| `NEEDS_INFO` | Request info again | Admin | `NEEDS_INFO` | `POST /admin/reports/{id}/decision` |
| `APPROVED` | Assign team | Admin | `IN_PROGRESS` | `POST /admin/reports/{id}/assign` |
| `IN_PROGRESS` | Submit resolution | Admin | `RESOLUTION_PENDING` | `POST /admin/reports/{id}/resolution` |
| `RESOLUTION_PENDING` | Verify | Admin | `RESOLVED` | `POST /admin/reports/{id}/verify` |
| `RESOLUTION_PENDING` | Reject evidence | Admin | `IN_PROGRESS` | `POST /admin/reports/{id}/verify` |

`VALIDATION_FAILED`, `REJECTED`, and `RESOLVED` are terminal for the MVP. Any action from a
terminal state returns `409 INVALID_STATE_TRANSITION`.

---

## 4. Public endpoints

### 4.1 `GET /health`

Shallow liveness check. It does not query DynamoDB or Step Functions; a deep readiness
check would add cost and coupling without helping the demo.

**Response `200`**

```json
{
  "status": "ok",
  "service": "fixflow",
  "time": "2026-11-14T18:30:00Z"
}
```

### 4.2 `POST /reports/upload-url`

Create a short-lived presigned POST for a report photograph. This endpoint does not create
a database record.

**Request**

```json
{
  "reportId": "e3b0c442-98fc-4c14-9afb-f4c8996fb924",
  "contentType": "image/jpeg",
  "contentLength": 2483910
}
```

| Field | Rules |
| --- | --- |
| `reportId` | UUIDv4, generated by the browser and reused for the whole submission. |
| `contentType` | One of `image/jpeg`, `image/png`, `image/webp`. |
| `contentLength` | Greater than 0 and at most 10 MiB (10,485,760 bytes). |

**Response `200`**

```json
{
  "upload": {
    "url": "https://fixflow-evidence.s3.<region>.amazonaws.com/",
    "fields": {
      "key": "incoming/e3b0c442-98fc-4c14-9afb-f4c8996fb924/4f1c2d8e-7a6b-4c3d-9e2f-1a2b3c4d5e6f.jpg",
      "Content-Type": "image/jpeg",
      "x-amz-meta-report-id": "e3b0c442-98fc-4c14-9afb-f4c8996fb924",
      "x-amz-meta-upload-id": "4f1c2d8e-7a6b-4c3d-9e2f-1a2b3c4d5e6f",
      "policy": "...",
      "x-amz-algorithm": "AWS4-HMAC-SHA256",
      "x-amz-credential": "...",
      "x-amz-date": "...",
      "x-amz-signature": "..."
    }
  },
  "objectKey": "incoming/e3b0c442-98fc-4c14-9afb-f4c8996fb924/4f1c2d8e-7a6b-4c3d-9e2f-1a2b3c4d5e6f.jpg",
  "uploadId": "4f1c2d8e-7a6b-4c3d-9e2f-1a2b3c4d5e6f",
  "expiresAt": "2026-11-14T18:35:00Z"
}
```

**Upload instructions**

1. Send a `multipart/form-data` POST to `upload.url`.
2. Include every field in `upload.fields` exactly as returned.
3. Add the file last under the field name `file`.
4. Do not add extra fields; the signed policy rejects them.

The URL expires in 5 minutes. A report can have zero or one photograph in the MVP. The
object is copied to the evidence prefix during `POST /reports`; the `incoming/` prefix has a
short lifecycle expiry for abandoned uploads.

**Errors:** `400`, `413`, `415`, `429`.

### 4.3 `POST /reports`

Create a report and start the workflow. This is the only endpoint that creates a report.

**Request**

```json
{
  "reportId": "e3b0c442-98fc-4c14-9afb-f4c8996fb924",
  "category": "POTHOLE",
  "description": "Large pothole in the left lane, about 40 cm wide, causing cars to swerve.",
  "severity": "HIGH",
  "location": {
    "area": "Kariakoo",
    "latitude": -6.8189,
    "longitude": 39.2803
  },
  "evidence": {
    "objectKey": "incoming/e3b0c442-98fc-4c14-9afb-f4c8996fb924/4f1c2d8e-7a6b-4c3d-9e2f-1a2b3c4d5e6f.jpg",
    "uploadId": "4f1c2d8e-7a6b-4c3d-9e2f-1a2b3c4d5e6f",
    "contentType": "image/jpeg",
    "contentLength": 2483910
  }
}
```

| Field | Rules |
| --- | --- |
| `reportId` | UUIDv4. Acts as the idempotency key. |
| `category` | `POTHOLE`, `STREETLIGHT`, `WATER_LEAK`, `DRAINAGE`, `WASTE`, or `OTHER`. |
| `description` | 10 to 2,000 characters after trimming. Control characters are rejected. |
| `severity` | `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`. Self-reported; not a safety assessment. |
| `location.area` | 2 to 120 characters. Required. |
| `location.latitude` | Optional. If present, `longitude` is required. -90 to 90. |
| `location.longitude` | Optional. If present, `latitude` is required. -180 to 180. |
| `evidence` | Optional. If present, all four fields are required and must match the uploaded object. |
| `evidence.objectKey` | Must match `incoming/<reportId>/<uploadId>.<jpg|jpeg|png|webp>`. |
| `evidence.uploadId` | UUIDv4 from the upload-url response. |
| `evidence.contentType` | Must match the object metadata and the allowlist. |
| `evidence.contentLength` | Must match the object size and be at most 10 MiB. |

No resident name, email, phone number, or account is collected in the MVP. The public
reference is the only tracking credential.

**Response `201`**

```json
{
  "publicRef": "FF-7K3M-9Q2X-4T8N-6V2B",
  "status": "SUBMITTED",
  "createdAt": "2026-11-14T18:30:00Z",
  "trackingUrl": "https://<cloudfront-domain>/track/FF-7K3M-9Q2X-4T8N-6V2B"
}
```

**Response `200` (idempotent retry)**

Same body as the original success. The report already exists with the same canonical
payload. The client should treat `200` and `201` identically.

**Behavior**

1. Validate the schema and the evidence object.
2. Copy `incoming/...` to `evidence/<reportId>/<uploadId>.<ext>`.
3. Generate `publicRef` and `requestHash`.
4. Write the report (`SUBMITTED`) and the `REPORT_SUBMITTED` event in one transaction.
5. Start a Step Functions execution named `reportId`.
6. Return the public reference.

**Errors:** `400`, `409`, `413`, `415`, `422`, `429`, `500`.

### 4.4 `GET /public/reports/{publicRef}`

Look up a report by its public reference. The reference is case-insensitive and accepts
hyphens; it is normalized to uppercase.

**Response `200`**

```json
{
  "publicRef": "FF-7K3M-9Q2X-4T8N-6V2B",
  "category": "POTHOLE",
  "area": "Kariakoo",
  "status": "IN_PROGRESS",
  "priority": "P2_HIGH",
  "submittedAt": "2026-11-14T18:30:00Z",
  "resolvedAt": null,
  "publicSummary": "Report received and assigned to the roads team.",
  "lastUpdatedAt": "2026-11-14T19:10:00Z"
}
```

**Never returned**

- `reportId`, resident contact details, description, exact coordinates, evidence keys,
  presigned URLs, administrator identities, internal comments, or task tokens.

**Errors:** `404` with a generic message, `429`. A `404` can be transient for a few seconds
immediately after submission because `GSI2` is eventually consistent; the tracking page
retries for up to 5 seconds before showing "not found".

### 4.5 `GET /public/reports/{publicRef}/events`

Return the public timeline, oldest first.

**Response `200`**

```json
{
  "items": [
    {
      "eventId": "01J8Z3Y6Q7K5M2N4P6R8S0T2V4",
      "status": "SUBMITTED",
      "timestamp": "2026-11-14T18:30:00Z",
      "publicMessage": "Report received."
    },
    {
      "eventId": "01J8Z4A1B2C3D4E5F6G7H8J9K0",
      "status": "IN_PROGRESS",
      "timestamp": "2026-11-14T19:10:00Z",
      "publicMessage": "Assigned to the roads team."
    }
  ],
  "nextCursor": null
}
```

Only events with `visibility = PUBLIC` are returned. Admin comments, rejection reasons,
and evidence metadata are never public.

**Errors:** `404`, `429`.

### 4.6 `POST /public/reports/{publicRef}/clarification`

Respond to an administrator request for more information. Possession of the high-entropy
`publicRef` is the capability in the MVP.
**Request**

```json
{
  "message": "The pothole is outside 42 Market Street, next to the blue gate. It is worse after rain."
}
```

| Field | Rules |
| --- | --- |
| `message` | 1 to 2,000 characters after trimming. |

**Response `200`**

```json
{
  "publicRef": "FF-7K3M-9Q2X-4T8N-6V2B",
  "status": "REVIEW_PENDING",
  "updatedAt": "2026-11-14T20:00:00Z"
}
```

**Rules**

- Allowed only when the report is `NEEDS_INFO` and `requiredActor = RESIDENT`.
- Appends a `CLARIFICATION_PROVIDED` event.
- Transitions `NEEDS_INFO -> REVIEW_PENDING` with a condition on the current status. No
  client-supplied version is required because the resident has no admin session.
- Starts a new Step Functions execution with `resumeFrom = REVIEW` and
  name `<reportId>-review-<eventId>`. The original execution ended at `NEEDS_INFO`.
- If the report is not `NEEDS_INFO`, returns `409 INVALID_STATE_TRANSITION`.
- A repeated response after the first succeeds returns `409 INVALID_STATE_TRANSITION`, which
  is safe: the first response already moved the report back to review.
- If the status transition commits but `StartExecution` fails, the endpoint returns `503`.
  A retry with the same message is idempotent: when the latest event is
  `CLARIFICATION_PROVIDED` with the same message hash, it retries the deterministic
  execution name instead of returning `INVALID_STATE_TRANSITION`.

**Errors:** `400`, `404`, `409`, `429`.

---

## 5. Admin endpoints

All admin endpoints require a valid Cognito token with the `admin` group.

### 5.1 `GET /admin/reports`

List reports, newest first. If `status` is present, the query uses the status index;
otherwise it uses the all-reports index. `category` is applied as a filter on the returned
page.

**Query parameters**

| Name | Rules |
| --- | --- |
| `status` | Optional. One lifecycle status. |
| `category` | Optional. One category. |
| `limit` | Optional. 1 to 100. Default 25. |
| `cursor` | Optional. Opaque pagination token. |
| `includeDemo` | Optional. `true` or `false`. Default `false`. |

**Response `200`**

```json
{
  "items": [
    {
      "reportId": "e3b0c442-98fc-4c14-9afb-f4c8996fb924",
      "publicRef": "FF-7K3M-9Q2X-4T8N-6V2B",
      "category": "POTHOLE",
      "area": "Kariakoo",
      "status": "REVIEW_PENDING",
      "priority": "P2_HIGH",
      "assignedTeam": null,
      "createdAt": "2026-11-14T18:30:00Z",
      "updatedAt": "2026-11-14T18:30:05Z",
      "dataSource": "LIVE"
    }
  ],
  "nextCursor": null
}
```

**Errors:** `400`, `401`, `403`, `429`.

### 5.2 `GET /admin/reports/{reportId}`

Return the full report, audit history, and short-lived evidence URLs.

**Response `200`**

```json
{
  "reportId": "e3b0c442-98fc-4c14-9afb-f4c8996fb924",
  "publicRef": "FF-7K3M-9Q2X-4T8N-6V2B",
  "category": "POTHOLE",
  "description": "Large pothole in the left lane, about 40 cm wide, causing cars to swerve.",
  "severity": "HIGH",
  "priority": "P2_HIGH",
  "priorityScore": 50,
  "status": "REVIEW_PENDING",
  "version": 2,
  "location": {
    "area": "Kariakoo",
    "latitude": -6.8189,
    "longitude": 39.2803
  },
  "assignedTeam": null,
  "evidence": {
    "url": "https://fixflow-evidence.s3.<region>.amazonaws.com/evidence/...?X-Amz-Signature=...",
    "contentType": "image/jpeg",
    "expiresAt": "2026-11-14T18:40:00Z"
  },
  "resolution": null,
  "createdAt": "2026-11-14T18:30:00Z",
  "updatedAt": "2026-11-14T18:30:05Z",
  "reviewedAt": null,
  "approvedAt": null,
  "resolvedAt": null,
  "workflowStatus": "STARTED",
  "dataSource": "LIVE",
  "events": [
    {
      "eventId": "01J8Z3Y6Q7K5M2N4P6R8S0T2V4",
      "actorType": "RESIDENT",
      "action": "REPORT_SUBMITTED",
      "previousStatus": null,
      "newStatus": "SUBMITTED",
      "timestamp": "2026-11-14T18:30:00Z",
      "comment": null,
      "visibility": "PUBLIC"
    }
  ]
}
```

- Evidence URLs expire in 5 minutes and are generated on read. They are not stored.
- `Cache-Control: no-store` is mandatory.
- `pendingTaskToken` and any token table data are never returned.

**Errors:** `401`, `403`, `404`.

### 5.3 `POST /admin/reports/{reportId}/decision`

Approve, reject, or request more information. Accepted from `REVIEW_PENDING` or
`REVIEW_EXPIRED` for all decisions. `NEEDS_INFO` accepts `REJECT` and `REQUEST_INFO` so an
administrator can close an unresponsive report or re-send a clarification request.

**Request**

```json
{
  "decision": "APPROVE",
  "comment": "Verified the location and severity. Assigning to the roads team.",
  "publicMessage": "Report reviewed and approved for action.",
  "expectedVersion": 2
}
```

| Field | Rules |
| --- | --- |
| `decision` | `APPROVE`, `REJECT`, or `REQUEST_INFO`. |
| `comment` | Optional for `APPROVE`; required for `REJECT` and `REQUEST_INFO`. 1 to 2,000 characters. Admin-only. |
| `publicMessage` | Optional. 1 to 280 characters. Shown on the public timeline. If omitted, the server uses a generic default for the action. |
| `expectedVersion` | Required. Must match the current report version. |

**Response `200`**

```json
{
  "reportId": "e3b0c442-98fc-4c14-9afb-f4c8996fb924",
  "status": "APPROVED",
  "version": 3,
  "updatedAt": "2026-11-14T19:00:00Z"
}
```

**Rules**

- `APPROVE`: `REVIEW_PENDING|REVIEW_EXPIRED -> APPROVED`; sets `approvedAt` and, if absent, `reviewedAt`.
- `REJECT`: `REVIEW_PENDING|REVIEW_EXPIRED|NEEDS_INFO -> REJECTED`; sets `reviewedAt` if absent; requires a reason.
- `REQUEST_INFO`: `REVIEW_PENDING|REVIEW_EXPIRED -> NEEDS_INFO`; sets `requiredActor = RESIDENT` and `reviewedAt` if absent.
- `REQUEST_INFO` from `NEEDS_INFO` stays in `NEEDS_INFO`: it updates the request and appends
  a new `INFO_REQUESTED` event, which is useful when the original request was lost.
- The status update and event insert are one DynamoDB transaction.
- For `REVIEW_PENDING` and `REVIEW_EXPIRED`, the handler consumes the latest `REVIEW` task
  token and calls `SendTaskSuccess`.
- For `NEEDS_INFO`, there is no waiting token. The handler records the decision and returns.
- If a review token is missing or already consumed, returns `409 REVIEW_EXPIRED`.

**Errors:** `400`, `401`, `403`, `404`, `409`.

### 5.4 `POST /admin/reports/{reportId}/assign`

Assign an approved report to a maintenance team. The MVP stores the team as a label; it
does not create team identities.

**Request**

```json
{
  "assignedTeam": "Roads Team A",
  "comment": "Assigned to the crew covering Kariakoo.",
  "publicMessage": "Assigned to the roads team.",
  "expectedVersion": 3
}
```

| Field | Rules |
| --- | --- |
| `assignedTeam` | 2 to 80 characters. |
| `comment` | Optional. 1 to 2,000 characters. Admin-only. |
| `publicMessage` | Optional. 1 to 280 characters. |
| `expectedVersion` | Required. |

**Response `200`**

```json
{
  "reportId": "e3b0c442-98fc-4c14-9afb-f4c8996fb924",
  "status": "IN_PROGRESS",
  "assignedTeam": "Roads Team A",
  "version": 4,
  "updatedAt": "2026-11-14T19:10:00Z"
}
```

**Rules**

- Allowed only from `APPROVED`.
- Sets `assignedAt` and `assignedTeam`.
- Resumes the `ASSIGNMENT` callback.

**Errors:** `400`, `401`, `403`, `404`, `409`.

### 5.5 `POST /admin/reports/{reportId}/resolution/upload-url`

Create a presigned POST for resolution evidence. One image per resolution in the MVP.

**Request**

```json
{
  "contentType": "image/jpeg",
  "contentLength": 3120044
}
```

**Response `200`**

```json
{
  "upload": {
    "url": "https://fixflow-evidence.s3.<region>.amazonaws.com/",
    "fields": {
      "key": "incoming/resolution/e3b0c442-98fc-4c14-9afb-f4c8996fb924/9a8b7c6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d.jpg",
      "Content-Type": "image/jpeg",
      "x-amz-meta-report-id": "e3b0c442-98fc-4c14-9afb-f4c8996fb924",
      "x-amz-meta-upload-id": "9a8b7c6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d",
      "policy": "...",
      "x-amz-algorithm": "AWS4-HMAC-SHA256",
      "x-amz-credential": "...",
      "x-amz-date": "...",
      "x-amz-signature": "..."
    }
  },
  "objectKey": "incoming/resolution/e3b0c442-98fc-4c14-9afb-f4c8996fb924/9a8b7c6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d.jpg",
  "uploadId": "9a8b7c6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d",
  "expiresAt": "2026-11-14T19:15:00Z"
}
```

Allowed only when the report is `IN_PROGRESS`. Same content types and 10 MiB limit as
report evidence. The object is copied to `resolution/<reportId>/<uploadId>.<ext>` during
submission.

**Errors:** `400`, `401`, `403`, `404`, `409`, `413`, `415`.

### 5.6 `POST /admin/reports/{reportId}/resolution`

Submit completion evidence and a note. Accepted only from `IN_PROGRESS`.

**Request**

```json
{
  "evidence": {
    "objectKey": "incoming/resolution/e3b0c442-98fc-4c14-9afb-f4c8996fb924/9a8b7c6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d.jpg",
    "uploadId": "9a8b7c6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d",
    "contentType": "image/jpeg",
    "contentLength": 3120044
  },
  "note": "Pothole filled, compacted, and the lane reopened.",
  "publicMessage": "Repair completed and awaiting verification.",
  "expectedVersion": 4
}
```

| Field | Rules |
| --- | --- |
| `evidence` | Required. Same validation as report evidence, with the resolution key prefix. |
| `note` | Required. 1 to 2,000 characters. Admin-only. |
| `publicMessage` | Optional. 1 to 280 characters. |
| `expectedVersion` | Required. |

**Response `200`**

```json
{
  "reportId": "e3b0c442-98fc-4c14-9afb-f4c8996fb924",
  "status": "RESOLUTION_PENDING",
  "version": 5,
  "updatedAt": "2026-11-14T20:00:00Z"
}
```

**Rules**

- `IN_PROGRESS -> RESOLUTION_PENDING`.
- Sets `resolutionEvidenceKey`, `resolutionNote`, and `resolutionSubmittedAt`.
- Resumes the `RESOLUTION` callback.

**Errors:** `400`, `401`, `403`, `404`, `409`, `413`, `415`, `422`.

### 5.7 `POST /admin/reports/{reportId}/verify`

Verify or reject the submitted resolution evidence.

**Request**

```json
{
  "decision": "VERIFIED",
  "comment": "Evidence matches the reported location and the repair is complete.",
  "publicMessage": "Repair verified and report closed.",
  "expectedVersion": 5
}
```

| Field | Rules |
| --- | --- |
| `decision` | `VERIFIED` or `REJECTED`. |
| `comment` | Required for `REJECTED`; optional for `VERIFIED`. 1 to 2,000 characters. |
| `publicMessage` | Optional. 1 to 280 characters. |
| `expectedVersion` | Required. |

**Response `200`**

```json
{
  "reportId": "e3b0c442-98fc-4c14-9afb-f4c8996fb924",
  "status": "RESOLVED",
  "version": 6,
  "resolvedAt": "2026-11-14T21:00:00Z",
  "updatedAt": "2026-11-14T21:00:00Z"
}
```

**Rules**

- `VERIFIED`: `RESOLUTION_PENDING -> RESOLVED`; sets `resolvedAt`.
- `REJECTED`: `RESOLUTION_PENDING -> IN_PROGRESS`; stores `resolutionRejectionReason`.
- Resumes the `VERIFICATION` callback.
- A resolution is never marked `RESOLVED` by the system or by AI.

**Errors:** `400`, `401`, `403`, `404`, `409`.

### 5.8 `GET /admin/metrics`

Operational metrics calculated from application records. Demo records are excluded unless
`includeDemo=true`.

**Query parameters**

| Name | Rules |
| --- | --- |
| `includeDemo` | Optional. `true` or `false`. Default `false`. |

**Response `200`**

```json
{
  "totalReports": 18,
  "awaitingReview": 4,
  "assigned": 3,
  "resolved": 7,
  "avgSubmissionToReviewSeconds": 2520,
  "avgApprovalToResolutionSeconds": 22800,
  "demoDataIncluded": false,
  "generatedAt": "2026-11-14T22:00:00Z"
}
```

Definitions:

- `totalReports`: all non-demo reports.
- `awaitingReview`: `REVIEW_PENDING` + `REVIEW_EXPIRED`.
- `assigned`: `IN_PROGRESS`.
- `resolved`: `RESOLVED`.
- `avgSubmissionToReviewSeconds`: average of `reviewedAt - createdAt` for reports with `reviewedAt`.
- `avgApprovalToResolutionSeconds`: average of `resolvedAt - approvedAt` for reports with both timestamps.
- Averages are `null` when there are no qualifying records.

**Errors:** `401`, `403`, `429`.

---

## 6. Error and retry guidance for clients

| Situation | Client behavior |
| --- | --- |
| `401` | Re-authenticate through Cognito; do not retry the same token. |
| `403` | Show an access-denied state; do not retry. |
| `409 CONFLICT` | Refetch the report; the version or status changed. |
| `409 INVALID_STATE_TRANSITION` | Refetch and show the current status; do not force the action. |
| `409 REVIEW_EXPIRED` | Refetch; the review window closed. An administrator can still act from the dashboard if the workflow issued a new token. |
| `422 EVIDENCE_NOT_FOUND` | Ask the user to upload again; do not retry the same object key. |
| `429` | Honor `Retry-After` and retry with exponential backoff and jitter. |
| `5xx` | Retry idempotent reads and `POST /reports` with the same `reportId`. Do not retry admin mutations without refetching `version`. |

## 7. Contract decisions and deviations from the PDF

| # | PDF endpoint or rule | Change | Reason |
| --- | --- | --- | --- |
| 1 | `GET /reports/{reportId}` public tracking | Replaced by `GET /public/reports/{publicRef}`. | The internal ID should not be a public capability. |
| 2 | `POST /reports/{reportId}/resolution/upload-url` | Moved under `/admin/`. | No maintenance-team login in the MVP. |
| 3 | `POST /reports/{reportId}/resolution` | Moved under `/admin/`. | Same. |
| 4 | `POST /admin/reports/{reportId}/verify` | Kept; added `decision` and `expectedVersion`. | Explicit verify/reject and optimistic concurrency. |
| 5 | No clarification response endpoint | Added `POST /public/reports/{publicRef}/clarification`. | A resident must be able to answer `NEEDS_INFO`. |
| 6 | No health or metrics endpoint | Added `GET /health` and `GET /admin/metrics`. | Deployment verification and operational demo. |
| 7 | Decision endpoint had no version guard | Added `expectedVersion` to every admin mutation. | Prevent double decisions and lost updates. |
| 8 | Public response unspecified | Defined a strict public projection. | No PII, no internal IDs, no evidence keys. |
| 9 | `POST /reports/upload-url` returned a URL only | Returns a presigned POST with exact key, type, size, and metadata conditions. | Enforce ownership and limits at the storage boundary. |
| 10 | Idempotency left open | `reportId` is generated once by the browser and reused. | Retries are safe without an extra idempotency table. |
| 11 | `SUBMITTED`, `VALIDATING`, ... statuses | Added `VALIDATION_FAILED` and `REVIEW_EXPIRED`; `REVIEW_EXPIRED` is still actionable by an admin. | Cover system failure and timeout paths. |
| 12 | No public summary field | Added optional `publicMessage` on admin actions. | The public timeline needs a safe, curated message instead of raw admin notes. |
