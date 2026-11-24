# INSHO Backend

FastAPI backend for INSHO by Pavonine. This document describes the backend architecture, authentication and authorization, project and event management APIs, and currently registered routes.

## Run locally

From this directory, install the dependencies, configure the database, apply migrations, and start the API:

```bash
cd insho-backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set DATABASE_URL in .env to your local PostgreSQL database.
alembic upgrade head
uvicorn app.main:app --reload
```

The API listens at `http://localhost:8000` by default. Interactive Swagger documentation is at `/docs`, ReDoc at `/redoc`, and the OpenAPI document at `/openapi.json`.

`DATABASE_URL` is required for application database access. `AUTH_SESSION_TTL_MINUTES` sets opaque bearer session lifetime (default `30`, allowed range `5`–`1440`). `TEST_DATABASE_URL` is used by the test suite. Keep real environment files and credentials out of source control.

Run the test suite against a dedicated PostgreSQL database whose name contains `insho_test_db`:

```bash
TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@localhost:5432/insho_test_db' python -m pytest -q
```

The test fixtures create and drop their own tables in that database. Do not point `TEST_DATABASE_URL` at a development or production database.

## Application entry points

| Entry point | Purpose |
| --- | --- |
| `app/main.py` | Creates the FastAPI app, serves `/` and `/health`, and mounts the versioned router at `/api/v1`. |
| `app/api/v1/router.py` | Registers authentication, organization access, health, projects, events, venues, uploads, assets, context, event types, venue zones/sources, Design DNA, Project Memory, AI execution, and design-intelligence routers. |
| `app/auth.py` | Resolves bearer credentials to an active user and unexpired, non-revoked session. |
| `app/security.py` | Checks organization membership, role permissions, and resource ownership before reads and writes. |
| `app/services/auth.py` | Implements login, session refresh, and logout. |
| `app/services/organization_access.py` | Implements invitations, membership changes, and initial-owner claiming. |

## API conventions

- API routes are versioned under `/api/v1`.
- Routes marked **Bearer required** expect `Authorization: Bearer <access_token>`.
- JSON request bodies use the Pydantic schemas named in the endpoint table; invalid fields receive FastAPI validation errors (`422`). IDs are UUIDs unless stated otherwise.
- Successful creates generally return `201`; deletes return `204` with no body.
- Protected resources from another organization are hidden with `404`. Authenticated users without write permission receive `403`.
- `GET /` and both health checks are public. Login and invitation acceptance are public entry points; all other API routes require a valid session.

## Authentication flow

There is no public open-registration endpoint. A new user joins through an organization invitation.

1. An organization owner or admin creates an invitation using `POST /api/v1/organizations/{organization_id}/invitations`. The response includes a one-time raw invitation `token`; only its hash is stored. The invitation expires after seven days. Email delivery is not integrated, so the caller must securely deliver the token.
2. The invitee submits the token, their full name, and a password to `POST /api/v1/auth/accept-invitation`. New accounts require a password of at least 14 characters. Existing accounts must provide their current password and join using the invited email address. Successful acceptance creates a bearer session and returns it.
3. Send the returned `access_token` as `Authorization: Bearer <access_token>` on protected routes.
4. Call `POST /api/v1/auth/refresh` with the current bearer token to rotate the session. The old token is revoked as part of rotation.
5. Call `POST /api/v1/auth/logout` to revoke the current session. The user profile is available at `GET /api/v1/auth/me`.

Passwords are hashed using Argon2. Session tokens are random opaque values; only SHA-256 token hashes are persisted. Expired, revoked, malformed, or inactive-user sessions are rejected. Login returns the same generic credential error for unknown users and incorrect passwords.

### Login request example

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "person@example.com",
  "password": "your-password"
}
```

The response includes `access_token`, `token_type` (`bearer`), `expires_at`, and a safe user profile (`id`, `full_name`, `email`, `is_active`, `created_at`). Password hashes and system-admin flags are not returned.

## Roles and authorization

Organization roles are `owner`, `admin`, `designer`, and `member`.

| Role | Read organization resources | Create/update/delete resources | Manage membership |
| --- | --- | --- | --- |
| Owner | Yes | Yes | Full organization administration, subject to retaining an owner |
| Admin | Yes | Yes | Invite designer/member; manage ordinary members. Cannot manage owners or admins, or invite another admin. |
| Designer | Yes | Yes | No |
| Member | Yes | No | No |

Owners and admins can invite designers and members. Only owners can invite admins. Owner invitations are not allowed; an owner role is assigned by an existing owner or claimed through the initial-owner bootstrap endpoint. A user cannot change their own role, and an organization cannot lose its last owner.

The `event-types` collection is a global catalog: any authenticated user can read it, while only a separately provisioned system administrator can create, edit, or delete catalog entries. Organization roles do not grant system-administrator rights.

## Endpoint reference

`{organization_id}`, `{user_id}`, and other named path values are UUIDs. Query parameters are shown explicitly.

### Public and health

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/` | Public | API welcome/status response. |
| `GET` | `/health` | Public | Root health check. |
| `GET` | `/api/v1/health` | Public | Versioned health check. |

### Authentication

| Method | Path | Access | Request / result |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/login` | Public | `LoginRequest` → bearer token, expiry, and user. |
| `POST` | `/api/v1/auth/accept-invitation` | Public | `AcceptInvitationRequest` → membership, user, and bearer token. |
| `GET` | `/api/v1/auth/me` | Bearer required | Returns the current safe user profile. |
| `POST` | `/api/v1/auth/refresh` | Bearer required | Rotates the current session and returns a new token. |
| `POST` | `/api/v1/auth/logout` | Bearer required | Revokes the current session; `204` response. |

`LoginRequest`: `{ "email": "...", "password": "..." }`.

`AcceptInvitationRequest`: `{ "token": "...", "full_name": "...", "password": "..." }`. `full_name` may be omitted only when accepting with an existing account; new accounts need a non-blank full name and a password with at least 12 characters.

### File uploads

Batch 1 provides private local storage and an authenticated upload/retrieval foundation. Configure `STORAGE_PROVIDER=local` (the default), `LOCAL_STORAGE_ROOT=./storage`, `MAX_UPLOAD_SIZE_BYTES=104857600`, `UPLOAD_ALLOWED_EXTENSIONS=.jpg,.png,.webp,.mp4,.pdf,.svg`, and the corresponding `UPLOAD_ALLOWED_MIME_TYPES` in `.env`. The storage root is not served as static content. Use a dedicated persistent volume for local deployments; cloud credentials and buckets are not configured by this batch.

Uploads accept multipart form data with an `organization_id`, optional `project_id` or `event_id`, and a `file`. Supported inputs are JPEG, PNG, WebP, MP4, PDF, and SVG, subject to the configured extension/MIME allowlists and content checks. Every file receives a server-generated opaque storage key; the original filename is retained only as metadata. Project and event scope is checked against the organization and relationship graph. Upload and retrieval require a valid bearer session and organization access. Retrieval streams the file as a private attachment; SVG is returned as `application/octet-stream` with `nosniff` to prevent inline execution. Safe SVG previews are not implemented.

| Method | Path | Access | Request / result |
| --- | --- | --- | --- |
| `POST` | `/api/v1/uploads` | Bearer; organization editor | Multipart form: `organization_id`, optional `project_id` and `event_id`, and `file`. Returns the opaque `storage_key`, original filename, media type, size, and scope (`201`). |
| `GET` | `/api/v1/uploads/{storage_key}` | Bearer; organization member in the stored scope | Streams the authorized file with attachment, private no-store, and content-sniffing protection headers. |

The storage service defines a provider boundary around store, open, existence, metadata, and delete operations. The local filesystem provider is implemented; an S3-compatible adapter for R2, S3, or MinIO can implement the same boundary later without coupling upload business logic to a vendor SDK. Batch 1 does not include the Asset database model/library, thumbnails, transformations, video processing, or cloud provisioning.

Batch 2 adds Asset records and logical folders. A successful upload creates an Asset row with scope, original/display names, MIME type, extension, size, SHA-256, category, uploader, lifecycle state, and the private storage reference. Asset responses omit the storage key and provider. Upload responses still include the Batch 1 opaque storage key for compatibility; managed retrieval should use the asset ID endpoint below. Assets are soft-deleted and can be restored; bytes remain in storage. Folder deletion is also soft and allowed only when there are no child folders or assets. This avoids accidental asset deletion; empty folders can be recreated after deletion.

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/assets` | Bearer; organization member | Lists assets with optional scope, category/MIME/extension/status/date filters, bounded search, allowlisted sorting, and pagination (`offset`, `limit` up to 100). |
| `GET` | `/api/v1/assets/{asset_id}` | Bearer; member of asset organization | Reads safe asset metadata. |
| `PATCH` | `/api/v1/assets/{asset_id}` | Bearer; editor | Updates display name, description, or category. Storage and ownership fields are immutable through this API. |
| `POST` | `/api/v1/assets/{asset_id}/move` | Bearer; editor | Moves an asset to an authorized workspace/project/event/folder scope. Relationships are validated together. |
| `GET` | `/api/v1/assets/{asset_id}/download` | Bearer; member of asset organization | Streams the file after authorizing the Asset row; SVG is always an attachment. |
| `DELETE` | `/api/v1/assets/{asset_id}` | Bearer; editor | Soft-deletes the asset; file bytes remain stored. |
| `POST` | `/api/v1/assets/{asset_id}/restore` | Bearer; editor | Restores a soft-deleted asset after revalidating its scope and folder. |
| `POST` | `/api/v1/assets/folders` | Bearer; editor | Creates an organization/workspace/project folder, optionally nested under a same-scope parent. |
| `GET` | `/api/v1/assets/folders` | Bearer; organization member | Lists folders in the authorized organization and optional workspace/project scope. |
| `GET` | `/api/v1/assets/folders/{folder_id}` | Bearer; folder scope member | Reads folder metadata. |
| `PATCH` | `/api/v1/assets/folders/{folder_id}` | Bearer; editor | Renames or reparents a folder within its existing scope; cycles and duplicate sibling names are rejected. |
| `DELETE` | `/api/v1/assets/folders/{folder_id}` | Bearer; editor | Soft-deletes an empty folder. Nonempty folders receive `409 Conflict`. |

### Organization access and invitations

| Method | Path | Access | Request / result |
| --- | --- | --- | --- |
| `POST` | `/api/v1/organizations/{organization_id}/claim-owner` | Organization member | Bootstrap ownership when no owner exists; only the earliest eligible member can claim. |
| `GET` | `/api/v1/organizations/{organization_id}/members` | Organization member | Lists member profiles and roles. |
| `PATCH` | `/api/v1/organizations/{organization_id}/members/{user_id}` | Owner/admin by target role | `OrganizationMemberUpdate` changes a member role. |
| `DELETE` | `/api/v1/organizations/{organization_id}/members/{user_id}` | Owner/admin by target role | Removes a member; `204` response. |
| `POST` | `/api/v1/organizations/{organization_id}/invitations` | Owner/admin | `InvitationCreate` creates a seven-day invitation and returns the raw token once (`201`). |
| `DELETE` | `/api/v1/organizations/{organization_id}/invitations/{invitation_id}` | Owner/admin by invite role | Revokes an unaccepted invitation; `204` response. |

