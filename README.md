# Authentication API with Image Upload

A modern user authentication system built with FastAPI and Prisma ORM, providing secure user registration, login, profile management, and image upload capabilities through RESTful API endpoints.

## Features

- User registration with email validation
- JWT-based authentication
- Secure password hashing with bcrypt
- User profile management
- Image upload and storage with MinIO
- Image validation (file type, size, MIME type)
- User-specific image organization
- Image retrieval and listing
- Automatic API documentation (Swagger UI & ReDoc)
- Type-safe database operations with Prisma ORM
- Async/await support for high performance
- PostgreSQL database backend

## Technology Stack

- FastAPI 0.104+ - Modern async web framework
- Prisma 0.11+ - Next-generation ORM with type safety
- PostgreSQL - Production-grade relational database
- MinIO - S3-compatible object storage for images
- minio 7.2.0 - MinIO Python SDK for object storage operations
- Pydantic 2.5+ - Data validation and settings management
- python-jose - JWT token generation and validation
- bcrypt 4.1+ - Secure password hashing with automatic salt generation
- Uvicorn - ASGI server

## Project Structure

Three top-level areas: the API, the UI, and the LLM agents that sit on top of
both. `backend/` is the working directory the API and the worker run from — its
subpackages import each other as `services.x`, `agentic.x`, `ml.x`.

```
├── backend/                    # FastAPI API + ARQ worker (run from here)
│   ├── main.py                 # App entry point, lifespan, router registration
│   ├── worker.py               # ARQ worker — the whole video analysis pipeline
│   ├── config.py               # Pydantic Settings, reads ../.env
│   ├── routes/                 # HTTP endpoints
│   │   ├── auth.py             #   signup, login, OTP, password reset
│   │   ├── users.py            #   profile
│   │   ├── images.py           #   image upload / retrieval
│   │   ├── detection.py        #   synchronous detection endpoints
│   │   ├── jobs.py             #   async analysis jobs + SSE progress stream
│   │   ├── admin.py            #   admin dashboard data
│   │   └── webhooks.py         #   outbound webhook management
│   ├── services/               # Business logic
│   │   ├── auth.py             #   password hashing, JWT
│   │   ├── database.py         #   Prisma client management
│   │   ├── minio_client.py     #   object storage (user images)
│   │   ├── media_store.py      #   object storage (videos, frames, clips)
│   │   ├── queue.py            #   ARQ job queue
│   │   ├── events.py           #   Redis pub/sub feeding the SSE stream
│   │   ├── cache.py            #   Redis cache
│   │   ├── scan_*.py           #   scan persistence (repository / writer / store)
│   │   ├── frame_extract.py    #   frame sampling
│   │   ├── clip_extract.py     #   incident clip cutting
│   │   ├── annotate.py         #   box drawing
│   │   └── *_detection.py      #   YOLO, OWLv2, action, violence, Gemini, Qwen
│   ├── agentic/                # Everything LLM-driven — see its own section
│   │   ├── prompts/            #   every system prompt, as editable .md files
│   │   ├── chat_agent.py       #   the orchestrator the user talks to
│   │   ├── agent_tools.py      #   tools the orchestrator can call
│   │   ├── db_agent.py         #   natural language -> read-only SQL
│   │   ├── scan_chat.py        #   grounded Q&A about one analysed video
│   │   ├── qwen_chat.py        #   chat completion client
│   │   ├── chat_store.py       #   conversation persistence
│   │   ├── schemas.py          #   chat request/response models
│   │   └── routes/             #   /chat and /detection/scans endpoints
│   ├── ml/                     # Model code, importable and runnable standalone
│   │   ├── vid_img.py          #   detection primitives shared by services/
│   │   └── qwen_infer.py       #   runs inside qwen_env, spawned as a subprocess
│   ├── schemas/                # Pydantic request/response models
│   ├── middleware/auth.py      # JWT authentication middleware
│   ├── prisma/schema.prisma    # Database schema
│   ├── scripts/                # Host-side one-offs (seeding, migration, manual tests)
│   ├── docker/                 # Image entrypoint, supervisor, model pre-download
│   ├── tests/                  # pytest + hypothesis suite
│   ├── Dockerfile              # Backend image (build context is backend/)
│   └── requirements.txt
├── frontend/                   # Next.js UI (its own image and build context)
│   └── src/
│       ├── app/(auth)/         #   login, signup, verify, password reset
│       ├── app/(app)/          #   dashboard, analyze, results, report, chat, admin
│       ├── components/         #   UI components
│       ├── lib/                #   API clients
│       └── store/              #   Zustand stores
├── docs/                       # Architecture, pipeline and schema notes
├── docker-compose.yml          # Backend + frontend, plus the split-stack profile
└── .env                        # Shared config (gitignored; see .env.example)
```

