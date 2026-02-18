# PostgreSQL Database Setup Guide

This guide will help you set up PostgreSQL for the FastAPI authentication application.

## Option 1: Using Docker (Recommended)

### Prerequisites
- Docker Desktop installed and running

### Steps

1. **Start PostgreSQL using Docker Compose**
   ```bash
   docker-compose up -d
   ```

2. **Verify the database is running**
   ```bash
   docker ps
   ```
   You should see a container named `postgres` running.

3. **The database is now ready!**
   - Database name: `vision_db`
   - Username: `root`
   - Password: `postgres`
   - Port: `5432`
   - Connection string is already configured in `.env`

4. **Connect to the database (optional)**
   ```bash
   docker exec -it postgres psql -U root -d vision_db
   ```

5. **Stop the database when done**
   ```bash
   docker-compose down
   ```

## Option 2: Local PostgreSQL Installation

### Windows

1. **Download PostgreSQL**
   - Visit https://www.postgresql.org/download/windows/
   - Download the installer (version 14 or higher)
   - Run the installer and follow the setup wizard

2. **During installation**
   - Set a password for the `postgres` superuser (remember this!)
   - Keep the default port `5432`
   - Install pgAdmin 4 (optional, but useful for GUI management)

3. **Create the database**
   - Open Command Prompt or PowerShell
   - Connect to PostgreSQL:
     ```bash
     psql -U postgres
     ```
   - Create the database:
     ```sql
     CREATE DATABASE vision_db;
     CREATE USER root WITH PASSWORD 'postgres';
     GRANT ALL PRIVILEGES ON DATABASE vision_db TO root;
     \q
     ```

4. **Update `.env` file**
   ```env
   DATABASE_URL=postgresql://root:postgres@localhost:5432/vision_db
   ```

### macOS

1. **Install PostgreSQL using Homebrew**
   ```bash
   brew install postgresql@14
   brew services start postgresql@14
   ```

2. **Create the database**
   ```bash
   psql postgres
   ```
   ```sql
   CREATE DATABASE vision_db;
   CREATE USER root WITH PASSWORD 'postgres';
   GRANT ALL PRIVILEGES ON DATABASE vision_db TO root;
   \q
   ```

3. **Update `.env` file**
   ```env
   DATABASE_URL=postgresql://root:postgres@localhost:5432/vision_db
   ```

### Linux (Ubuntu/Debian)

1. **Install PostgreSQL**
   ```bash
   sudo apt update
   sudo apt install postgresql postgresql-contrib
   sudo systemctl start postgresql
   sudo systemctl enable postgresql
   ```

2. **Create the database**
   ```bash
   sudo -u postgres psql
   ```
   ```sql
   CREATE DATABASE vision_db;
   CREATE USER root WITH PASSWORD 'postgres';
   GRANT ALL PRIVILEGES ON DATABASE vision_db TO root;
   \q
   ```

3. **Update `.env` file**
   ```env
   DATABASE_URL=postgresql://root:postgres@localhost:5432/vision_db
   ```

## Verify Database Connection

After setting up PostgreSQL, verify the connection:

```bash
python test_db_connection.py
```

This script will test the database connection and confirm everything is working.

## Next Steps

Once the database is set up and verified:

1. **Generate Prisma Client**
   ```bash
   prisma generate
   ```

2. **Push the schema to the database**
   ```bash
   prisma db push
   ```

3. **Run the application**
   ```bash
   uvicorn main:app --reload
   ```

## Troubleshooting

### Connection Refused
- Ensure PostgreSQL is running
- Check if the port 5432 is not blocked by firewall
- Verify the DATABASE_URL in `.env` matches your setup

### Authentication Failed
- Double-check username and password in DATABASE_URL
- Ensure the user has proper permissions on the database

### Database Does Not Exist
- Create the database using the SQL commands above
- Ensure the database name in DATABASE_URL matches the created database

### Docker Issues
- Ensure Docker Desktop is running
- Try `docker-compose down` and then `docker-compose up -d` again
- Check logs: `docker-compose logs postgres`

## Database Management Tools

### Command Line
- **psql**: PostgreSQL interactive terminal
  ```bash
  psql -U root -d vision_db
  ```

### GUI Tools
- **pgAdmin 4**: Free, open-source PostgreSQL management tool
- **DBeaver**: Universal database tool
- **DataGrip**: JetBrains IDE for databases (paid)

## Security Notes

⚠️ **Important**: The default credentials in this setup are for development only!

For production:
1. Use strong, unique passwords
2. Store credentials in secure secret management systems
3. Use SSL/TLS for database connections
4. Restrict database access by IP address
5. Regularly update PostgreSQL to the latest version