`InvitationCreate`: `{ "email": "person@example.com", "role": "designer" }` where role is `admin`, `designer`, or `member`. `OrganizationMemberUpdate`: `{ "role": "owner" | "admin" | "designer" | "member" }` subject to role rules above.

### Projects and events

| Method | Path | Access | Request / query |
| --- | --- | --- | --- |
| `POST` | `/api/v1/projects/` | Bearer; editor role for organization | `ProjectCreate`. Requires `organization_id`, `workspace_id`, `name`; accepts description, status, budget, guest count, event dates, and constraints. |
| `GET` | `/api/v1/projects/` | Bearer; organization member | Required `organization_id`; optional `status`, `workspace_id`, `offset` (default `0`), and `limit` (default `50`, max `100`). |
| `GET` | `/api/v1/projects/{project_id}` | Bearer; organization member | Reads a project within the caller's organization. |
| `GET` | `/api/v1/projects/{project_id}/events` | Bearer; organization member | Lists events attached to the project; optional `offset` and `limit`. |
| `PATCH` | `/api/v1/projects/{project_id}` | Bearer; editor role | `ProjectUpdate` (partial fields); include returned `version` to reject stale writes with `409`. Archived projects cannot be reopened. |
| `DELETE` | `/api/v1/projects/{project_id}` | Bearer; editor role | Deletes project; `204` response. |
| `POST` | `/api/v1/events/` | Bearer; editor role for project | `EventCreate`. Requires `project_id`, `name`; organization ownership is derived from the project. Accepts optional `venue_id`, event type, description, date, timezone-aware schedule, guest count, budget, currency, and status. |
| `GET` | `/api/v1/events/` | Bearer; organization member | Required `organization_id`; optional `project_id`, `offset`, and `limit`. |
| `GET` | `/api/v1/events/{event_id}` | Bearer; organization member | Reads an event in the caller's organization. |
| `PATCH` | `/api/v1/events/{event_id}` | Bearer; editor role | `EventUpdate` (partial fields); include returned `version` to reject stale writes with `409`. |
| `DELETE` | `/api/v1/events/{event_id}` | Bearer; editor role | Deletes event; `204` response. |

### Venues, zones, and sources

| Method | Path | Access | Request / query |
| --- | --- | --- | --- |
| `POST` | `/api/v1/venues/` | Bearer; editor role for organization | `VenueCreate`. Requires `organization_id`, `name`; accepts address, city, country, area. |
| `GET` | `/api/v1/venues/` | Bearer; organization member | Required query: `organization_id`. |
| `GET` | `/api/v1/venues/{venue_id}` | Bearer; organization member | Reads a venue in the caller's organization. |
| `PATCH` | `/api/v1/venues/{venue_id}` | Bearer; editor role | `VenueUpdate` (partial venue fields). |
| `DELETE` | `/api/v1/venues/{venue_id}` | Bearer; editor role | Deletes venue; `204` response. |
| `POST` | `/api/v1/venue-zones/` | Bearer; editor role for venue organization | `VenueZoneCreate`: `venue_id`, `name`, optional area and description. |
| `GET` | `/api/v1/venue-zones/` | Bearer; venue organization member | Required query: `venue_id`. |
| `GET` | `/api/v1/venue-zones/{zone_id}` | Bearer; venue organization member | Reads a zone through its parent venue. |
| `PATCH` | `/api/v1/venue-zones/{zone_id}` | Bearer; editor role | `VenueZoneUpdate` (partial zone fields). |
| `DELETE` | `/api/v1/venue-zones/{zone_id}` | Bearer; editor role | Deletes zone; `204` response. |
| `POST` | `/api/v1/venue-sources/` | Bearer; editor role for venue organization | `VenueSourceCreate`: `venue_id`, `source_type`, optional `file_url` and `source_metadata`. |
| `GET` | `/api/v1/venue-sources/` | Bearer; venue organization member | Required query: `venue_id`. |
| `GET` | `/api/v1/venue-sources/{source_id}` | Bearer; venue organization member | Reads a source through its parent venue. |
| `PATCH` | `/api/v1/venue-sources/{source_id}` | Bearer; editor role | `VenueSourceUpdate` (partial source fields). |
| `DELETE` | `/api/v1/venue-sources/{source_id}` | Bearer; editor role | Deletes source; `204` response. |

### Context retrieval

These authenticated read endpoints return deterministic, tenant-scoped domain context. Related records are resolved through existing project/event/venue relationships; missing venues are returned as absent, and venue deletion clears an event's optional venue link.

| Method | Path | Access | Result |
| --- | --- | --- | --- |
| `GET` | `/api/v1/context/projects/{project_id}` | Bearer; project organization member | Project, its events, event venues and zones, Design DNA, legacy Project Memory, and active memory items. |
| `GET` | `/api/v1/context/events/{event_id}` | Bearer; event organization member | Event, parent project when present, project design/memory context, and its venue and zones. Event-specific memory is included with project-wide items. |
| `GET` | `/api/v1/context/venues/{venue_id}` | Bearer; venue organization member | Venue, zones, linked events, and the related projects, Design DNA, and memory. |

### Event types (global catalog)

All event-type endpoints require authentication. Reads are available to authenticated users; mutations additionally require system-admin provisioning.

| Method | Path | Access | Request / result |
| --- | --- | --- | --- |
| `POST` | `/api/v1/event-types/` | System administrator | `EventTypeCreate`: `name`, optional `description`. |
| `GET` | `/api/v1/event-types/` | Any authenticated user | Lists global event types. |
| `GET` | `/api/v1/event-types/{event_type_id}` | Any authenticated user | Reads one event type. |
| `PATCH` | `/api/v1/event-types/{event_type_id}` | System administrator | `EventTypeUpdate` (partial fields). |
| `DELETE` | `/api/v1/event-types/{event_type_id}` | System administrator | Deletes event type; `204` response. |

### Design DNA and Project Memory

| Method | Path | Access | Request / result |
| --- | --- | --- | --- |
| `POST` | `/api/v1/design-dna/` | Bearer; editor role for project organization | `DesignDNACreate`: `project_id` and optional palette, materials, floral, lighting, architecture, motifs, cultural inspiration, mood, formality, luxury, confidence. |
| `GET` | `/api/v1/design-dna/project/{project_id}` | Bearer; project organization member | Reads Design DNA for a project. |
| `GET` | `/api/v1/design-dna/{design_dna_id}` | Bearer; project organization member | Reads one Design DNA record. |
| `PATCH` | `/api/v1/design-dna/{design_dna_id}` | Bearer; editor role | `DesignDNAUpdate` (partial design fields). |
| `DELETE` | `/api/v1/design-dna/{design_dna_id}` | Bearer; editor role | Deletes record; `204` response. |
| `POST` | `/api/v1/project-memory/` | Bearer; editor role for project organization | `ProjectMemoryCreate`: `project_id`, optional `memory_data` and `summary`. |
| `GET` | `/api/v1/project-memory/project/{project_id}` | Bearer; project organization member | Reads Project Memory for a project. |
| `GET` | `/api/v1/project-memory/{memory_id}` | Bearer; project organization member | Reads one Project Memory record. |
| `PATCH` | `/api/v1/project-memory/{memory_id}` | Bearer; editor role | `ProjectMemoryUpdate` (partial memory fields). |
| `DELETE` | `/api/v1/project-memory/{memory_id}` | Bearer; editor role | Deletes record; `204` response. |
| `POST` | `/api/v1/project-memory/items` | Bearer; editor role for project | Creates a typed memory item with `project_id`, `kind`, `title`, structured `content`, optional `event_id`, and source metadata. Creator identity is assigned by the server. |
| `GET` | `/api/v1/project-memory/project/{project_id}/items` | Bearer; project organization member | Lists active structured items; `include_archived=true` includes archived items. |
| `GET` | `/api/v1/project-memory/items/{item_id}` | Bearer; project organization member | Reads one item within its project scope. |
| `PATCH` | `/api/v1/project-memory/items/{item_id}` | Bearer; editor role | Updates item data or changes status between `active` and `archived`. |
| `DELETE` | `/api/v1/project-memory/items/{item_id}` | Bearer; editor role | Deletes item; `204` response. |

## Phase 3 implementation summary

- Added Argon2 password hashing and normalized, case-insensitive email login.
- Added opaque bearer sessions with configurable expiry, hashed-at-rest tokens, refresh rotation, and revocation on logout.
- Added invitation-only registration/organization onboarding, one-time hashed invitation tokens, expiry, revocation, and password proof for an existing account accepting an invite.
- Added `owner`, `admin`, `designer`, and `member` organization authorization, initial-owner claiming, membership listing, role changes, member removal, and invitation management.
- Enforced organization membership and editor permissions across project, event, venue, zone, source, Design DNA, and Project Memory routes. Cross-organization resource access is hidden as not found.
- Kept the event-type catalog global and limited its mutations to separately provisioned system admins.
- Added session/invitation persistence and migrations, the system-admin flag, and case-insensitive unique user-email enforcement.
- Added authentication, membership, authorization, and tenant-isolation tests. At the Phase 3 checkpoint, the existing and new backend suite passed: 26 tests.

Phase 3 does not implement password reset, request rate limiting, or email delivery for invitations. The API produces the invitation token for the authorized caller to deliver through an approved channel.

## Phase 4 implementation summary

- Completed project and event create, read, update, list, and delete operations using the existing organization roles and tenant isolation checks.
- Added project list pagination and filters for status and workspace. Events can be listed by project at `GET /api/v1/projects/{project_id}/events`.
- Event creation requires an accessible `project_id`; the organization is derived from that project. New event rows are therefore scoped to their parent project. The database keeps `project_id` nullable to preserve legacy events created before the relationship was added.
- Added event scheduling fields (`starts_at`, `ends_at`) that require timezone-aware timestamps. Validation checks schedule ordering and consistency with `event_date`.
- Added guest-count and decimal budget validation, optional three-letter currency, and controlled project/event statuses with lifecycle transition checks.
- Added project and event version values. Clients can send the version from the latest response in a `PATCH` request; stale versions receive `409 Conflict`.
- Added migration `ed7a2c91b405_phase4_project_event_management`. It adds the event-project link, event configuration fields, and version columns without dropping existing data.
- Added project/event lifecycle, validation, version conflict, and authorization tests.