### The agents

`backend/agentic/` holds the three LLM agents and nothing else, so the
model-driven behaviour is separable from the deterministic pipeline:

| Agent | Module | What it does |
| --- | --- | --- |
| Orchestrator | `chat_agent.py` | The chatbot. Decides when to call a tool (analyse a video, query the database, drive playback, grab a still). |
| Database analyst | `db_agent.py` | Turns a plain-English question into one read-only `SELECT` over the user's scans, then explains the rows. |
| Scan chat | `scan_chat.py` | Answers questions about one specific analysed video, grounded in its summary, incidents and frame images. |

Their prompts live in `backend/agentic/prompts/`, one folder per agent, as plain
`.md` files loaded at import time:

```
agentic/prompts/
├── orchestrator/system.md      # the chatbot's instructions and tool policy
├── db_agent/
│   ├── schema.md               # the three relations the SQL agent may query
│   ├── sql_system.md           # SQL-writing rules ({schema}, {max_rows} template)
│   └── insight_system.md       # how to explain the returned rows
└── scan_chat/
    ├── system.md               # grounded Q&A rules for a whole video
    └── segment.md              # reading one incident from its strip of stills
```

Prompt wording is the main lever on answer quality, so it is kept as text rather
than as a string constant — reword a rule, restart the API, and the agent
behaves differently without a code change. `prompts.load("db_agent/schema")`
reads them; paths resolve relative to the package, not the working directory.

## Setup

> **Run every backend command from `backend/`.** That is the import root for
> `main.py`, `worker.py` and the `services` / `agentic` / `ml` packages. The
> venvs (`env`, `qwen_env`) and `.env` stay at the repo root and are shared, so
> from `backend/` the interpreter is `../env/Scripts/python.exe`.
> `RUN.md` has the exact commands for this machine.

1. Install dependencies:
```bash
cd backend
pip install -r requirements.txt
```

2. Set up environment variables:
Create a `.env` file based on `.env.example`:
```env
# Required
DATABASE_URL=postgresql://user:password@localhost:5432/dbname
SECRET_KEY=your-secret-key-minimum-32-characters-long

# MinIO Configuration (required for image upload)
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET_NAME=user-images
MINIO_SECURE=false

# Optional (with defaults)
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
APP_NAME=Authentication API
DEBUG=false
```

3. Start the backing services (PostgreSQL, Redis, MinIO):
```bash
# From the repo root. These sit behind the "split" compose profile, so a bare
# `docker compose up -d` skips them and starts the all-in-one backend instead.
docker compose up -d postgres redis minio
```

4. Verify MinIO integration (optional but recommended):
```bash
# The API creates and checks both buckets during startup — watch the log for
# "MinIO client initialized successfully" and "Media bucket ready".
curl http://localhost:9000/minio/health/live
```

5. Generate Prisma client:
```bash
prisma generate
```

6. Set up the database:
```bash
prisma db push
```

7. Verify database schema (optional):
```bash
python verify_schema.py
```

## Development Status

This project has successfully migrated from Flask + SQLAlchemy to FastAPI + Prisma ORM.

### Completed (Tasks 1-15.1)
✅ Project infrastructure and dependencies  
✅ Configuration management with Pydantic Settings  
✅ Prisma schema definition and client generation  
✅ Pydantic schemas for validation  
✅ Authentication service (password hashing, JWT tokens)  
✅ Database service (Prisma client management)  
✅ Authentication middleware (JWT token validation)  
✅ Authentication routes (signup, login)  
✅ User routes (profile endpoint)  
✅ FastAPI application with lifespan management  
✅ API documentation endpoints (/docs, /redoc, /openapi.json)  
✅ Comprehensive property-based tests using Hypothesis  
✅ Error handling for all routes  
✅ Database setup and migrations  
✅ End-to-end integration tests  

