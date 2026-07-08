# MercadoDirecto — Backend API

FastAPI service for the farm-to-table marketplace connecting Colombian farmers
directly with consumers.

## Stack
- **FastAPI** — web framework
- **SQLAlchemy 2.0** — ORM
- **PostgreSQL** — database (psycopg)
- **Alembic** — migrations
- **Pydantic v2** — schemas / validation
- **JWT (python-jose)** — auth
- **bcrypt** — password hashing

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill DB_URL, JWT_SECRET
alembic upgrade head
uvicorn app.main:app --reload
```

## API Layout
| Prefix | Purpose |
|--------|---------|
| `/auth` | signup / login / me |
| `/farmers` | farmer profile (me) |
| `/consumers` | consumer profile (me) |
| `/products` | listing CRUD + browse |
| `/cart` | shopping cart |
| `/orders` | checkout + history |

## Tests
```bash
pytest
```
