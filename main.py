# =============================================================================
# main.py - FastAPI Endpoints & WebSocket Hub
# =============================================================================

import os
import json
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import (
    FastAPI, HTTPException, Depends, Header,
    WebSocket, WebSocketDisconnect, Query, Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

import functions as fn

load_dotenv()


# =============================================================================
# 1. APP LIFECYCLE
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    fn.init_db()
    yield


app = FastAPI(title="Maison Restaurant", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# =============================================================================
# WEBSOCKET HUB
# =============================================================================


class ConnectionHub:
    def __init__(self):
        self.connections: dict[str, list[WebSocket]] = {}

    async def connect(self, ws: WebSocket, role: str):
        await ws.accept()
        self.connections.setdefault(role, []).append(ws)

    def disconnect(self, ws: WebSocket, role: str):
        if role in self.connections:
            self.connections[role] = [c for c in self.connections[role] if c != ws]

    async def broadcast(self, message: dict, roles: list[str] = None):
        targets = roles or list(self.connections.keys())
        payload = json.dumps(message)
        for role in targets:
            dead = []
            for ws in self.connections.get(role, []):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.connections[role].remove(ws)


hub = ConnectionHub()


# =============================================================================
# AUTH DEPENDENCIES
# =============================================================================


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    payload = fn.decode_token(authorization[7:])
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_waiter_or_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] not in ("waiter", "admin"):
        raise HTTPException(status_code=403, detail="Waiter access required")
    return user


def require_kitchen_or_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] not in ("kitchen", "admin"):
        raise HTTPException(status_code=403, detail="Kitchen access required")
    return user


# =============================================================================
# PYDANTIC REQUEST SCHEMAS
# =============================================================================


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str = Field(min_length=6)
    role: str = Field(pattern="^(waiter|kitchen)$")


class CreateMenuItemRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    price: float = Field(gt=0)
    category: str


class UpdateMenuItemRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = Field(default=None, gt=0)
    category: Optional[str] = None


class ToggleMenuItemRequest(BaseModel):
    is_available: bool


class CreateTableRequest(BaseModel):
    table_number: int = Field(gt=0)
    capacity: int = Field(gt=0)


class OpenSessionRequest(BaseModel):
    table_id: int


class OrderItemSchema(BaseModel):
    menu_item_id: int
    quantity: int = Field(gt=0)


class CreateOrderRequest(BaseModel):
    session_id: int
    table_id: int
    items: list[OrderItemSchema] = Field(min_length=1)
    notes: Optional[str] = ""


# =============================================================================
# 2. AUTH ENDPOINTS
# =============================================================================


@app.post("/auth/login")
def login(body: LoginRequest):
    user = fn.get_user_by_username(body.username)
    if not user or not fn.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Account is deactivated")
    token = fn.create_token(user["id"], user["username"], user["role"])
    fn.log_action(user["id"], "login", "user", user["id"], f"Login: {user['username']}")
    return {"token": token, "role": user["role"], "username": user["username"]}


@app.get("/auth/me")
def get_me(user: dict = Depends(get_current_user)):
    return user


# =============================================================================
# 3. USER MANAGEMENT (ADMIN ONLY)
# =============================================================================