### In Progress
🚧 Image upload system with MinIO integration (Tasks 2-13)
- ✅ Image utility functions (validation, naming, hashing)
- ✅ Image upload authentication tests
  - ✅ Property 1: Unauthenticated Request Rejection
  - ✅ Property 2: Authenticated Request Processing
- ✅ Image upload error handling tests
  - ✅ Property 16: Descriptive Error Messages (no extension, invalid extension, oversized files, MIME mismatches)
  - ✅ MinIO unavailability scenarios (upload, retrieval, uninitialized client)
- ✅ Image retrieval round-trip tests
  - ✅ Property 12: Image Retrieval Round-Trip (upload/download integrity)
  - ✅ Unit tests for specific content and large files
- ✅ Database schema updates for Image model
- ✅ MinIO client service implementation
- ✅ Image upload and retrieval endpoints
- ⏳ Image listing endpoint  

### Testing

Run the test suite:
```bash
pytest
```

Run with coverage:
```bash
pytest --cov
```

Run specific test categories:
```bash
# Property-based tests only
pytest tests/test_*_properties.py

# Integration tests only
pytest tests/test_integration.py

# Error handling tests
pytest tests/test_error_handling.py

# Image upload error handling tests
pytest tests/test_error_handling_images.py

# Image upload authentication tests
pytest tests/test_image_upload_auth_properties.py

# Image retrieval tests
pytest tests/test_image_retrieval_properties.py
```

The project includes comprehensive testing:
- **Property-based tests** using Hypothesis validate system behavior across hundreds of randomly generated inputs
- **Integration tests** verify complete user flows from signup through login to profile access
- **Unit tests** for error handling, API documentation, and edge cases
- **Image upload authentication tests** validate both unauthenticated rejection and authenticated request processing
- **Image upload error handling tests** verify descriptive error messages and MinIO unavailability scenarios
- **Image retrieval tests** validate round-trip integrity and ownership authorization
- All core services, middleware, routes, and API endpoints are fully tested

### Property-Based Testing

The test suite uses Hypothesis for property-based testing, which validates system behavior across hundreds of randomly generated inputs. This approach catches edge cases that traditional unit tests might miss.

Tested properties include:
- **Password Hashing**: Passwords never stored in plain text, verification correctness
- **JWT Tokens**: Signature validity, user identity preservation, expiration handling
- **Email Validation**: Invalid email format rejection
- **User Registration**: Valid registration creates accounts, duplicate emails rejected
- **User Login**: Valid credentials return tokens, invalid credentials rejected
- **Profile Access**: Authenticated users can access profiles, inactive users rejected
- **Auth Middleware**: Invalid tokens rejected with 401 status
- **Image Upload Authentication**: 
  - Property 1: Unauthenticated requests rejected with 401
  - Property 2: Authenticated requests processed (not rejected with 401)
- **Image Upload Error Handling**:
  - Property 16: Descriptive error messages for validation failures (missing extension, invalid extension, oversized files, MIME type mismatches)
  - MinIO unavailability returns 503 Service Unavailable
  - Uninitialized MinIO client returns 503
- **Image Retrieval**:
  - Property 12: Round-trip integrity (uploaded content matches retrieved content)
- **Sensitive Data Exclusion**: Hashed passwords never appear in API responses (attributes, serialized dicts, or JSON)
- **Configuration**: Missing required environment variables fail startup
- **Database**: Auto-incrementing IDs, unique email constraints

Each property test runs 100+ examples by default to ensure robust validation.

### Integration Testing

End-to-end integration tests verify complete user flows:
- **Complete signup → login → profile flow**: Tests the entire user journey from registration through authentication to profile access
- **Invalid token handling**: Verifies rejection of missing, invalid, malformed, and empty tokens
- **Duplicate registration**: Ensures duplicate email addresses are properly rejected with 409 Conflict
- **Invalid credentials**: Tests login failures with non-existent emails and wrong passwords
- **Inactive user handling**: Verifies inactive users cannot log in
- **Expired token rejection**: Tests that expired JWT tokens are properly rejected
- **Email validation**: Validates that invalid email formats return 422 with field-level error details
- **API documentation**: Confirms Swagger UI, ReDoc, and OpenAPI schema endpoints are accessible

