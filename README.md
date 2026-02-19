# Authentication API

A modern user authentication system built with FastAPI and Prisma ORM, providing secure user registration, login, and profile management through RESTful API endpoints.

## Features

- User registration with email validation
- JWT-based authentication
- Secure password hashing with bcrypt
- User profile management
- Automatic API documentation (Swagger UI & ReDoc)
- Type-safe database operations with Prisma ORM
- Async/await support for high performance
- PostgreSQL database backend

## Technology Stack

- FastAPI 0.104+ - Modern async web framework
- Prisma 0.11+ - Next-generation ORM with type safety
- PostgreSQL - Production-grade relational database
- Pydantic 2.5+ - Data validation and settings management
- python-jose - JWT token generation and validation
- bcrypt 4.1+ - Secure password hashing with automatic salt generation
- Uvicorn - ASGI server

## Project Structure

```
├── main.py                  # Application entry point with lifespan management
├── config.py                # Configuration management with Pydantic Settings
├── prisma/
│   └── schema.prisma       # Database schema definition
├── routes/
│   ├── auth.py             # Authentication endpoints (signup, login)
│   └── users.py            # User management endpoints (profile)
├── schemas/
│   ├── user.py             # User-related Pydantic schemas
│   └── auth.py             # Auth-related Pydantic schemas
├── services/
│   ├── auth.py             # Password hashing and JWT token logic
│   └── database.py         # Prisma client management
├── middleware/
│   └── auth.py             # JWT authentication middleware
├── tests/
    ├── conftest.py                        # Shared test fixtures
    ├── test_api_documentation.py          # API documentation endpoint tests
    ├── test_config_properties.py          # Configuration validation tests
    ├── test_email_validation_properties.py # Email format validation tests
    ├── test_jwt_properties.py             # JWT token tests
    ├── test_password_hashing_properties.py # Password hashing tests
    ├── test_user_properties.py            # User model tests
    ├── test_registration_properties.py    # User registration tests
    ├── test_login_properties.py           # User login tests
    ├── test_profile_access_properties.py  # Profile access tests
    ├── test_sensitive_data_exclusion_properties.py # Sensitive data exclusion tests
    ├── test_auth_middleware_properties.py # Auth middleware tests
    ├── test_error_handling.py             # Error handling tests
    └── test_integration.py                # End-to-end integration tests
```

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set up environment variables:
Create a `.env` file based on `.env.example`:
```env
# Required
DATABASE_URL=postgresql://user:password@localhost:5432/dbname
SECRET_KEY=your-secret-key-minimum-32-characters-long

# Optional (with defaults)
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
APP_NAME=Authentication API
DEBUG=false
```

3. Generate Prisma client:
```bash
prisma generate
```

4. Set up the database:
```bash
prisma db push
```

5. Verify database schema (optional):
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
🚧 Final system verification (Task 16)  

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
```

The project includes comprehensive testing:
- **Property-based tests** using Hypothesis validate system behavior across hundreds of randomly generated inputs
- **Integration tests** verify complete user flows from signup through login to profile access
- **Unit tests** for error handling, API documentation, and edge cases
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

Optional (with defaults):
- `ALGORITHM` - JWT signing algorithm (default: HS256)
- `ACCESS_TOKEN_EXPIRE_MINUTES` - Token expiration in minutes (default: 30)
- `APP_NAME` - Application name (default: Authentication API)
- `DEBUG` - Debug mode flag (default: false)

The application will fail to start with a clear validation error if required variables are missing.