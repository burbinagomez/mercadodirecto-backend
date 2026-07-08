"""MercadoDirecto API entrypoint."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import Base, engine
import app.models  # noqa: F401  (register models on Base.metadata)
from app.routers import auth, farmers, consumers, products, cart, orders

Base.metadata.create_all(bind=engine)

app = FastAPI(title="MercadoDirecto API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(farmers.router)
app.include_router(consumers.router)
app.include_router(products.router)
app.include_router(cart.router)
app.include_router(orders.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