Integration tests use FastAPI's TestClient to make real HTTP requests to the API, testing the full stack from routes through middleware to database operations.

## Running the Application

Start the development server using either method:

**Method 1: Direct execution (recommended for development)**
```bash
python main.py
```

**Method 2: Using uvicorn command**
```bash
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`

The application automatically:
- Connects to the database on startup
- Disconnects from the database on shutdown
- Provides interactive API documentation at `/docs` and `/redoc`
- Runs with auto-reload enabled in development mode (when using `python main.py`)
- Binds to all network interfaces (0.0.0.0) for accessibility

## API Documentation

Once the server is running, access the interactive API documentation:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI Schema: `http://localhost:8000/openapi.json`
- Health Check: `http://localhost:8000/`

The FastAPI application automatically generates comprehensive API documentation based on your route definitions, Pydantic schemas, and docstrings.

## API Endpoints

### Authentication

- `POST /auth/signup` - Register a new user
  - Request: `{ "email": "user@example.com", "password": "securepass" }`
  - Response: `201 Created` with user object (id, email, is_active, created_at, updated_at)
  - Errors: `409 Conflict` if email already registered, `422 Unprocessable Entity` for validation errors

- `POST /auth/login` - Authenticate and receive JWT token
  - Request: Form data with `username` (email) and `password` fields (OAuth2 password flow)
  - Response: `{ "access_token": "jwt_token", "token_type": "bearer" }`
  - Errors: `401 Unauthorized` for invalid credentials, `400 Bad Request` for inactive users

### User Management

- `GET /users/me` - Get current user profile (requires authentication)
  - Headers: `Authorization: Bearer <jwt_token>`
  - Response: User object (id, email, is_active, created_at, updated_at)
  - Errors: `401 Unauthorized` for invalid/missing token, `400 Bad Request` for inactive users

### Image Management (Coming Soon)

- `POST /images/upload` - Upload an image (requires authentication)
  - Headers: `Authorization: Bearer <jwt_token>`
  - Request: Multipart form data with image file
  - Supported formats: JPG, JPEG, PNG, SVG, BMP, WEBP, GIF, TIFF, ICO
  - Max file size: 50MB
  - Response: `201 Created` with image metadata (id, object_key, filenames, size, MIME type, upload timestamp)
  - Errors: `400 Bad Request` for validation errors, `401 Unauthorized` for missing token, `503 Service Unavailable` for storage errors

- `GET /images/{image_id}` - Retrieve an image (requires authentication)
  - Headers: `Authorization: Bearer <jwt_token>`
  - Response: Image file with appropriate Content-Type header
  - Errors: `401 Unauthorized`, `403 Forbidden` (not owner), `404 Not Found`

- `GET /images/` - List all user's images (requires authentication)
  - Headers: `Authorization: Bearer <jwt_token>`
  - Response: Array of image metadata with total count
  - Errors: `401 Unauthorized`

## Testing

Run the test suite:
```bash
pytest
```

Run with coverage:
```bash
pytest --cov
```

Run specific test categories:
```bash
# Property-based tests only
pytest tests/test_*_properties.py

# Integration tests only
pytest tests/test_integration.py

# Error handling tests
pytest tests/test_error_handling.py

# Image upload error handling tests
pytest tests/test_error_handling_images.py

# API documentation tests
pytest tests/test_api_documentation.py
```

The project includes comprehensive testing:
- **Property-based tests** using Hypothesis validate system behavior across hundreds of randomly generated inputs
- **Integration tests** verify complete user flows from signup through login to profile access
- **Unit tests** for error handling, API documentation, and edge cases
- All core services, middleware, routes, and API endpoints are fully tested

## Security Features

- Passwords are hashed using bcrypt with automatic salt generation
- Bcrypt's 72-byte limit is properly handled by truncating input
- JWT tokens with configurable expiration (default: 30 minutes)
- Protected endpoints require valid JWT authentication via OAuth2 bearer tokens
- Email format validation using Pydantic's EmailStr
- Duplicate email prevention via database unique constraint
- Sensitive data (hashed passwords) excluded from API responses
- Token signature validation prevents tampering
- Expired token rejection with proper error handling
- Inactive user accounts cannot access protected endpoints
- Auto-incrementing user IDs for database integrity

