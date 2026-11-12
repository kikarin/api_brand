# platform-api

Satu backend FastAPI untuk **Mockup Studio**, **Fitting Store**, dan (nanti) **E-com Try On**.

Acuan: [`docs/blueprint-fresh.md`](../docs/blueprint-fresh.md), [`docs/session-lifecycle.md`](../docs/session-lifecycle.md).

## Stack

- FastAPI + SQLAlchemy + Alembic
- PostgreSQL + Redis
- Decart client token (`TRYON_MODE=mock` default)
- Cloudflare R2 (opsional sampai upload live)

## Quick start

Dari root repo:

```bash
docker compose up -d
cd platform-api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

- Health: http://localhost:8000/health  
- Docs: http://localhost:8000/docs  

## Seed cepat (opsional)

```bash
python -m app.scripts.seed_dev
```

Membuat brand demo + owner `owner@demo.brand` / `demo1234` dengan modul studio + fitting dan kuota awal.

## Modul route

| Prefix | Isi |
|--------|-----|
| `/api/auth` | Register / login JWT |
| `/api/sessions` | Start / Mulai / Jeda / Lanjut / garment / end |
| `/api/mockups` · `/api/collections` | Katalog studio |
| `/api/etalase` · `/api/fitting` | Fitting Store |
| `/api/share-links` · `/api/snapshots` | Share + snapshot |

Permanent `DECART_API_KEY` hanya di sini. FE hanya terima client token pendek saat Mulai / Lanjut.
# api_brand
