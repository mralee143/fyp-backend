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
├── main.py                  # Application entry point (pending implementation)
├── config.py                # Configuration management with Pydantic Settings
├── prisma/
│   └── schema.prisma       # Database schema definition
├── routes/                  # API route handlers (pending implementation)
│   ├── auth.py             # Authentication endpoints
│   └── users.py            # User management endpoints
├── schemas/
│   ├── user.py             # User-related Pydantic schemas
│   └── auth.py             # Auth-related Pydantic schemas
├── services/
│   ├── auth.py             # Password hashing and JWT token logic
│   └── database.py         # Prisma client management
├── middleware/              # (pending implementation)
│   └── auth.py             # JWT authentication middleware
└── tests/
    ├── conftest.py         # Shared test fixtures
    ├── test_config_properties.py
    ├── test_email_validation_properties.py
    ├── test_jwt_properties.py
    ├── test_password_hashing_properties.py
    └── test_user_properties.py
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

## Development Status

This project is currently undergoing migration from Flask + SQLAlchemy to FastAPI + Prisma ORM.

### Completed (Tasks 1-7)
✅ Project infrastructure and dependencies  
✅ Configuration management with Pydantic Settings  
✅ Prisma schema definition and client generation  
✅ Pydantic schemas for validation  
✅ Authentication service (password hashing, JWT tokens)  
✅ Database service (Prisma client management)  
✅ Comprehensive property-based tests using Hypothesis  

### In Progress
🚧 Authentication middleware (Task 8)  
🚧 Authentication routes (Task 9)  
🚧 User routes (Task 10)  
🚧 FastAPI application setup (Task 12)  
🚧 Error handling (Task 13)  

### Testing

Run the test suite:
```bash
pytest
```

Run with coverage:
```bash
pytest --cov
```

The project includes both unit tests and property-based tests using Hypothesis to ensure correctness across a wide range of inputs. All core services (configuration, password hashing, JWT tokens, database operations) are fully tested.

## Running the Application

⚠️ **Note**: The FastAPI application (`main.py`) is not yet fully implemented. The following commands will be available once migration is complete:

Start the development server:
```bash
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`

## API Documentation

Once the server is running, access the interactive API documentation:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI Schema: `http://localhost:8000/openapi.json`

## API Endpoints

### Authentication

- `POST /auth/signup` - Register a new user
  - Request: `{ "email": "user@example.com", "password": "securepass" }`
  - Response: User object with id, email, isActive, timestamps

- `POST /auth/login` - Authenticate and receive JWT token
  - Request: Form data with username (email) and password
  - Response: `{ "access_token": "jwt_token", "token_type": "bearer" }`

### User Management

- `GET /users/me` - Get current user profile (requires authentication)
  - Headers: `Authorization: Bearer <jwt_token>`
  - Response: User object

## Testing

Run the test suite:
```bash
pytest
```

Run with coverage:
```bash
pytest --cov
```

The project includes both unit tests and property-based tests using Hypothesis to ensure correctness across a wide range of inputs.

## Security Features

- Passwords are hashed using bcrypt with automatic salt generation
- Bcrypt's 72-byte limit is properly handled by truncating input
- JWT tokens with configurable expiration
- Protected endpoints require valid authentication
- Email format validation using Pydantic's EmailStr
- Duplicate email prevention via database unique constraint
- Sensitive data (passwords) excluded from API responses
- Token signature validation prevents tampering

## Development

### Database Migrations

After modifying `prisma/schema.prisma`:
```bash
prisma db push
```

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