## Development

### Database Migrations

After modifying `prisma/schema.prisma`:
```bash
prisma db push
```

### Database Verification

Verify that the Prisma schema is correctly applied to PostgreSQL:
```bash
python verify_schema.py
```

This script checks:
- Table existence
- Column names, types, and nullable constraints
- Primary key and unique constraints
- Indexes

### Database Connection Testing

Test the database connection:
```bash
python test_db_connection.py
```

### MinIO Integration Testing

Bucket setup runs during application startup, so booting the API is the check:
```bash
cd backend && ../env/Scripts/python.exe -m uvicorn main:app --port 8000
```

The startup log confirms:
- MinIO client initialization with configured credentials
- Bucket creation (if it doesn't exist)
- Bucket existence verification
- Connection to MinIO service

Run this before starting the application to ensure MinIO is properly configured.

### API Documentation Verification

Verify that all API documentation endpoints are accessible:
```bash
python verify_api_docs.py
```

This script checks:
- Root endpoint (/)
- OpenAPI schema (/openapi.json)
- Swagger UI (/docs)
- ReDoc (/redoc)

### Adding New Routes

1. Create route file in `routes/`
2. Define Pydantic schemas in `schemas/`
3. Register router in `main.py`

## Environment Variables

Configuration is managed through Pydantic Settings with automatic validation on startup.

Required:
- `DATABASE_URL` - PostgreSQL connection string
- `SECRET_KEY` - Secret key for JWT signing (minimum 32 characters recommended)

MinIO Configuration (required for image upload):
- `MINIO_ENDPOINT` - MinIO server endpoint (e.g., localhost:9000)
- `MINIO_ACCESS_KEY` - MinIO access key
- `MINIO_SECRET_KEY` - MinIO secret key
- `MINIO_BUCKET_NAME` - Bucket name for storing images
- `MINIO_SECURE` - Use HTTPS for MinIO connection (true/false)

Optional (with defaults):
- `ALGORITHM` - JWT signing algorithm (default: HS256)
- `ACCESS_TOKEN_EXPIRE_MINUTES` - Token expiration in minutes (default: 30)
- `APP_NAME` - Application name (default: Authentication API)
- `DEBUG` - Debug mode flag (default: false)

The application will fail to start with a clear validation error if required variables are missing.

## Image Upload Features

The system includes comprehensive image upload capabilities with the following features:

### Validation
- **File Extensions**: Supports JPG, JPEG, PNG, SVG, BMP, WEBP, GIF, TIFF, ICO
- **File Size**: Maximum 50MB per image
- **MIME Type Verification**: Ensures Content-Type matches file extension
- **Security**: Validates file integrity and prevents malicious uploads

### Storage Organization
- **User Isolation**: Each user's images stored in hashed folder (e.g., `user_5f4dcc3b/`)
- **Unique Filenames**: Automatic timestamp-based naming prevents collisions
- **Snake Case Normalization**: Filenames converted to lowercase snake_case
- **Privacy**: User folder names are hashed for privacy protection

### Metadata Tracking
- Original filename preservation
- Generated filename with timestamp
- File size and MIME type
- Upload timestamp
- Object storage key for retrieval

### Image Utilities (`services/image_utils.py`)

The image utilities module provides:

- `generate_hashed_user_id(user_id)` - Creates consistent, privacy-preserving folder names using MD5 hashing
- `generate_unique_filename(original_filename)` - Generates timestamped, snake_case filenames
- `to_snake_case(filename)` - Converts filenames to lowercase snake_case format
- `validate_file_extension(filename)` - Validates file has an allowed extension
- `validate_mime_type(content_type, extension)` - Ensures MIME type matches extension
- `validate_file_size(file_size)` - Enforces 50MB size limit

Constants:
- `ALLOWED_EXTENSIONS` - Set of supported image formats
- `MIME_TYPE_MAP` - Mapping of extensions to MIME types
- `MAX_FILE_SIZE` - Maximum file size (50MB)