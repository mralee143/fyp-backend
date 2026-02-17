# Database Setup Instructions

## PostgreSQL Setup with Docker Compose

### 1. Start PostgreSQL Container

```bash
docker-compose up -d
```

This will start a PostgreSQL 14 container with:
- **Database Name**: vision_db
- **Username**: root
- **Password**: postgres
- **Port**: 5432 (mapped to localhost)

### 2. Verify Container is Running

```bash
docker-compose ps
```

You should see the `vision_db_postgres` container running.

### 3. Check Container Logs

```bash
docker-compose logs postgres
```

Look for "database system is ready to accept connections"

### 4. Install Prisma (if not already installed)

```bash
pip install prisma
```

### 5. Test Database Connection

```bash
python test_db_connection.py
```

Expected output:
```
✅ Configuration loaded successfully!
✅ Successfully connected to PostgreSQL database!
✅ PostgreSQL version: PostgreSQL 14.x ...
```

## Database Management Commands

### Stop the database
```bash
docker-compose stop
```

### Start the database (after stopping)
```bash
docker-compose start
```

### Remove the database (WARNING: deletes all data)
```bash
docker-compose down -v
```

### Access PostgreSQL CLI
```bash
docker exec -it vision_db_postgres psql -U root -d vision_db
```

### View database logs
```bash
docker-compose logs -f postgres
```

## Connection Details

- **Host**: localhost
- **Port**: 5432
- **Database**: vision_db
- **Username**: root
- **Password**: postgres
- **Connection String**: `postgresql://root:postgres@localhost:5432/vision_db`

## Troubleshooting

### Port 5432 already in use
If you have another PostgreSQL instance running:
```bash
# Stop other PostgreSQL services
# Or change the port in docker-compose.yml:
# ports:
#   - "5433:5432"
# Then update DATABASE_URL in .env to use port 5433
```

### Container won't start
```bash
# Check logs
docker-compose logs postgres

# Remove and recreate
docker-compose down -v
docker-compose up -d
```

### Connection refused
```bash
# Wait for database to be ready
docker-compose logs postgres | grep "ready to accept connections"

# Check health status
docker inspect vision_db_postgres | grep Health
```