@app.post("/users", status_code=201)
def create_user(body: CreateUserRequest, admin: dict = Depends(require_admin)):
    try:
        return fn.create_user(body.username, body.password, body.role, admin["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/users")
def list_users(admin: dict = Depends(require_admin)):
    return fn.get_all_users()


@app.delete("/users/{user_id}")
def deactivate_user(user_id: int, admin: dict = Depends(require_admin)):
    try:
        fn.deactivate_user(user_id, admin["user_id"])
        return {"message": "User deactivated"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# =============================================================================
# 4. MENU ENDPOINTS
# =============================================================================


@app.get("/menu")
def get_menu(available_only: bool = Query(True), user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        available_only = True
    return fn.get_menu_items(available_only)


@app.post("/menu", status_code=201)
def add_menu_item(body: CreateMenuItemRequest, admin: dict = Depends(require_admin)):
    try:
        return fn.create_menu_item(body.name, body.description, body.price, body.category, admin["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/menu/{item_id}")
def update_menu_item(item_id: int, body: UpdateMenuItemRequest, admin: dict = Depends(require_admin)):
    try:
        return fn.update_menu_item(item_id, body.model_dump(exclude_none=True), admin["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.patch("/menu/{item_id}/toggle")
def toggle_menu_item(item_id: int, body: ToggleMenuItemRequest, admin: dict = Depends(require_admin)):
    try:
        return fn.toggle_menu_item(item_id, body.is_available, admin["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# =============================================================================
# 5. TABLE ENDPOINTS
# =============================================================================


@app.get("/tables")
def list_tables(user: dict = Depends(get_current_user)):
    return fn.get_all_tables()


@app.post("/tables", status_code=201)
def add_table(body: CreateTableRequest, admin: dict = Depends(require_admin)):
    try:
        return fn.create_table(body.table_number, body.capacity, admin["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


# =============================================================================
# 6. SESSION ENDPOINTS
# =============================================================================


@app.post("/sessions", status_code=201)
def open_session(body: OpenSessionRequest, user: dict = Depends(require_waiter_or_admin)):
    if not fn.get_table(body.table_id):
        raise HTTPException(status_code=404, detail="Table not found")
    return fn.create_session(body.table_id, user["user_id"])


@app.get("/sessions/{session_id}")
def get_session_orders(session_id: int, user: dict = Depends(get_current_user)):
    return fn.get_orders_by_session(session_id)


@app.delete("/sessions/{session_id}")
def close_session(session_id: int, admin: dict = Depends(require_admin)):
    try:
        return fn.close_session(session_id, admin["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# =============================================================================
# 7. ORDER ENDPOINTS
# =============================================================================


@app.post("/orders", status_code=201)
async def create_order(body: CreateOrderRequest, user: dict = Depends(require_waiter_or_admin)):
    try:
        items = [i.model_dump() for i in body.items]
        order = fn.create_order(body.session_id, body.table_id, user["user_id"], items, body.notes)
        await hub.broadcast({"type": "new_order", "order": order}, roles=["kitchen", "admin"])
        return order
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/orders/kitchen")
def get_kitchen_queue(status: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    if user["role"] not in ("kitchen", "admin"):
        raise HTTPException(status_code=403, detail="Not authorized")
    return fn.get_kitchen_orders(status)


@app.get("/orders/{order_id}")
def get_order(order_id: int, user: dict = Depends(get_current_user)):
    order = fn.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.patch("/orders/{order_id}/status")
async def advance_status(order_id: int, user: dict = Depends(get_current_user)):
    if user["role"] not in ("kitchen", "admin", "waiter"):
        raise HTTPException(status_code=403, detail="Not authorized")
    try:
        order = fn.advance_order_status(order_id, user["user_id"])
        await hub.broadcast(
            {"type": "status_update", "order_id": order_id, "status": order["status"]},
            roles=["kitchen", "waiter", "admin"],
        )
        return order
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/orders/{order_id}")
async def cancel_order(order_id: int, user: dict = Depends(require_waiter_or_admin)):
    try:
        order = fn.cancel_order(order_id, user["user_id"])
        await hub.broadcast({"type": "order_cancelled", "order_id": order_id}, roles=["kitchen", "admin"])
        return order
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# 8. BILLING (ADMIN ONLY)
# =============================================================================


@app.get("/bill/{session_id}")
def get_bill(session_id: int, admin: dict = Depends(require_admin)):
    try:
        return fn.get_session_bill(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/invoice/{session_id}")
def download_invoice(session_id: int, admin: dict = Depends(require_admin)):
    try:
        pdf = fn.generate_invoice_pdf(session_id, admin["user_id"])
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=invoice_{session_id}.pdf"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# 9. ANALYTICS (ADMIN ONLY)
# =============================================================================


@app.get("/stats")
def get_stats(days: int = Query(30, ge=1, le=365), admin: dict = Depends(require_admin)):
    return fn.get_statistics(days)


@app.get("/earnings")
def get_earnings(days: int = Query(30, ge=1, le=365), admin: dict = Depends(require_admin)):
    return fn.get_earnings(days)


# =============================================================================
# 10. LOGS (ADMIN ONLY)
# =============================================================================


@app.get("/logs")
def get_logs(
    limit: int = Query(200, ge=1, le=1000),
    entity_type: Optional[str] = Query(None),
    admin: dict = Depends(require_admin),
):
    return fn.get_logs(limit, entity_type)


# =============================================================================
# 11. WEBSOCKET
# =============================================================================


@app.websocket("/ws/{role}")
async def websocket_endpoint(websocket: WebSocket, role: str, token: str = Query(...)):
    payload = fn.decode_token(token)
    if not payload:
        await websocket.close(code=4001)
        return
    await hub.connect(websocket, role)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except Exception:
                pass
    except WebSocketDisconnect:
        hub.disconnect(websocket, role)


# =============================================================================
# FRONTEND ROUTES
# =============================================================================


@app.get("/", response_class=HTMLResponse)
def serve_login():
    with open("static/login.html") as f:
        return f.read()


@app.get("/waiter", response_class=HTMLResponse)
def serve_waiter():
    with open("static/waiter.html") as f:
        return f.read()


@app.get("/kitchen", response_class=HTMLResponse)
def serve_kitchen():
    with open("static/kitchen.html") as f:
        return f.read()


@app.get("/admin", response_class=HTMLResponse)
def serve_admin():
    with open("static/admin.html") as f:
        return f.read()