**Verification at Phase 4 closeout:** 31 tests passed. The Alembic migration chain upgraded an isolated PostgreSQL test database through `head`, and `alembic check` reported no model/schema drift. Phase 4 is complete.

## Phase 5 implementation summary

- Reused the existing foreign-key graph for projects, events, Design DNA, legacy Project Memory, venues, venue zones, and venue sources. Added an optional `events.venue_id` relationship; deleting a venue clears that link while preserving the event.
- Added structured `ProjectMemoryItem` records with a typed kind, non-empty JSON object content, optional event scope, source type/reference, server-assigned creator, timestamps, and active/archive lifecycle. Existing one-per-project Project Memory remains available for compatibility.
- Memory item services validate project membership and, when supplied, verify that the event belongs to that same project. Archived items are excluded from normal memory retrieval and can be requested explicitly for management.
- Added authenticated context reads for projects, events, and venues. Responses include relevant events, venues/zones, Design DNA, legacy memory, and active scoped memory items. Retrieval is deterministic and does not depend on an AI provider.
- Cross-organization venue links and cross-project event-memory links are rejected; context and memory reads enforce the existing project or venue access checks.
- Added migration `f42b8c7d190e_phase5_event_graph_and_memory_items`. It adds an optional event venue FK and the structured memory-item table without removing existing data.
- Added event/venue graph, context, memory provenance/lifecycle, missing-related-record, and tenant-isolation tests.

**Verification:** the full backend suite passed with 33 tests. The Phase 5 migration upgraded the isolated PostgreSQL test database, and `alembic check` reported no model/schema drift. Phase 5 is complete.

## Phase 6 Batch 1 implementation summary

- Added a provider protocol and private local filesystem provider with opaque generated keys, sharded storage paths, collision protection, streaming writes/reads, metadata sidecars, existence checks, and safe deletion.
- Added centralized environment configuration for the provider, storage root, upload size, and extension/MIME allowlists. Local storage is the only configured provider; no cloud bucket or credentials are required.
- Added a streaming multipart upload service and authenticated retrieval route. Organization membership, editor permissions, project/event ownership, and event-to-project consistency are checked before storage or retrieval.
- Added upload validation for empty/oversized files, filename safety, extension/MIME agreement, and file signatures/structure for JPEG, MP4, PDF, and SVG. SVG is served only as an attachment with `nosniff`.
- Added storage and API tests using the prepared `INSHO_Assets` directory as read-only test input. The asset collection is not a runtime storage location.
- No database model or migration was needed: this batch stores internal file metadata in private sidecars and does not implement the Asset Library.

At the Batch 1 checkpoint, cloud storage configuration, asset lifecycle/search/folders, image or video processing, deduplication, and background workers were not yet implemented. The Batch 2 and Batch 3 sections below document the later asset and processing work. AI/RAG features remain out of scope.

## Phase 6 Batch 2 implementation summary

- Added `Asset` and `AssetFolder` tables. File bytes remain in the storage provider; PostgreSQL stores metadata and the opaque reference. Assets are scoped to an organization and may be organization-wide, workspace-scoped, project-scoped, or event-scoped. Project/event/workspace consistency is validated in shared service logic.
- Successful Batch 1 uploads now register an Asset record and a SHA-256 checksum. If registration fails after storage succeeds, the upload service attempts compensating object deletion; this is best-effort and is not a distributed transaction.
- Added asset metadata updates, scope/folder moves, search, filters, allowlisted sorting, pagination, secure download by asset ID, soft-delete, and restore. Raw Batch 1 key retrieval also rejects a key once its managed Asset is deleted.
- Added nested logical folders with same-scope parent checks, cycle prevention, case-insensitive sibling-name conflict checks, and empty-only soft deletion.
- Added migration `ab61d3f20c84_phase6_batch2_assets` and asset/folder authorization, lifecycle, search, download, and hierarchy tests.

Batch 2 did not perform physical cleanup of soft-deleted objects or manage orphan recovery. Batch 3 adds an explicit dry-run-first cleanup task. Batch 3 does not add AI analysis, AI generation, RAG, or vector indexing.

## Phase 6 Batch 3 implementation summary

- Added bounded Celery processing using the existing worker setup. Upload requests publish a task only after the Asset row commits; media decoding runs in a worker. Workers use concurrency `2` by default, late acknowledgements, and two retries for transient errors. A broker outage does not undo an upload; processing is marked `failed` with `queue_unavailable` and can be retried after the broker is available.
- Added image processing for JPEG, PNG, and WebP. Pillow extracts dimensions, actual format, source color mode, orientation handling, and size. Configurable pixel and dimension caps are checked before decode. Previews and thumbnails are separately stored JPEGs with EXIF and other source metadata stripped. Original files are never modified.
- Added optional MP4 metadata and poster extraction through operator-installed `ffprobe` and `ffmpeg`. Calls use no shell, restrict protocols to local file/pipe input, cap threads, and enforce `VIDEO_PROCESSING_TIMEOUT_SECONDS`. No FFmpeg binary is bundled. If the tools are unavailable, processing records a retryable failure and retains the original.
- Added derivative records with deterministic per-asset keys, checksums, dimensions, and private storage references. Processing state, attempts, timestamps, safe error codes, and non-sensitive media metadata are recorded on the Asset. PDF and SVG processing is `not_applicable`; raw SVG stays attachment-only, and no SVG preview is generated.
- Added SHA-256 duplicate reporting within the same organization. Identical content creates a separate object and Asset row with `duplicate_of_asset_id`; files are never physically shared or deleted because of a duplicate.
- Added organization/project storage usage aggregation from active Asset and derivative database rows. It does not scan object storage and is not a billing or quota system.
- Added a manual soft-delete cleanup task. It defaults to a dry run, uses a 30-day retention window and row locks, skips processing assets and active references, and permanently removes originals/derivatives only when explicitly called with `confirm_permanent_delete=true`. Cleanup is not scheduled automatically.
- Added migration `64a92f370b1e_phase6_batch3_processing`, processing/preview/usage/retry APIs, and media lifecycle/security tests.

### Running the media worker

Install and run Redis separately, configure `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND`, then start a worker from the backend directory:

```bash
celery -A app.workers.celery_app:celery_app worker --loglevel=INFO --concurrency=2
```

Install an approved FFmpeg/ffprobe build separately and set `FFMPEG_BINARY` and `FFPROBE_BINARY` if the commands are not on `PATH`. This repository does not redistribute FFmpeg binaries. Confirm the selected build and its codec licensing for your deployment. Running media workers with a dedicated low-privilege OS account is recommended.

Cleanup is an explicit Celery task and is safe by default (dry run):

```python
from app.workers.tasks import cleanup_deleted_assets
cleanup_deleted_assets.delay(retention_days=30, confirm_permanent_delete=False)
```

Only pass `confirm_permanent_delete=True` when you intend to permanently delete eligible soft-deleted assets and their derivatives.

### Processing and usage endpoints

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/assets/{asset_id}/processing` | Bearer; asset organization member | Returns state, safe error code, attempts, extracted metadata, and derivative metadata. |
| `POST` | `/api/v1/assets/{asset_id}/processing/retry` | Bearer; editor | Queues a failed, not-started, or stale job again. |
| `GET` | `/api/v1/assets/{asset_id}/preview/{kind}` | Bearer; asset organization member | Streams a safe JPEG `thumbnail`, `preview`, or video `poster`. |
| `GET` | `/api/v1/assets/usage?organization_id=…` | Bearer; organization member | Returns active asset count and original/derivative/total bytes. Optional `project_id` narrows the usage. |

**Verification:** the API tests exercise video tool orchestration with stubbed commands, but no real FFmpeg/ffprobe executable is installed in the development environment. Real codec support and poster extraction need verification with the selected FFmpeg build. Image processing, API behavior, queue failure handling, migration, and regression tests are verified separately.

## Phase 7 and Phase 8 — AI infrastructure and design modules

These phases are separate layers:

| Phase | What it provides | What uses it |
| --- | --- | --- |
| **7 — AI orchestration and provider routing** | Provider-independent execution, model routing, retries, usage, cost estimates, and execution history. It contains no Mood/Style/Plan business logic. | Generic AI callers and later product modules. |
| **8 — Core design intelligence** | The Mood, Style, Plan, Vision, and Elements workflows, their domain schemas, project context, persistence, and APIs. It contains no provider-specific calls. | Designers and downstream INSHO modules. |

Phase 8 calls the Phase 7 service internally. Use the generic `/ai/execute` route for a general AI task; use the design-intelligence routes for design workflows. Neither a Phase 8 route nor a feature service calls a provider API directly.

### Phase 7 — AI orchestration and provider routing

The central `AIExecutionService` validates a standardized request, checks project/workspace membership, selects a configured model by declared capabilities, calls its adapter, validates output, and records execution history. It handles bounded concurrency, timeout, retries, one compatible fallback attempt, normalized errors, usage, and optional cost estimates. Request prompts are not stored in history.

Provider and model configuration is environment-backed. `AI_OPENAI_MODELS` and `AI_ANTHROPIC_MODELS` define ordered model allowlists; `AI_*_CAPABILITIES` declares supported capabilities. Providers without server-side credentials are omitted from routing. Anthropic is off unless `AI_ANTHROPIC_ENABLED=true` and a model allowlist is supplied. Optional `AI_MODEL_CONTEXT_LIMITS_JSON` and `AI_MODEL_PRICING_JSON` use `provider:model` keys. Unknown pricing and usage remain null; cost is an estimate only. See `.env.example` for the settings. OpenAI and Anthropic payloads are isolated in their respective adapters.

Migration `7f1a9b2c3d4e_phase7_ai_orchestration` adds tenant-scoped execution history. Apply the current migration head with `alembic upgrade head` before using the AI APIs.

#### Phase 7 APIs

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/ai/execute` | Execute a generic capability-routed AI request. Accepts text or JSON output, optional JSON Schema, explicit project/workspace context, and an optional idempotency key. |
| `GET` | `/api/v1/ai/projects/{project_id}/executions` | Read execution history for an authorized project. |

### Phase 8 — Core design intelligence

Phase 8 turns authorized project context into versioned design artifacts:

| Module | Persisted result |
| --- | --- |
| **Mood** | Moodboard Sheet: atmosphere, emotional direction, palette, floral/material/texture, lighting, rationale, uncertainty, and confidence. |
| **Style** | Style Sheet: design language, visual identity, forms, composition, guest experience, materials, lighting, and rationale. Uses Mood when available. |
| **Plan** | Layout Sheet: conceptual zones, adjacencies, circulation, assumptions, unknowns, constraints, and rationale. It is not a measured or construction-ready layout. |
| **Vision** | Concept-view sheet: view definitions and design intent for front, side, bird’s-eye, three-quarter, day, night, or alternate views. It does not render images. |
| **Elements** | Element List: individual categorized suggestions with zone/style/material/rationale, known dimensions or quantities only, confidence, and confirmation state. |

