from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.auth import router as auth_router
from app.api.inventory import router as inventory_router
from app.api.checkout import router as checkout_router
from app.api.purchase_requests import router as purchase_requests_router
from app.api.demo import router as demo_router
from app.api.vendors import router as vendors_router
from app.api.contact import router as contact_router
from app.core.config import settings

app = FastAPI(title="ShelfSync API", version="0.1.0", description="Inventory, forecasting, purchasing and advisory AI APIs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(contact_router)
app.include_router(router)
app.include_router(auth_router)
app.include_router(inventory_router)
app.include_router(checkout_router)
app.include_router(purchase_requests_router)
app.include_router(demo_router)
app.include_router(vendors_router)