Project context is loaded through existing authorization and scoped to the selected event. Only explicitly selected, active assets from the same project/event are linked as references. Stored briefs and references are treated as untrusted data. Module output must pass its Pydantic schema before persistence. Regeneration creates a new version; it does not delete or silently overwrite downstream artifacts. The API reports stale context when upstream versions or source context have changed. Suggested elements remain `ai_suggested` until a user confirms them.

Vision currently persists concept-view plans only. Image-generation adapters are not implemented, so no generated-image asset is claimed or recorded. Plan dimensions and element quantities are not calculated; unavailable facts remain unknown.

Migration `93b4c5d6e7f8_phase8_design_intelligence` adds versioned artifacts, individual element records, and asset links.

#### Phase 8 APIs

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/design-intelligence/projects/{project_id}/{module}/generate` | Generate a `mood`, `style`, `plan`, `vision`, or `elements` artifact. Optional inputs are `event_id`, a short additional brief, and authorized reference asset IDs. |
| `GET` | `/api/v1/design-intelligence/projects/{project_id}/{module}` | Read the latest artifact; add `?history=true` to list up to 100 versions. |
| `GET` | `/api/v1/design-intelligence/artifacts/{artifact_id}` | Read an artifact and its stale-context indicator. |
| `PUT` | `/api/v1/design-intelligence/artifacts/{artifact_id}` | Validate edits and save a new user-edited version. |
| `POST` | `/api/v1/design-intelligence/artifacts/{artifact_id}/approve` | Mark an artifact version approved. |
| `GET` | `/api/v1/design-intelligence/artifacts/{artifact_id}/elements` | List individual rows for an Elements artifact. |
| `PATCH` | `/api/v1/design-intelligence/elements/{element_id}` | Edit an element or confirm an AI suggestion. |
| `POST` | `/api/v1/context/projects/{project_id}/summarize` | Existing project-context feature integration; calls Phase 7 after authorized context retrieval. |

#### Verification and limits

The Phase 8 migration is applied to the local development database at `93b4c5d6e7f8 (head)`. Offline migration SQL generation and the full backend suite passed (**78 tests**). Provider-dependent tests use explicit deterministic mocks; no live Phase 8 generation is verified. The last live OpenAI smoke request returned HTTP 429. Provider account access and rate limits need to be resolved before live validation.

Phase 8 does not include a spatial editor, geometry engine, renderer, quantity/BOQ or budget engine, procurement, RAG, or a redesigned frontend. Phase 9 Part 1 below adds the persistent spatial foundation; it does not implement the editor or geometry engine.

## Phase 9 Part 1 — Spatial architecture and canonical scene model

Phase 9 Part 1 adds one canonical, persistent spatial model for future 2D and 3D clients. It reuses project/event authorization and the existing managed Asset records; the legacy venue and venue-zone APIs remain available and are not treated as the editable scene graph.

Spatial scenes are project-owned and may be event-scoped. Each scene records positive width, depth, and height bounds in meters, a display-unit preference, an extensible scene type, metadata, lifecycle status, and a current revision. A project may have multiple scenes. Spatial zones and scene objects are relational rows with same-scene parent constraints. Objects can also carry structured, typed relationship edges and attach existing project-compatible assets with role and metadata snapshots.

### Coordinate and transform convention

- The canonical coordinate system is right-handed and Z-up. The scene origin is the lower-left corner of the venue floor. +X points right/east, +Y points away from the origin/north, and +Z points upward.
- Position and unscaled dimensions are stored in meters. Supported display units are `m`, `cm`, `mm`, `ft`, and `in`; changing `display_unit` never rewrites spatial values. `app/spatial_units.py` supplies deterministic Decimal-based conversion helpers.
- Positive rotations follow the right-hand rule. `rotation_deg.x/y/z` are rotations about X/Y/Z, composed as `Rz * Ry * Rx` (local X, then Y, then Z). Parentless transforms are in scene coordinates; child transforms are local to their parent. Part 2 resolves parent matrices for geometry calculations.
- Object position is its local pivot; built-in box/rectangle extents are centered on that pivot. Child transforms compose as `parent_world * child_local`.
- `dimensions` are authored local, unscaled extents in meters. Positive `scale` values are dimensionless and apply to those extents; the API returns derived `effective_dimensions_m`. Dimensions are never silently rewritten when scale changes.
- A zone footprint, when present, is a polygon in zone-local XY coordinates in meters. Object/zone types and categories are open strings rather than a closed enum.

Every successful scene, zone, object, relationship, or asset-reference mutation appends one `spatial_scene_revisions` record with actor, operation, entity identity, before/after row state, timestamp, and a monotonically increasing scene revision. Writes lock the scene row, so concurrent mutations in one scene serialize. This is an audit/change log foundation, not an undo/restore API. Scene deletion is a soft delete and is rejected while zones or objects remain; zone/object deletion is rejected when it would orphan children or assignments.

### Spatial API

All routes require a valid bearer session. Reads require project membership; writes require an editor role. Event-scoped scenes require the event to belong to the same project. IDs are resolved through the scene/project before mutation, and composite database foreign keys also prevent parent, zone, and relationship links across scenes. Asset attachments use the existing AssetService authorization and must match the scene organization and compatible workspace/project/event scope.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST`, `GET` | `/api/v1/spatial/projects/{project_id}/scenes` | Create/list a project's scenes; list can filter by `event_id`. |
| `GET`, `PATCH`, `DELETE` | `/api/v1/spatial/scenes/{scene_id}` | Read, edit, or safely soft-delete a scene. |
| `POST`, `GET` | `/api/v1/spatial/scenes/{scene_id}/zones` | Create/list zones in a scene. |
| `GET`, `PATCH`, `DELETE` | `/api/v1/spatial/zones/{zone_id}` | Read or manage one zone. Parent assignment is part of zone create/update. |
| `POST`, `GET` | `/api/v1/spatial/scenes/{scene_id}/objects` | Create/list scene objects; list can filter by `zone_id`. |
| `GET`, `PATCH`, `DELETE` | `/api/v1/spatial/objects/{object_id}` | Read or manage one object, including transform and reparenting. |
| `GET` | `/api/v1/spatial/scenes/{scene_id}/hierarchy` | Read a nested scene/zone/object hierarchy. |
| `POST`, `GET` | `/api/v1/spatial/scenes/{scene_id}/relationships` | Create/list typed object-to-object edges. |
| `DELETE` | `/api/v1/spatial/relationships/{relationship_id}` | Remove an edge. |
| `POST`, `GET` | `/api/v1/spatial/objects/{object_id}/assets` | Attach/list existing managed assets by spatial role. |
| `DELETE` | `/api/v1/spatial/objects/{object_id}/assets/{link_id}` | Remove an asset link without deleting the asset. |
| `GET` | `/api/v1/spatial/scenes/{scene_id}/revisions` | Read the paginated scene audit history. |

### Persistence, integration boundary, and verification

Migration `a14c9d2e7b61_phase9_part1_spatial_scene_model` adds `spatial_scenes`, `spatial_zones`, `spatial_objects`, `spatial_object_relationships`, `spatial_object_assets`, and `spatial_scene_revisions`. See [the spatial architecture guide](docs/spatial-architecture.md) for the entity and API contract details.

Part 1 established persistence and canonical transforms; it did not add a canvas/editor, geometry engine, collision/containment validation, 3D renderer, AI spatial calculations, or restore/undo behavior. Existing Phase 8 artifacts are not automatically converted into measured scenes.

#### Phase 9 Part 1 verification

The full backend suite passed: **89 tests**. A clean, empty PostgreSQL test database upgraded through the complete Alembic chain to `a14c9d2e7b61`, and `alembic check` reported no model/schema drift. The configured local development database was upgraded to the same revision and also passed `alembic check`. Python compilation and offline SQL generation for the migration passed.

## Phase 9 Part 2 — Spatial engine and deterministic geometry

Part 2 adds a reusable, pure-Python geometry package on the Part 1 models. It calculates vectors and transforms, resolves local-to-world hierarchy matrices, derives object footprints and 3D bounds, checks scene/zone containment, detects 2D/3D overlaps, measures distances and clearances, and returns grid-snap/alignment proposals. Calculations are deterministic and independent of AI, persistence, and HTTP. No schema migration or new dependency was needed.

The shared tolerance defaults to `1e-6 m` absolute and `1e-12` relative. Touching boundaries count as intersections, with `touching` separated from positive-area overlap. Supported explicit scene constraints return `pass`, `fail`, or `unknown`; unsupported geometry and rules remain unknown. Clearance is a geometric measurement and is not a regulatory or safety certification.

### Phase 9 Part 2 APIs

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/spatial/scenes/{scene_id}/objects/{object_id}/geometry` | Return calculated world footprint, bounds, volume corners, and transform matrix for a persisted object. |
| `POST` | `/api/v1/spatial/scenes/{scene_id}/validate` | Validate measurable scene bounds, assigned-zone containment, overlaps, and supplied extensible constraints. |
| `POST` | `/api/v1/spatial/scenes/{scene_id}/snap` | Calculate grid/anchor snapping for a world-meter point; does not persist. |
| `POST` | `/api/v1/spatial/scenes/{scene_id}/align` | Calculate alignment/equal-spacing positions for authorized scene objects; does not persist. |
| `POST` | `/api/v1/spatial/scenes/{scene_id}/clearance` | Measure the geometric distance between two authorized objects against a requested threshold. |

See [the geometry engine guide](docs/spatial-engine.md) for exact geometry types, supported object properties, transform composition, precision rules, intersection semantics, limitations, and Part 3 integration. The engine supports boxes/rectangles, circles, polygons, line segments, and points in 2D. Upright custom footprints use their exact 2D geometry plus vertical extents. Tilted boxes use 3D SAT; tilted non-box solids and affine ellipses are explicitly unsupported and reported as unknown or invalid rather than approximated.

Part 2 does not implement the 2D editor, 3D renderer, AI geometry, regulatory constraints, or future procurement/fabrication work. Geometry results are calculated on demand from canonical data and are not persisted as a second source of truth.

#### Phase 9 Part 2 verification

The full backend regression suite passed: **101 tests** (including Phases 1–8 and the new spatial math/API coverage). The focused spatial engine suite passed **12 tests**. Compilation passed. No database migration or dependency addition was required for Part 2.

## Phase 9 Part 3 — 2D spatial editor

Open `/workspace` on the existing FastAPI server, sign in, and choose an organization, project, and spatial scene. Designers can create measured project or event scenes, place and edit objects and zones, organize layers, measure distances, and review geometry findings. The editor provides an SVG viewport with world-meter grid, pan, zoom, selection, handles, snapping, alignment, and an inspector.

Autosave writes atomic actions against the current scene revision. Undo and redo use the same canonical APIs. Reopening a scene reads its persisted objects, zones, settings and links. The workspace also exposes compatible managed assets and the existing Phase 8 Plan as a design reference. See [the editor guide](docs/spatial-editor.md) for controls, persistence, API changes and test commands.

The full backend suite, including the real Chrome workflow, passed **107 tests**. Four isolated JavaScript tests also passed. Part 3 required no database migration or new runtime dependency.

## Phase 9 Part 4 — Scene graph and object system

The canonical scene now supports nested groups, scene layers, independent sibling and layer member order, configurable default layers, and inherited visibility and locking. Structural edits use the existing scene lock and revision log. Grouping preserves member IDs, dimensions and associations, and reparenting preserves world transforms within canonical storage precision. The graph API exposes the structural and effective state future Phase 12 workspace controls will consume.

Migrations `c481a7d02f64` and `d37b196ac241` add layers, ordering, configurable defaults, and database safeguards for graph cycles and locked scene deletion. See [the scene graph guide](docs/spatial-graph.md) for the backend contract, commands and verification. The backend suite passed **121 tests** with the browser workflow excluded.

## Phase 9 Part 5 — 3D Scene Engine

The read-only `GET /api/v1/spatial/scenes/{scene_id}/runtime-3d` endpoint compiles the authorized scene revision into a versioned 3D snapshot. It uses the Part 4 tree and inherited state and the Part 2 footprint and transform math. Each valid measured node has indexed local geometry, its world matrix, and local and world bounds. Hidden nodes remain in the snapshot. Invalid nodes retain their identities and include structured diagnostics.

See [the 3D runtime guide](docs/spatial-runtime-3d.md) for geometry mappings, coordinate semantics, the Phase 12 contract and validation. The backend suite passed **130 tests** with the existing browser workflow excluded. Part 5 required no migration or runtime dependency.

## Phase 9 Part 6 — 2D and 3D synchronization

`GET /api/v1/spatial/scenes/{scene_id}/synchronization` returns an authorized 2D plan and 3D runtime compiled from one canonical scene revision. The response compares optional client revisions, preserves shared graph state and dimensions, and reports invalid data and unsupported projections. After a canonical edit, the next read compiles both views from the latest revision.

See [the synchronization guide](docs/spatial-synchronization.md) for coordinates, shape mappings, validation, and the Phase 12 read and write flow. The backend suite passed **137 tests** with the existing browser workflow excluded. Part 6 required no migration, runtime dependency or frontend change.

## Phase 9 Part 7 — Asset and Object Library

The object library stores reusable organization, workspace, and project definitions with validated geometry, default dimensions and transforms, tags, metadata, and Phase 6 asset references. The catalog supports search, filters, facets, duplication, versioned updates and archive. Placement creates an ordinary canonical scene object with the source definition ID and version. Existing instances retain independent authored values when a definition changes.

Migration `eb4c51ad9027` adds catalog tables and scene object provenance. See [the object library guide](docs/object-library.md) for the API, asset lifecycle, versioning and Phase 12 integration. The backend suite passed **145 tests** with the existing browser workflow excluded.

## Phase 9 Part 8 — Spatial intelligence and validation

`POST /api/v1/spatial/scenes/{scene_id}/validation-report` evaluates one canonical scene revision and returns severity counts, coverage by rule, and filterable findings with stable IDs and measurements. It checks geometry, transforms, hierarchy, venue and zone containment, collisions, configured clearance, 2D/3D consistency, and object library integrity.

Project defaults, scene policies, and per-request overrides use the existing JSON fields. Reports are computed on demand. See [the spatial validation guide](docs/spatial-validation.md) for collision permissions, clearance examples, supported geometry, and the Phase 12 integration contract.

Verification passed with **166 pytest tests** including the Chrome editor workflow, **4 JavaScript tests**, and a clean Alembic schema check. In a 240-object fixture, broad-phase filtering sent one of 28,680 possible pairs to each exact collision and clearance calculation.

## Phase 9 Part 9 — Design intelligence integration

The scene-scoped design integration API reads saved Mood, Style, Plan, Vision, and Elements artifacts. It exposes their provenance and freshness, maps conceptual zones to the scene, and ranks matching Object Library definitions for element placement. Editors can validate a measured zone or an object placement against the full Part 8 report, then apply or reject it.

Migration `02e73da81bf4` stores recommendation reviews and retry-safe application receipts. See [the design integration guide](docs/spatial-design-integration.md) for module contracts, secure API flows, and Phase 12 integration.

Verification passed with **61 focused tests**, **178 tests** in the full backend suite including the existing Chrome workflow, and **4 existing JavaScript tests**. The migration upgraded, downgraded, and re-upgraded in a disposable PostgreSQL database. The development database is at `02e73da81bf4` with no schema drift.

## Phase 9 Part 10 — Spatial system completion

Phase 9 is **complete with limitations**. The audit verified the measured scene-to-design workflow across all nine prior parts, fixed geometry precision and projection defects, and hardened legacy reads, spatial inputs and design asset scoping. The final full suite passed **193 tests**, including the existing Chrome editor workflow. The local PostgreSQL schema is at `02e73da81bf4` with no drift.

The [completion audit](docs/spatial-completion.md) records the part-by-part evidence, Phase 12 API flow, exact verification commands, and benchmark results through 500 objects. The historical Part 3 workspace remains available as a compatibility client.

## Phase 10 Part 1 — Production foundation

Scene-scoped production records link Phase 9 designs to assemblies, components, materials, specifications, and canonical measurements. The production API tracks provenance, flags stale sources, and preserves approved records. [Production foundation guide](docs/production-foundation.md)


## Phase 10 Part 2 — Quantification and technical specifications

The backend calculates versioned takeoffs from Phase 9 geometry and extends the production specification model with structured fields, source review, and approval history. [Quantification and Technical Sheets guide](docs/production-quantification.md)


## Phase 10 Part 3 — Materials, BOM, and costing

Material definitions and plan-specific assignments feed revisioned BOMs from verified quantities and technical specifications. Decimal pricing records estimates, quotes, actuals, approvals, and stale inputs. [Materials, BOM, and costing guide](docs/production-bom-costing.md)


## Phase 10 Part 4 — Budgets, procurement, and quotes

The production backend has revisioned project and event budgets, supplier relationships and offerings, comparable supplier quotes, purchasing requirements from approved BOMs, and reviewed value-engineering substitutions. [Budget and procurement guide](docs/production-budget-procurement.md)

## Phase 10 Part 5 — Integration and hardening

Start with the authorized Phase 9 scene and create its production record. Calculate object or zone quantities, pin the takeoffs in the Technical Sheet, and map the production components to material assignments. Build and approve a BOM from the current quantities and sheets. Select BOMs and approved quotations for a project or event budget. Generate procurement from the approved BOM, compare supplier quotations, and apply an explicitly reviewed alternative when required.

The APIs report source freshness independently of approval. After a stage dimension change, recalculate the affected takeoff, approve the successor sheet and BOM, and explicitly revise the linked commercial records. If Part 1 approval has frozen the source record, use a linked successor. Prior approvals and amounts remain readable. Production Ready and In Fabrication check the technical package and BOQ. The operational workflow records the production sequence, vendor assignments, installation windows, dependencies, and approval evidence on the same production record. [Workflow and migration guide](docs/production-foundation.md) · [Quantity and sheet guide](docs/production-quantification.md) · [BOM and financial rules](docs/production-bom-costing.md) · [Sourcing and procurement contracts](docs/production-budget-procurement.md)

Apply `alembic upgrade head` before using the workflow endpoints. To verify with an isolated PostgreSQL test database, follow the test setup above and run `python -m pytest -q tests/test_production*.py`. The migration test exercises the upgrade, downgrade, and re-upgrade with seeded Phase 9 and Phase 10 records. Full regression coverage uses `python -m pytest -q`.

Phase 12 can consume `/workflow` and its history alongside existing scene, quantity, specification, BOM, project cost, budget, quote, proposal, procurement, and managed asset endpoints. Use the version tokens on all writes and render readiness blockers, stale sources, unpriced totals, and approval independently. Phase 11 can reference the pinned package and approval evidence for presentation workflows. External engineering signoff, price feeds, ordering, payment, and procurement reconciliation between BOM versions remain outside this backend workflow.

## Phase 11 Part 1 — Insho Present

A Client Presentation Sheet belongs to an existing project and can be associated with one of its events. The project supplies organization and workspace scope. A presentation has a stable ID. Each save records the title, description, purpose, client context, section inputs, selected source IDs and versions, captured display evidence, actor, and timestamp in a new revision. Saving identical inputs with identical source evidence returns the current revision. Earlier revisions and decisions remain readable.

Create an alternative with `alternative_of_id` pointing to a main presentation in the same project and event. An alternative has its own identity, revisions, assets, and checkpoints. Select an alternative revision explicitly in the main sheet's `alternatives` section. Removing it from the main sheet leaves its history available.

### Section sources and completeness

Sections accept `text`, `applicability`, `sources`, and `asset_ids` where supported. A source has `kind` and `id`. Use `version` with an `alternative` source to pin the selected revision. Direct and inherited assets use the existing Asset IDs and expose role, name, media type, checksum, and available preview metadata. Use the authenticated Asset download or preview routes for bytes.

| Section | Existing data used |
| --- | --- |
| `concept_story` | Authored narrative or Style/Vision content. Design DNA and explicitly selected Project Memory item titles can provide context. |
| `design_dna` | The selected project's Design DNA and version. |
| `moodboard` | Mood artifact and its linked visual references, or selected managed visuals. |
| `hero_visual` | One selected primary visual or a Vision artifact's generated visual. Scoped generation references expose execution ID, provider, and model when available. |
| `spatial_plan` | Canonical scene and revision with drawable 2D geometry, a structured conceptual Plan artifact, or an authored floor plan asset. |
| `views_3d` | Canonical scene and revision with drawable geometry or selected render assets associated with that scene. A Vision sheet can supply viewpoint descriptions. |
| `journey` | Authored journey text or guest-flow content from a Plan artifact. |
| `material_palette` | Phase 10 material assignments, Design DNA palette and materials, Mood/Style selections, or palette visuals. |
| `led_visual_preview` | Managed visual plus a current LED quantity or a linked canonical scene containing a placed LED surface. A linked LED source or recognized `led`, `led_wall`, `led_screen`, or `led_surface` scene object makes the preview required. |
| `budget_summary` | Phase 10 budget revision, currency, approval, estimates, quotes, and actuals with their existing completeness states. |
| `production_confidence` | Workflow state, latest BOM, technical package review, readiness blockers, and available procurement status counts. Completeness requires a current pinned readiness review. |
| `alternatives` | Pinned revisions of linked alternatives with available design evidence or visual assets. |

`concept` requires Concept Story, Design DNA, Moodboard, and Hero Visual. `design_review` adds Spatial Plan, Journey, and Material Palette. `production_review` also requires 3D Views, Budget Summary, and Production Confidence. Authors can make other sections required or mark empty optional sections inapplicable using `applicability`. Validation returns each section's requirement, `complete`, `missing`, `unavailable`, or `not_applicable` status, and issues with a `blocking` flag. Missing optional material stays nonblocking. An explicitly linked stale or inaccessible source must be resolved before approval.

Client contexts are `luxury`, `corporate`, `bride_family`, `hotel`, `agency`, `brand`, and `government`. Each preset changes `emphasis` and `section_order`. The event data and completeness rules remain common across contexts. Project Memory links are explicitly selected and expose their titles and kinds. Internal memory payloads, model outputs, supplier documents, and storage details stay in their owning services.

### API and approvals

All routes require a bearer session and are scoped under `/api/v1/projects/{project_id}/presentations`. Project members can read. Owners, admins, and designers can create, revise, and request reviews. Owners and admins can decide checkpoints. Cross-tenant, wrong-project, and wrong-event references return `404`. Requests with incompatible sources or fields return `422`.

| Endpoint | Request and response |
| --- | --- |
| `POST` at the collection | Create a sheet with `title`, `client_context`, optional `purpose`, `description`, `event_id`, `alternative_of_id`, and `sections`. Returns `201` with the first revision. |
| `GET` at the collection | List current summaries with optional `event_id`, `offset`, and `limit`. |
| `GET /{id}` or `GET /{id}/sections` | Read the current sheet or evidence by section. Use `?revision=N` for history. |
| `PUT /{id}` | Save the full desired sheet with `expected_revision`. Add or remove source and asset references by changing `sections`. |
| `GET /{id}/revisions` or `GET /{id}/alternatives` | Paginated history or associated alternatives. |
| `GET /{id}/validation` | Read structured completeness and source availability for the current revision or `?revision=N`. |
| `POST /{id}/approvals` | Define a checkpoint with `revision` and `title` before requesting review. |
| `POST /{id}/approvals/{checkpoint_id}/request` | Move a draft checkpoint to pending with `revision` and optional `comment`. |
| `POST /{id}/approvals/{checkpoint_id}/decision` | Record `approved`, `rejected`, or `revision_requested` with `revision` and optional `feedback`. |
| `GET /{id}/approvals` | Paginated review history, optionally filtered by `revision`. |

Define all checkpoints before the first request. A revision is approved when all its checkpoints are approved. Requesting review and approving both require a complete, current evidence package. Conflicts return `409`. An older checkpoint keeps its state and actor history when a newer revision is saved. A decision requires the checkpoint and the current revision to match. Source changes preserve the captured evidence and report `changed` or `unavailable`. Metadata is withheld when a source moves outside the presentation scope or becomes inaccessible.

Phase 12 can assemble its client view from `sheet`, `section_order`, and `sections`. Use the source scene ID and pinned revision with the existing spatial synchronization APIs. Use a managed render when an exact historical visual must remain available after scene edits. The backend records references, evidence, and internal decisions. Slides, document export, new image rendering, engineering certification, external guest review, and real-time collaboration are outside Part 1. Phase 11 Part 2 can use the presentation ID, revision, checkpoint history, and alternative relationship for version and memory integration.

Apply `alembic upgrade head` for revision `73fd6a4ce211`. The migration adds presentations, revisions, asset links, and checkpoints. Project and event deletion protect attached presentation history. Managed asset cleanup retains objects linked by historical revisions. A downgrade to `4b9d5a732e10` removes Phase 11 presentation data while preserving the preceding project and event records. Run `python -m pytest -q tests/test_presentation.py tests/test_presentation_migration.py` with the dedicated PostgreSQL test database configured.

## Phase 11 Part 2 — Design versions, branches, and Project Memory

Design versions capture selected state from the existing Event Graph, Design DNA, design artifacts and elements, authored spatial scenes, presentation revisions, BOMs, budgets, and active memory. The snapshot retains the original object identifiers, inputs, source revisions, and managed asset references. Scene geometry is stored as captured authored records. Asset bytes stay in the managed Asset service. Historical reads flag unavailable assets and withhold their metadata. The version service operates on captured design states. Editing an alternative scene uses the existing scene APIs with a separate scene ID.

### Version and branch workflow

Use `/api/v1/projects/{project_id}` as the prefix for the following routes. Pass `event_id` on mainline version writes and lists to select the event stream. When omitted, the stream is project-wide. Version numbers are unique within a project/event scope and shared by its branch histories, so an individual history can have gaps.

| Route | Behavior |
| --- | --- |
| `POST /design-versions` | Save a new mainline version with `expected_head_id`, `summary`, `reason`, and an optional `selection`. Use `null` as the first expected head. |
| `GET /design-versions` and `GET /design-versions/current` | Paginated mainline history and current state. |
| `GET /design-versions/{id}` | Read the captured state, ancestry, author, and review association. |
| `GET /design-versions/compare` | Compare `from_version_id` with `to_version_id` in the same scope. Reports added, removed, or updated entities and changed fields, using the original scene object IDs. |
| `POST /design-versions/restore` | Append an earlier mainline snapshot as the next working version with `source_version_id`, `expected_head_id`, `summary`, and `reason`. |
| `PUT /design-versions/{id}/review` | Bind the captured presentation `id` and `revision` before requesting checkpoint review. |
| `POST /design-branches` | Fork with `source_version_id`, `expected_mainline_id`, `name`, optional `description`, and `reason`. The first branch version references its source snapshot. |
| `GET /design-branches` and `GET /design-branches/{id}` | Paginated branch summaries and details for the requested project/event scope. |
| `GET` or `POST /design-branches/{id}/versions` | Read branch history or save its next revision using the branch head as `expected_head_id`. |
| `GET /design-branches/{id}/compare` | Compare the branch head with the version it was forked from. |
| `POST /design-branches/{id}/restore` | Append an earlier version from that branch using the restore request fields. |
| `POST /design-branches/{id}/promote` | Publish the selected branch head as a new mainline version. Requires `expected_head_id`, `expected_mainline_id`, `mode: "whole_state"`, `summary`, and `reason`. |
| `POST /design-branches/{id}/archive` | Close a branch with `expected_head_id` and `reason`. Its history remains readable. |
| `GET /design-activity` | Paginated activity for the project, optionally filtered by event, version, or branch. |

Omitted `selection` fields carry forward the captured source. Supply a source again to capture its current data. Empty arrays and explicit nulls remove selected sources. `refresh_context: true` captures current project, event, and venue context. A create request can use the following shape with an authorized scene ID.

```json
{
  "expected_head_id": null,
  "summary": "Stage and entry design",
  "reason": "Record the layout for technical review",
  "selection": {"scene_ids": ["cfd37578-b2bd-48e1-8b77-cbeb5ca93172"]}
}
```

Design versions are append-only. Write transactions serialize project-level version numbering and head checks. Restoration and promotion reuse immutable snapshot IDs and record both the previous head and the source version. The mainline changes only after a successful mainline write, restore, or explicit promotion. Promotion checks both heads and the live mainline sources recorded at the fork. A changed head or changed or unavailable dependency returns `409`. Nested branches inherit that baseline. Promotion uses the whole captured state. Resolve divergence by reviewing the current mainline and creating a fresh branch. Promotion closes the source branch for further writes and retains its history.

Design review reuses Part 1. Capture the exact presentation revision in `selection.presentation`, bind it to one design version, then use the existing checkpoint endpoints. Where the sheet and design state both select sources of the same kind, the IDs and revisions must agree. All checkpoints on the bound sheet must approve for the design version to show `approved`. Branches, restores, and promoted versions start with their own approval state even if they reuse a snapshot. PostgreSQL protects design snapshots, review bindings, presentation revision content, and finalized checkpoint decisions from direct edits.

Activity records the actor, action, entity ID, project/event and applicable version/branch, reason, time, and request ID. Version events include prior and new references, changed fields, and affected source IDs. Captured artifacts and linked memory can identify existing AI execution IDs. Send a UUID in `X-Request-ID` to correlate write requests. The service generates an ID when the header is absent.

### Project Memory

The existing `/api/v1/project-memory/items` and `/project-memory/project/{project_id}/items` routes support the thirteen Section 27 categories. The API values are `must_have`, `must_avoid`, `approved`, `rejected`, `client_preferences`, `design_dna`, `budget`, `venue_dimensions`, `guest_count`, `previous_decisions`, `vendor_constraints`, `production_constraints`, and `safety_constraints`.

Items carry structured `content`, source attribution, a revision number, and `verification` (`unverified` or `verified`). A linked `decision_id` must refer to a finalized Part 1 checkpoint with the matching outcome for approval and rejection memories. If that checkpoint is bound to a design version, the memory records the same `linked_version_id`. Owners and admins can mark a memory item verified. Existing source attribution and decision links are preserved. Record a new item for a different source or a different decision outcome.

Use `PATCH /api/v1/project-memory/items/{id}` with the current `expected_version` and an optional change `reason`. Use `GET /api/v1/project-memory/items/{id}/history` for recorded values and authors. `POST /api/v1/project-memory/items/{id}/archive` takes `expected_version` and `reason`. The legacy item DELETE route also archives. Archived items stay out of active memory and Event Graph context. Event deletion archives its scoped memory before detaching it, so its constraints do not enter the project-wide active set. List with `category`, `event_id`, `include_archived`, `offset`, and `limit` as needed. A list scoped to an event includes project-wide active items and that event's active items by default. The legacy aggregate memory API and context response shape remain available.

Existing organization, workspace, project, and event checks apply throughout. Members can read, editors can save and branch, and owners or admins bind reviews, promote branches, and verify memory. Managed asset access is rechecked for each historical read. Cross-project and cross-tenant identifiers return `404`.

### Migration, tests, and downstream use

Run `alembic upgrade head` to apply `921d7c306abe`. The migration records a baseline revision for each existing structured memory item and classifies legacy preferences and decisions where the category is clear. A downgrade to `73fd6a4ce211` removes Part 2 history, new memory fields, and design versions while retaining existing memory item values and Part 1 data. Run `python -m pytest -q tests/test_design_versioning.py tests/test_project_memory_history.py tests/test_design_version_migration.py` with a dedicated PostgreSQL test database.

Part 3 can validate a selected version ID and fetch current category-filtered memory with verification and provenance. Part 4 can consume finalized decisions and historical memory as learning inputs. Later Phase 11 work and Phase 12 can use stable branch heads, compare, and activity endpoints. Restore and promotion publish a new captured state. Canonical records still change through their owning APIs. Object-level merges, simultaneous authoring, and replay into the live graph remain outside this workflow. A removed or moved asset must be resolved before reusing a historical snapshot for restoration or promotion.

## Phase 11 Part 3 — Design Validator and Design Audit

Each audit identifies an exact design version and snapshot, the initiating user, the evaluator configuration, and an input fingerprint. It reads the version's selected sources and verifies the pinned presentation fingerprint before using its evidence. Bound approval checkpoint states and managed asset availability are captured at run start. Phase 9 replays its existing deterministic validation rules over the stored authored scene rows. Phase 10 supplies recorded takeoffs, rates, budgets, specifications, and readiness evidence. A newer live scene or source edit leaves the historical audit's stored results intact.

### Dimensions and evidence

| Section 28 dimension | Implemented criterion |
| --- | --- |
| Creativity | Optional routed interpretation of design direction and captured concept or scenic elements. |
| Originality | Optional comparison against references documented in the selected project artifacts. |
| Theme consistency | Optional comparison of captured Design DNA and selected creative direction. |
| Color harmony | Optional interpretation of captured Design DNA and Mood palette selections with explicit color values. |
| Floral quality | Optional review of recorded floral intent and confirmed floral design elements. |
| Furniture/decor | Deterministic fit, placement, and collision screening for placed furniture and decor. |
| Lighting | Optional review of recorded lighting intent and fixture choices. |
| Scale/proportion | Deterministic comparison of measured object envelopes with authored scene dimensions. |
| Spatial feasibility | Existing scene geometry, transforms, hierarchy, containment, collision and configured clearance checks. |
| Production complexity | Captured package coordination and readiness blockers from a pinned production section. |
| Budget efficiency | Recorded Phase 10 estimated variance against each eligible approved ceiling in its original currency. |
| Photography/social impact | Optional review of documented photography direction and planned viewpoints. Available managed visual references provide supporting metadata. |
| Guest experience | Optional review of documented guest flow against the recorded experience intent. The captured guest count provides context. |
| Accessibility | Deterministic review of measured targets against explicitly selected authored Phase 9 clearance IDs. A measurable target is required. |
| Sustainability | Verified Project Memory with a source reference and `content.sustainability` containing `reuse_required: true` and a boolean `reuse_verified` outcome. |
| LED suitability | Valid Phase 10 pixel-grid evidence pinned by the presentation and matched to the captured scene revision. |
| Technical completeness | Captured BOM line inputs, technical sheet references, package readiness evidence, and relevant checkpoint states. |
| Risk/safety flags | Unscored preliminary screening of measured geometry findings, installation review needs, and captured safety constraints. |

Scores are ordinal within the stated criterion. `1` means an evidenced issue, `2` means mixed evidence, and `3` means the reviewed evidence supports the criterion. Missing, incompatible, disabled, and insufficient evidence can produce `not_evaluated` with a null score. There is no aggregate design-quality score. The summary counts evaluated and unevaluated dimensions, lists missing inputs, and identifies high-priority dimensions. Comparisons flag matching deterministic criteria and configuration. AI judgments and unevaluated dimensions are shown with their evidence and remain outside comparable scoring.

Every dimension includes an explanation, evidence, recommendations, confidence, and uncertainty. Evidence identifies its source kind, canonical ID, revision or field when available, origin (`observed`, `derived`, or `ai_inferred`), and a scoped value. An observed item records what the source states. Recommendations identify the finding, action, rationale, target, affected dependencies, evidence IDs, and whether editor or professional review is required. Applying a recommendation takes a separate authorized write through the owning service. Historical asset evidence exposes current availability and hides metadata that is no longer accessible.

AI assistance is opt-in. The existing provider router receives bounded excerpts for the eligible subjective dimensions. The adapter accepts categorical judgments with evidence IDs from both sides of the review and discards unsupported judgments. AI interpretation is low-confidence and limited to descriptive evidence. It receives no binary assets and supplies no measurements, costs, or safety determinations. A routed provider failure leaves deterministic results available with a structured warning and retryability flag.

Safety findings are preliminary decision support. The audit retains authored constraints with their source attribution and flags conditions for qualified review. The safety dimension has a null score. Structural adequacy, weather limits, rigging, power, venue rules, and inspection decisions require qualified review before fabrication or installation. Spatial checks use captured scene envelopes and authored clearances. Accessibility checks cover those specific separations. They provide no route, facilities, or compliance certification.

### API and verification

Use `/api/v1/projects/{project_id}/design-audits`. Members can read. Editors can start and retry audits. Project, event, version, and asset scope are checked using existing authorization. Requests can send a UUID `X-Request-ID` to correlate the audit and activity records.

| Route | Behavior |
| --- | --- |
| `POST` at the collection | Start an audit with `design_version_id`, optional `ai_assisted` (default `false`), and optional `accessibility_clearance_ids`. Returns the new audit and its status with HTTP `201`. |
| `GET` at the collection | Paginated history, optionally filtered by `version_id`, `event_id`, and `status`. |
| `GET /{evaluation_id}` | Read the saved audit, evaluator metadata, warnings, and available results. |
| `GET /{evaluation_id}/dimensions` and `GET /{evaluation_id}/dimensions/{dimension}` | Read all dimension results or one named dimension. |
| `GET /{evaluation_id}/evidence` and `GET /{evaluation_id}/recommendations` | Read evidence or recommendations, optionally filtered by `dimension`. |
| `GET /compare` | Compare completed audits in the same scope using `from_evaluation_id` and `to_evaluation_id`. |
| `POST /{evaluation_id}/retry` | Start a new audit for the same design version and options after a retryable outcome. The original audit remains readable. |

The start request runs through completion without a new queue. An in-progress run is readable as `running`. An unfinished request older than ten minutes is exposed as `interrupted` and can be retried. Failed and cancelled runs retain typed warnings. Dimension and comparison endpoints return `409` until a finalized result exists. The audit checks up to 250 captured objects and zones per scene and stores up to 100 spatial findings per scene. Skipped or truncated evidence is flagged and cannot support a complete positive spatial assessment. Live catalogue checks stay in the existing Phase 9 report. Older design snapshots retain their captured values and may have less detailed technical evidence. A new version can capture the expanded Phase 10 provenance fields.

Run `alembic upgrade head` to apply `67e52ccf940a`. PostgreSQL validates the version/snapshot scope and protects finalized audit records. Downgrading to `921d7c306abe` removes the audit table and preserves the design versions and their histories. Use `TEST_DATABASE_URL` to point to a dedicated PostgreSQL test database. Run `python -m pytest -q tests/test_design_audit.py tests/test_design_audit_migration.py` for the focused suite and `python -m pytest -q` for the full suite. Run `node --test tests/browser/geometry.test.mjs` for the browser geometry regression tests.

Phase 11 Part 3 verification passed 323 Python tests, including 11 audit tests, and four browser geometry tests. The PostgreSQL test verified an upgrade, downgrade, and re-upgrade with seeded Part 2 design data. `alembic check` reported no schema drift. AI routing and failure recovery were exercised with a test provider.

Part 4 can use the audit ID, exact version, dimension results, validated evidence, approval states, and recommendation provenance as review inputs alongside recorded event outcomes.

## Phase 11 Part 4 — Learning Engine and Actuals Capture

Section 29 learning starts with event outcomes recorded by a person and verified by an organization owner or admin. Three tables store outcome records, report snapshots, and recommendation dispositions. The backend resolves sources through the owning domain services. Financial and measured amounts use Decimal values serialized as decimal strings. Managed evidence stays in Asset Management. Design activity records the actor, operation, and request ID for writes.

### Outcome records and verification

| Outcome kind | Evidence and scope |
| --- | --- |
| `cost` | Amount, currency and expenditure date against a selected Phase 10 estimated BOM line or budget total. May include a project supplier and an invoice or transaction reference. |
| `quantity` | Consumed amount and unit for an estimated BOM line and a canonical material or production component. Optional wastage is recorded separately from consumption. |
| `labour` | Actual team hours or explicit hours per worker multiplied by the covered worker count. Links an attributed work window, workflow task, and any source-documented planned team hours. |
| `installation` | Actual start and completion, elapsed time, and lateness at each end of the recorded workflow window. Preserves planned duration, task revision, and dependencies. |
| `wastage` | Actual material waste, unit, category, optional activity, and any sourced expected wastage. |
| `vendor` | Project-linked supplier, observation period, dimension, recorded outcome, reviewer, and optional incident or delay links. |
| `client_feedback` | Source, written feedback or explicitly reported rating with its scale, event design version, and optional captured design elements. Verification requires a completed event. |
| `concept_decision` | Final Part 1 approval or rejection bound to the exact Part 2 design version, including actor, time, and recorded feedback. Can link an existing verified Project Memory item for the same decision. |
| `weather_incident`, `production_incident`, `delay` | Recorded occurrence, impact, applicable source attribution, and optional resolution. Weather links the event venue and affected activities. Delays use a pinned workflow window. |
| `change_order` | Original and changed scope, request and approval attribution, signed cost, unit-specific quantity, and schedule impact, plus version and affected tasks. |
| `final_media` | A managed image or video reference, recorded capture date if known, uploader and upload metadata, review status, and optional captured scene or object. |

Use `origin: reported` for a sourced report or `origin: observed` for a documented direct observation. Outcome edits replace the draft payload and require its current revision. Verification records the reviewer, timestamp and review note, then locks the outcome. A correction uses `supersedes_id` to create a new draft for the same scope and kind. Verifying the correction replaces the prior input in future reports while retaining the old record. Owners, admins and designers can record and edit drafts. Members can read within the authorized project. Quantitative verification requires a documentary reference. Files used as visual evidence provide context and cannot on their own verify money, labour or quantities. Exact-version Part 3 audit IDs can be linked as design context.

Scoped asset IDs and metadata are omitted from reads if the file is withdrawn, deleted, or outside the current project or event scope. The data model retains verified history and prevents deletion of a project or event that still has learning records. Drafts can be discarded. Asset retention continues through the existing lifecycle. Client feedback and provider traffic stay separate.

### Reconciliation and learning methodology

Reconciliation reads the exact event design snapshot and explicitly linked Phase 10 workflow revisions. Cost uses the original estimated total for the chosen budget or BOM line. Consumption uses the selected line's planned quantity. Labour and wastage can carry separately sourced plan values. Installation prefers the captured planned task duration and uses the authored window if only that window exists.

Set `coverage: complete` only when the outcome covers the entire estimate scope. The default is `partial`. A report compares a single verified, complete actual for each baseline. Multiple competing totals are ambiguous. Partial amounts remain separately readable and are never blindly added. Reports preserve estimate and actual values, units, currency, source record IDs and revisions, signed difference, absolute magnitude, and percentage change where the original estimate is nonzero. Unit conversion follows Phase 10 rules and rounds converted quantities to six decimal places. Percentage change is rounded to four decimal places. Different currencies require reporting separately. Missing estimate, missing or draft actual, incompatible unit, currency mismatch, incomplete coverage, and ambiguity have explicit outcomes. Use `estimates` to request rows for captured cost, quantity, or installation baselines even when the corresponding actual is absent. Re-running against the same inputs returns the saved reconciliation.

Learning reports review specified events within one authorized project, using at most one explicitly selected reconciliation per event. A pattern requires supporting evidence from at least two distinct events. Counts show supporting and eligible events in each comparison group. Qualifying patterns include cost, quantity, labour, installation and waste deviations, vendor delivery misses, activity delays, recurring weather and production categories, sourced reasons on approved change orders, and recorded concept decisions. Vendor observations require consistent delivery outcomes within each supporting event. Creative observations concern the decisions recorded and leave client preferences to explicit feedback. Insights include period, method, provenance, confidence, uncertainty and a proposed human review. Confidence is limited and can be moderate when at least five eligible events all support the observation. The backend does not apply changes from report dispositions.

Optional AI uses the existing provider router to suggest a bounded review emphasis. Its input contains system-defined categories, sample counts, and opaque evidence identifiers. Private notes, financial values and media files stay out of provider requests. An accepted emphasis is labeled `ai_inferred` and retains the cited evidence ID. An outage or unsupported citation leaves the deterministic insights available. Independent review and explicit recorded outcomes are required for actuals.

### API, migration and verification

All routes are under `/api/v1/projects/{project_id}/learning`. Send an optional UUID in `X-Request-ID` to correlate activity. History routes support `offset` and `limit`.

| Route | Behavior |
| --- | --- |
| `POST /actuals`, `GET /actuals` | Create a draft or list outcomes by required `event_id`, with optional `kind`, `status`, and `supplier_id`. Use kind filters for vendor history, feedback, incident, change-order and media records. |
| `GET /actuals/{id}`, `PATCH /actuals/{id}`, `DELETE /actuals/{id}` | Read, edit, or discard a draft. Update and discard require the current revision. |
| `POST /actuals/{id}/verify` | Finalize a draft with its expected revision and reviewer note. |
| `POST /reconciliations`, `GET /reconciliations` | Reconcile one event and design version or list prior reports, optionally filtered by event. |
| `GET /reconciliations/{id}`, `GET /reconciliations/{id}/comparisons` | Read the report or filter its comparisons by metric. |
| `POST /insights`, `GET /insights` | Generate or list learning reports. Generation accepts event IDs, optional reconciliation IDs, opt-in AI, and an optional UUID idempotency key. |
| `GET /insights/{id}`, `GET /insights/{id}/evidence`, `GET /insights/{id}/items/{insight_id}` | Read a learning report, its evidence, or one insight. |
| `POST /insights/{id}/items/{insight_id}/dispositions`, `GET` on the same path | Record and list attributed review decisions. |

Apply Alembic revision `d914b27f803c` with `alembic upgrade head`. PostgreSQL verifies event, tenant and exact snapshot scope and protects finalized records. Downgrading to `67e52ccf940a` removes the Part 4 tables and preserves earlier design and audit data. Run `python -m pytest -q tests/test_learning.py tests/test_learning_migration.py` against a dedicated `TEST_DATABASE_URL` for focused tests, `python -m pytest -q` for the regression suite, and `node --test tests/browser/geometry.test.mjs` for the browser geometry tests.

Part 4 verification passed 334 Python tests, including 11 focused learning and migration tests, and four browser geometry tests. A seeded local PostgreSQL upgrade, downgrade, and re-upgrade preserved existing Part 3 design and audit data. `alembic check` reported no schema drift. AI citation checks and provider failure fallback used a test provider.

Reconciliation and insight generation use bounded requests in the existing backend. Each report accepts up to 500 relevant outcomes and insight generation accepts up to 20 events. There is no new queue. Historical report quality depends on the coverage and scope of source records, especially for labour and wastage plans, partial invoices, and older design snapshots. Pattern evidence is descriptive. Live provider behavior, production load and externally verified predictive performance require their own validation.

## Phase 11 Part 5 — Integration, testing, and hardening

### Integration inventory

| Component | Models and migration | API and owning services | Dependencies, access, and baseline coverage |
| --- | --- | --- | --- |
| Presentation and approval | `Presentation`, `PresentationRevision`, `PresentationAsset`, `PresentationCheckpoint` in `73fd6a4ce211` | Project `/presentations` and revision-scoped approvals. `presentation`, `presentation_sources`, `presentation_validation`. | Canonical design, scene, Asset Management and Phase 10 package evidence. Members read, editors author or request review, and owners/admins decide. Presentation and migration tests cover completeness, scope, concurrency, and revision history. |
| Versions, branches, and Project Memory | `DesignSnapshot`, `DesignVersion`, `DesignBranch`, `DesignVersionReview`, `DesignActivity`, `ProjectMemoryItem`, `ProjectMemoryRevision` in or extended by `921d7c306abe` | Project `/design-versions`, `/design-branches`, `/design-activity`, and `/project-memory/items`. `design_version`, `design_snapshot`, `design_activity`, `project_memory_item`. | Event Graph, published presentation pins, managed assets, and Phase 10 estimate snapshots. Editors version and fork, and owners/admins bind approvals, promote branches and verify memory. Version and memory tests cover lineage, decision links, archival and migration. |
| Design Audit | `DesignAudit` in `67e52ccf940a` | Project `/design-audits`. `design_audit`, `design_audit_context`, deterministic rules, and optional routed interpretation. | Exact design snapshot, Phase 9 geometry, Phase 10 estimates and production, and pinned presentation and checkpoints. Members read and editors run/retry. Tests cover all 18 dimensions, evidence, fallback, comparison and history. |
| Learning and actuals | `LearningActual`, `LearningReport`, `LearningDisposition` in `d914b27f803c` | Project `/learning/actuals`, `/learning/reconciliations`, and `/learning/insights`. Learning source, actual, report, reconciliation and insight services. | Phase 10 budgets, BOMs, workflow and suppliers, plus exact versions, approvals, memory, audit and media. Members read, editors record and generate, and owners/admins verify actuals. Tests cover precision, corrections, scope, event samples and provider fallback. |

The repository audit confirmed a single migration chain and found application-only checks for some approval relationships, a deletion path that could surface a foreign-key failure, and repeated asset and event lookups in learning history. The integration plan is to enforce exact approval relationships in PostgreSQL, make write conflicts and retained history explicit, batch scoped reads, and exercise the complete lifecycle on a migrated local schema.

### Cross-module workflow

Use the existing API families in this order when the event needs an exact design approval and an auditable closeout.

1. Author and validate the event presentation with scoped Phase 10 and design evidence. Capture its exact revision in a design version, bind review, and request the checkpoint. Owners and admins can record the decision. Subsequent presentation revisions have their own checkpoints and preserve the older decision and snapshot.
2. Fork or restore designs using their recorded parent and source identifiers. Promotion appends a mainline version. Record relevant decisions in Project Memory. Start the audit for the target version and read its dimension evidence and recommendations. Comparing runs with incompatible configurations leaves that dimension marked non-comparable.
3. Record and verify event actuals against the appropriate captured estimate or production task revision. Reconcile the exact version. Later corrections produce new inputs and leave prior reconciliations readable.
4. Request project-scoped insights using the selected events and reconciliations. Use the evidence and disposition endpoints to review recommendations. Designs, budgets and production plans are edited separately through their owning services.

### Integrity, recovery, and observability

Migration `e35f18c42a79` adds the event/project foreign key for presentations and guards same-event alternatives, checkpoint identity, and exact design review bindings. It validates existing presentation and binding rows before activation. Downgrade removes the new guards and restores the earlier foreign key. The seeded test checks that a failed upgrade rolls back and that a successful re-upgrade retains Phase 10 budget and BOM records and Phase 11 presentation, approval, version, memory, audit, actual and reconciliation records.

Authorization uses the existing organization role and project workspace checks, with explicit event and source scope checks. Presented evidence is rechecked before approval. An asset withdrawn after submission blocks approval until evidence is available again. Historical reads check current asset access while retaining the underlying protected records. Container deletions report a retained-history conflict before removal. Expected integrity conflicts roll back the operation and return a scoped error.

Presentation writes and review decisions record attributed activity. Completed audit and learning report operations log correlation and scope IDs, outcome, duration, warning codes and available provider metadata. The learning activity and optional provider execution share the same request ID. Tests exercise normalized provider timeout, outage and invalid-output handling and check that fixture credentials and database error detail stay out of responses and application logs.

### Verification and remaining limits

Use a disposable PostgreSQL `TEST_DATABASE_URL` whose database name contains `insho_test_db`. The full suite can use a local Chrome binary via `INSHO_CHROME`. The integration suite creates and removes a separate schema and uses actual backend routes, Alembic constraints and database records. Provider tests supply a local test transport.

- `python -m pytest -q -r a -s tests/test_phase11_integration.py` runs the integrated workflows, recovery tests and query budgets.
- `python -m pytest -q -r a` runs the complete backend regression suite.
- `node --test tests/browser/geometry.test.mjs` runs browser geometry tests.
- `alembic upgrade head` and `alembic check` verify the selected database. The migration suite performs rollback and re-upgrade on disposable data. [Migration notes](migrations/README)

Final verification on disposable PostgreSQL 18.6 passed 343 Python tests, including nine migrated-schema integration tests, and four browser geometry tests. The final runs reported zero failures and zero skips. PostgreSQL reached the single Alembic head and `alembic check` reported no schema drift. Python compilation and offline migration SQL generation also passed.

Actuals pages check all referenced assets in one scoped batch. Insight generation batches event and report lookups. In one single-client run using the in-process API transport, a 100-row actuals page took 10.1 ms and nine SQL statements, including one asset lookup. Reconciliation of 500 inputs took 33.4 ms and 18 statements. Insight generation from 500 observations across 20 events took 102.9 ms and 11 statements. These timings describe the local fixtures and vary with the environment.

Part 3 still limits subjective review to captured descriptive evidence and managed-asset metadata. Earlier snapshots can lack provenance captured by newer versions. Spatial review remains bounded to 250 objects and zones and 100 findings per scene. Safety findings remain unscored and preliminary. Part 4 learning remains project-scoped and reconciliation requires one complete verified actual per baseline. Insight observations are descriptive. Asset downloads use authenticated private streaming. Signed object-store URLs remain outside the implemented local storage path. Live providers, distributed tracing, real Redis worker operation, production storage, production load and predictive accuracy remain unverified. Phase 11 audit and learning requests use the existing bounded request paths. The separate asset-processing worker retains its existing task retry policy.
