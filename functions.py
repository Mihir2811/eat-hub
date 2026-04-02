# =============================================================================
# functions.py - Core Business Logic with SQLAlchemy ORM Models
# =============================================================================
#
# FLOW:
# 1. Models         -> User, MenuItem, Table, TableSession, Order,
#                      OrderItem, ActionLog  (SQLAlchemy declarative)
# 2. DB Init        -> init_db()  creates tables from models on first run
# 3. DB Session     -> _db()  plain session for internal use
#                      get_db()  FastAPI generator dependency
# 4. Auth           -> hash_password (bcrypt, cost=12)
#                      verify_password
#                      create_token / decode_token  (NO expiry)
# 5. User Mgmt      -> create_user, get_user_by_username,
#                      get_all_users, deactivate_user
# 6. Menu           -> create_menu_item, get_menu_items,
#                      update_menu_item, toggle_menu_item
# 7. Tables         -> create_table, get_all_tables, get_table
# 8. Sessions       -> create_session, get_active_session, close_session
# 9. Orders         -> create_order, get_orders_by_session,
#                      get_order, get_kitchen_orders
# 10. Order Items   -> get_order_items  (immutable price snapshots)
# 11. FSM           -> advance_order_status  (received->preparing->ready->served)
#                      cancel_order  (received only)
# 12. Billing       -> get_session_bill, generate_invoice_pdf
# 13. Analytics     -> get_statistics, get_earnings
# 14. Logging       -> _log (internal), log_action (public), get_logs
# =============================================================================

import os, hmac, base64, json, io, hashlib
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from dotenv import load_dotenv
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    DateTime, ForeignKey, Text, Index, event, func
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///restaurant.db")
SECRET_KEY   = os.getenv("SECRET_KEY", "change-this-secret-key")

Base           = declarative_base()
_engine        = None
_SessionFactory = None


def _get_engine():
    """Lazy-initialize the SQLAlchemy engine once, reuse thereafter."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            DATABASE_URL,
            connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
            echo=False,
        )
        if DATABASE_URL.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _fk(conn, _):
                conn.execute("PRAGMA foreign_keys=ON")
    return _engine


def _get_factory():
    """Lazy-initialize the session factory."""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=_get_engine(), autoflush=True, autocommit=False)
    return _SessionFactory


# Valid FSM transitions - no backward movement
ORDER_STATUS_TRANSITIONS = {
    "received":  "preparing",
    "preparing": "ready",
    "ready":     "served",
    "served":    None,
}


# =============================================================================
# 1. SQLALCHEMY MODELS
# =============================================================================


class User(Base):
    """Staff account - role: admin | waiter | kitchen."""
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    username      = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    role          = Column(String(20), nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_active     = Column(Boolean, default=True, nullable=False)

    orders   = relationship("Order",        back_populates="waiter",         lazy="select")
    sessions = relationship("TableSession", back_populates="opened_by_user", lazy="select")
    logs     = relationship("ActionLog",    back_populates="user",           lazy="select")


class MenuItem(Base):
    """Menu item; price is snapshotted at order time so history stays accurate."""
    __tablename__ = "menu_items"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    name         = Column(String(128), nullable=False)
    description  = Column(Text, default="")
    price        = Column(Float, nullable=False)
    category     = Column(String(64), nullable=False)
    is_available = Column(Boolean, default=True, nullable=False)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)

    order_items = relationship("OrderItem", back_populates="menu_item", lazy="select")


class Table(Base):
    """Physical restaurant table."""
    __tablename__ = "tables"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    table_number = Column(Integer, unique=True, nullable=False)
    capacity     = Column(Integer, nullable=False)
    is_active    = Column(Boolean, default=True, nullable=False)

    sessions = relationship("TableSession", back_populates="table", lazy="select")
    orders   = relationship("Order",        back_populates="table", lazy="select")


class TableSession(Base):
    """Customer session on a table - persists until admin closes it after billing."""
    __tablename__ = "table_sessions"
    id        = Column(Integer, primary_key=True, autoincrement=True)
    table_id  = Column(Integer, ForeignKey("tables.id"), nullable=False)
    opened_by = Column(Integer, ForeignKey("users.id"),  nullable=False)
    opened_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    table          = relationship("Table", back_populates="sessions", lazy="select")
    opened_by_user = relationship("User",  back_populates="sessions", lazy="select")
    orders         = relationship("Order", back_populates="session",  lazy="select")

    __table_args__ = (Index("ix_sessions_table_active", "table_id", "is_active"),)


class Order(Base):
    """Order placed by a waiter under an active table session."""
    __tablename__ = "orders"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("table_sessions.id"), nullable=False)
    table_id   = Column(Integer, ForeignKey("tables.id"),         nullable=False)
    waiter_id  = Column(Integer, ForeignKey("users.id"),          nullable=False)
    status     = Column(String(20), default="received", nullable=False)
    notes      = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    session = relationship("TableSession", back_populates="orders", lazy="select")
    table   = relationship("Table",        back_populates="orders", lazy="select")
    waiter  = relationship("User",         back_populates="orders", lazy="select")
    items   = relationship("OrderItem", back_populates="order",
                           cascade="all, delete-orphan", lazy="select")

    __table_args__ = (
        Index("ix_orders_session", "session_id"),
        Index("ix_orders_status",  "status"),
    )


class OrderItem(Base):
    """Immutable snapshot of a menu item at the time an order was placed."""
    __tablename__ = "order_items"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    order_id      = Column(Integer, ForeignKey("orders.id"),     nullable=False)
    menu_item_id  = Column(Integer, ForeignKey("menu_items.id"), nullable=False)
    item_name     = Column(String(128), nullable=False)
    price_at_time = Column(Float, nullable=False)
    quantity      = Column(Integer, nullable=False)

    order     = relationship("Order",    back_populates="items",       lazy="select")
    menu_item = relationship("MenuItem", back_populates="order_items", lazy="select")


class ActionLog(Base):
    """Audit trail - one row written for every staff action."""
    __tablename__ = "action_logs"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=True)
    username    = Column(String(80),  nullable=True)
    action      = Column(String(80),  nullable=False)
    entity_type = Column(String(40),  nullable=True)
    entity_id   = Column(Integer,     nullable=True)
    details     = Column(Text,        nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="logs", lazy="select")
    __table_args__ = (Index("ix_logs_created_at", "created_at"),)


# =============================================================================
# 2. DB INIT & SESSION HELPERS
# =============================================================================


def init_db():
    """Create all ORM tables from models. Safe to call on an existing database."""
    Base.metadata.create_all(bind=_get_engine())


def get_db():
    """FastAPI dependency: yield a SQLAlchemy session, commit/rollback on exit."""
    factory = _get_factory()
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _db() -> Session:
    """Open and return a plain session for standalone function calls."""
    return _get_factory()()


# =============================================================================
# 3. AUTH - bcrypt hashing, HMAC tokens, NO expiry
# =============================================================================


def hash_password(password: str) -> str:
    """Hash a plain-text password with bcrypt at cost factor 12."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, stored_hash: str) -> bool:
    """Return True if the plain-text password matches the stored bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
    except Exception:
        return False


def create_token(user_id: int, username: str, role: str) -> str:
    """Create a permanent HMAC-SHA256 signed token. No expiry field is included."""
    payload = json.dumps({"user_id": user_id, "username": username, "role": role})
    encoded = base64.urlsafe_b64encode(payload.encode()).decode()
    sig     = hmac.new(SECRET_KEY.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{sig}"


def decode_token(token: str) -> Optional[dict]:
    """Verify token signature and return the payload dict, or None if invalid."""
    try:
        encoded, sig = token.rsplit(".", 1)
        expected = hmac.new(SECRET_KEY.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return json.loads(base64.urlsafe_b64decode(encoded.encode()).decode())
    except Exception:
        return None


# =============================================================================
# 4. USER MANAGEMENT
# =============================================================================


def create_user(username: str, password: str, role: str,
                created_by_id: Optional[int] = None) -> dict:
    """Create a staff account. Raises ValueError if username is already taken."""
    if role not in ("admin", "waiter", "kitchen"):
        raise ValueError(f"Invalid role '{role}'. Must be admin, waiter, or kitchen.")
    db = _db()
    try:
        if db.query(User).filter(User.username == username).first():
            raise ValueError(f"Username '{username}' already exists")
        user = User(username=username, password_hash=hash_password(password), role=role)
        db.add(user)
        db.flush()
        _log(db, created_by_id, "create_user", "user", user.id, f"Created {role}: {username}")
        db.commit()
        return {"id": user.id, "username": user.username, "role": user.role}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_user_by_username(username: str) -> Optional[dict]:
    """Return full user record including password_hash for login verification."""
    db = _db()
    try:
        u = db.query(User).filter(User.username == username).first()
        if not u:
            return None
        return {
            "id":            u.id,
            "username":      u.username,
            "password_hash": u.password_hash,
            "role":          u.role,
            "is_active":     u.is_active,
        }
    finally:
        db.close()


def get_all_users() -> list:
    """Return all staff accounts without password hashes, ordered by ID."""
    db = _db()
    try:
        return [
            {
                "id":         u.id,
                "username":   u.username,
                "role":       u.role,
                "created_at": u.created_at.isoformat(),
                "is_active":  u.is_active,
            }
            for u in db.query(User).order_by(User.id).all()
        ]
    finally:
        db.close()


def deactivate_user(user_id: int, admin_id: int) -> bool:
    """Soft-delete a staff account by setting is_active = False."""
    db = _db()
    try:
        u = db.query(User).filter(User.id == user_id).first()
        if not u:
            raise ValueError(f"User {user_id} not found")
        u.is_active = False
        _log(db, admin_id, "deactivate_user", "user", user_id, f"Deactivated: {u.username}")
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# =============================================================================
# 5. MENU MANAGEMENT
# =============================================================================


def create_menu_item(name: str, description: str, price: float,
                     category: str, admin_id: int) -> dict:
    """Add a new item to the menu. Price must be a positive number."""
    if price <= 0:
        raise ValueError("Price must be greater than zero")
    db = _db()
    try:
        item = MenuItem(name=name, description=description, price=price, category=category)
        db.add(item)
        db.flush()
        _log(db, admin_id, "create_menu_item", "menu_item", item.id, f"Added: {name} @ {price}")
        db.commit()
        return _item_dict(item)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_menu_items(available_only: bool = True) -> list:
    """Return menu items ordered by category then name."""
    db = _db()
    try:
        q = db.query(MenuItem)
        if available_only:
            q = q.filter(MenuItem.is_available == True)
        return [_item_dict(i) for i in q.order_by(MenuItem.category, MenuItem.name).all()]
    finally:
        db.close()


def update_menu_item(item_id: int, updates: dict, admin_id: int) -> dict:
    """Update allowed fields (name, description, price, category) on a menu item."""
    allowed = {"name", "description", "price", "category"}
    db = _db()
    try:
        item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
        if not item:
            raise ValueError(f"Menu item {item_id} not found")
        for k, v in updates.items():
            if k in allowed:
                setattr(item, k, v)
        _log(db, admin_id, "update_menu_item", "menu_item", item_id, str(updates))
        db.commit()
        return _item_dict(item)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def toggle_menu_item(item_id: int, is_available: bool, admin_id: int) -> dict:
    """Enable or disable a menu item without deleting it."""
    db = _db()
    try:
        item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
        if not item:
            raise ValueError(f"Menu item {item_id} not found")
        item.is_available = is_available
        _log(db, admin_id, "toggle_menu_item", "menu_item", item_id,
             "enabled" if is_available else "disabled")
        db.commit()
        return _item_dict(item)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _item_dict(item: MenuItem) -> dict:
    return {
        "id":           item.id,
        "name":         item.name,
        "description":  item.description,
        "price":        item.price,
        "category":     item.category,
        "is_available": item.is_available,
        "created_at":   item.created_at.isoformat(),
    }


# =============================================================================
# 6. TABLE MANAGEMENT
# =============================================================================


def create_table(table_number: int, capacity: int, admin_id: int) -> dict:
    """Register a new physical table. Raises if table number already exists."""
    db = _db()
    try:
        if db.query(Table).filter(Table.table_number == table_number).first():
            raise ValueError(f"Table {table_number} already exists")
        t = Table(table_number=table_number, capacity=capacity)
        db.add(t)
        db.flush()
        _log(db, admin_id, "create_table", "table", t.id,
             f"Table {table_number}, capacity {capacity}")
        db.commit()
        return {"id": t.id, "table_number": t.table_number, "capacity": t.capacity}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_all_tables() -> list:
    """Return all active tables with their current session status."""
    db = _db()
    try:
        tables = db.query(Table).filter(Table.is_active == True).order_by(Table.table_number).all()
        result = []
        for t in tables:
            s = (db.query(TableSession)
                   .filter(TableSession.table_id == t.id, TableSession.is_active == True)
                   .first())
            result.append({
                "id":           t.id,
                "table_number": t.table_number,
                "capacity":     t.capacity,
                "is_active":    t.is_active,
                "session_id":   s.id          if s else None,
                "opened_at":    s.opened_at.isoformat() if s else None,
            })
        return result
    finally:
        db.close()


def get_table(table_id: int) -> Optional[dict]:
    """Fetch a single table record by ID, or None if not found."""
    db = _db()
    try:
        t = db.query(Table).filter(Table.id == table_id).first()
        return {"id": t.id, "table_number": t.table_number, "capacity": t.capacity} if t else None
    finally:
        db.close()


# =============================================================================
# 7. SESSION MANAGEMENT
# =============================================================================


def create_session(table_id: int, waiter_id: int) -> dict:
    """Open a new session for a table, or return the existing active session."""
    existing = get_active_session(table_id)
    if existing:
        return existing
    db = _db()
    try:
        s = TableSession(table_id=table_id, opened_by=waiter_id)
        db.add(s)
        db.flush()
        _log(db, waiter_id, "open_session", "session", s.id, f"Table {table_id} opened")
        db.commit()
        return {"id": s.id, "table_id": s.table_id,
                "opened_at": s.opened_at.isoformat(), "is_active": s.is_active}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_active_session(table_id: int) -> Optional[dict]:
    """Return the active session for a table, or None if the table is free."""
    db = _db()
    try:
        s = (db.query(TableSession)
               .filter(TableSession.table_id == table_id, TableSession.is_active == True)
               .first())
        if not s:
            return None
        return {"id": s.id, "table_id": s.table_id,
                "opened_at": s.opened_at.isoformat(), "is_active": s.is_active}
    finally:
        db.close()


def close_session(session_id: int, admin_id: int) -> dict:
    """Close a session after billing. Table becomes free for new customers."""
    db = _db()
    try:
        s = db.query(TableSession).filter(TableSession.id == session_id).first()
        if not s:
            raise ValueError(f"Session {session_id} not found")
        s.is_active = False
        s.closed_at = datetime.utcnow()
        _log(db, admin_id, "close_session", "session", session_id, "Closed after billing")
        db.commit()
        return {"id": s.id, "is_active": s.is_active, "closed_at": s.closed_at.isoformat()}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# =============================================================================
# 8. ORDER MANAGEMENT
# =============================================================================


def create_order(session_id: int, table_id: int, waiter_id: int,
                 items: list, notes: str = "") -> dict:
    """Create an order with immutable item snapshots. items=[{menu_item_id, quantity}]."""
    if not items:
        raise ValueError("Order must contain at least one item")
    db = _db()
    try:
        order = Order(session_id=session_id, table_id=table_id,
                      waiter_id=waiter_id, notes=notes, status="received")
        db.add(order)
        db.flush()
        for entry in items:
            mi = (db.query(MenuItem)
                    .filter(MenuItem.id == entry["menu_item_id"],
                            MenuItem.is_available == True)
                    .first())
            if not mi:
                raise ValueError(f"Menu item {entry['menu_item_id']} not available")
            db.add(OrderItem(
                order_id=order.id, menu_item_id=mi.id,
                item_name=mi.name, price_at_time=mi.price, quantity=entry["quantity"],
            ))
        _log(db, waiter_id, "create_order", "order", order.id,
             f"Session {session_id}, {len(items)} item(s)")
        db.commit()
        return {"id": order.id, "status": order.status,
                "created_at": order.created_at.isoformat()}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_orders_by_session(session_id: int) -> list:
    """Return all orders (with items) for a session, ordered by creation time."""
    db = _db()
    try:
        orders = (db.query(Order)
                    .filter(Order.session_id == session_id)
                    .order_by(Order.created_at).all())
        return [_order_dict(o) for o in orders]
    finally:
        db.close()


def get_order(order_id: int) -> Optional[dict]:
    """Fetch a single order with all its items by order ID."""
    db = _db()
    try:
        o = db.query(Order).filter(Order.id == order_id).first()
        return _order_dict(o) if o else None
    finally:
        db.close()


def get_kitchen_orders(status_filter: Optional[str] = None) -> list:
    """Return active orders for the kitchen board, with table and waiter info."""
    db = _db()
    try:
        q = db.query(Order)
        if status_filter:
            q = q.filter(Order.status == status_filter)
        else:
            q = q.filter(Order.status.notin_(["served", "cancelled"]))
        orders = q.order_by(Order.created_at).all()
        result = []
        for o in orders:
            d = _order_dict(o)
            d["table_number"] = o.table.table_number if o.table else None
            d["waiter_name"]  = o.waiter.username    if o.waiter else None
            result.append(d)
        return result
    finally:
        db.close()


def _order_dict(o: Order) -> dict:
    return {
        "id":         o.id,
        "session_id": o.session_id,
        "table_id":   o.table_id,
        "waiter_id":  o.waiter_id,
        "status":     o.status,
        "notes":      o.notes,
        "created_at": o.created_at.isoformat(),
        "updated_at": o.updated_at.isoformat(),
        "items":      [_order_item_dict(i) for i in o.items],
    }


# =============================================================================
# 9. ORDER ITEMS
# =============================================================================


def get_order_items(order_id: int) -> list:
    """Return all item snapshots for a given order."""
    db = _db()
    try:
        items = db.query(OrderItem).filter(OrderItem.order_id == order_id).all()
        return [_order_item_dict(i) for i in items]
    finally:
        db.close()


def _order_item_dict(i: OrderItem) -> dict:
    return {
        "id":            i.id,
        "order_id":      i.order_id,
        "menu_item_id":  i.menu_item_id,
        "item_name":     i.item_name,
        "price_at_time": i.price_at_time,
        "quantity":      i.quantity,
    }


# =============================================================================
# 10. ORDER STATUS FSM
# =============================================================================


def advance_order_status(order_id: int, user_id: int) -> dict:
    """Advance order to the next FSM stage. No backward transitions allowed."""
    db = _db()
    try:
        o = db.query(Order).filter(Order.id == order_id).first()
        if not o:
            raise ValueError(f"Order {order_id} not found")
        next_s = ORDER_STATUS_TRANSITIONS.get(o.status)
        if not next_s:
            raise ValueError(f"Order is already in terminal state: {o.status}")
        prev, o.status, o.updated_at = o.status, next_s, datetime.utcnow()
        _log(db, user_id, "advance_status", "order", order_id, f"{prev} -> {next_s}")
        db.commit()
        return {"id": o.id, "status": o.status, "updated_at": o.updated_at.isoformat()}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def cancel_order(order_id: int, waiter_id: int) -> dict:
    """Cancel an order. Only possible while status is 'received'."""
    db = _db()
    try:
        o = db.query(Order).filter(Order.id == order_id).first()
        if not o:
            raise ValueError(f"Order {order_id} not found")
        if o.status != "received":
            raise ValueError("Only 'received' orders can be cancelled")
        o.status, o.updated_at = "cancelled", datetime.utcnow()
        _log(db, waiter_id, "cancel_order", "order", order_id, "Cancelled by waiter")
        db.commit()
        return {"id": o.id, "status": o.status}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# =============================================================================
# 11. BILLING
# =============================================================================


def get_session_bill(session_id: int) -> dict:
    """Aggregate all non-cancelled orders for a session into a bill summary."""
    db = _db()
    try:
        s = db.query(TableSession).filter(TableSession.id == session_id).first()
        if not s:
            raise ValueError(f"Session {session_id} not found")
        orders    = db.query(Order).filter(Order.session_id == session_id,
                                            Order.status != "cancelled").all()
        all_items = []
        subtotal  = 0.0
        for o in orders:
            for item in o.items:
                subtotal += item.price_at_time * item.quantity
                all_items.append(_order_item_dict(item))
        return {
            "session_id":   session_id,
            "table_number": s.table.table_number if s.table else None,
            "items":        all_items,
            "subtotal":     round(subtotal, 2),
            "opened_at":    s.opened_at.isoformat(),
        }
    finally:
        db.close()


def generate_invoice_pdf(session_id: int, admin_id: int) -> bytes:
    """Generate and return a ReportLab PDF invoice for the completed session."""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    bill, buf = get_session_bill(session_id), io.BytesIO()
    doc, styles, elems = SimpleDocTemplate(buf, pagesize=A4), getSampleStyleSheet(), []
    elems += [
        Paragraph("RESTAURANT INVOICE", styles["Title"]), Spacer(1, 12),
        Paragraph(f"Table: {bill['table_number']}", styles["Normal"]),
        Paragraph(f"Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}", styles["Normal"]),
        Spacer(1, 12),
    ]
    seen = {}
    for item in bill["items"]:
        key = (item["item_name"], item["price_at_time"])
        seen.setdefault(key, {"n": item["item_name"], "p": item["price_at_time"], "q": 0})
        seen[key]["q"] += item["quantity"]
    data = [["Item", "Qty", "Unit Price", "Total"]]
    for v in seen.values():
        data.append([v["n"], str(v["q"]), f"${v['p']:.2f}", f"${v['p']*v['q']:.2f}"])
    data.append(["", "", "TOTAL", f"${bill['subtotal']:.2f}"])
    tbl = Table(data, colWidths=[200, 60, 100, 100])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0,0), (-1,0),  colors.grey),
        ("TEXTCOLOR",      (0,0), (-1,0),  colors.whitesmoke),
        ("FONTNAME",       (0,0), (-1,0),  "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, colors.lightgrey]),
        ("FONTNAME",       (0,-1),(-1,-1), "Helvetica-Bold"),
        ("LINEABOVE",      (0,-1),(-1,-1), 1, colors.black),
        ("GRID",           (0,0), (-1,-2), 0.5, colors.grey),
    ]))
    elems.append(tbl)
    doc.build(elems)
    log_action(admin_id, "generate_invoice", "session", session_id,
               f"Invoice for session {session_id}")
    return buf.getvalue()


# =============================================================================
# 12. ANALYTICS
# =============================================================================


def get_statistics(days: int = 30) -> dict:
    """Return aggregated operational stats for the admin dashboard."""
    db = _db()
    try:
        since = datetime.utcnow() - timedelta(days=days)
        total_orders = (db.query(func.count(Order.id))
                          .filter(Order.created_at >= since, Order.status != "cancelled")
                          .scalar() or 0)
        total_revenue = (db.query(
                             func.coalesce(
                                 func.sum(OrderItem.price_at_time * OrderItem.quantity), 0))
                           .join(Order)
                           .filter(Order.created_at >= since, Order.status != "cancelled")
                           .scalar() or 0.0)
        popular_items = (db.query(OrderItem.item_name,
                                   func.sum(OrderItem.quantity).label("total_qty"))
                           .join(Order)
                           .filter(Order.created_at >= since, Order.status != "cancelled")
                           .group_by(OrderItem.item_name)
                           .order_by(func.sum(OrderItem.quantity).desc())
                           .limit(10).all())
        table_util = (db.query(Table.table_number,
                                func.count(TableSession.id).label("sessions"))
                        .outerjoin(TableSession,
                                   (TableSession.table_id == Table.id) &
                                   (TableSession.opened_at >= since))
                        .group_by(Table.id)
                        .order_by(func.count(TableSession.id).desc())
                        .all())
        return {
            "period_days":       days,
            "total_orders":      total_orders,
            "total_revenue":     round(float(total_revenue), 2),
            "popular_items":     [{"item_name": r.item_name, "total_qty": r.total_qty}
                                  for r in popular_items],
            "table_utilization": [{"table_number": r.table_number, "sessions": r.sessions}
                                  for r in table_util],
        }
    finally:
        db.close()


def get_earnings(days: int = 30) -> list:
    """Return daily revenue and order count breakdown for the earnings chart."""
    db = _db()
    try:
        since = datetime.utcnow() - timedelta(days=days)
        rows = (db.query(
                    func.date(Order.created_at).label("date"),
                    func.count(Order.id.distinct()).label("order_count"),
                    func.coalesce(
                        func.sum(OrderItem.price_at_time * OrderItem.quantity), 0
                    ).label("revenue"))
                  .join(OrderItem)
                  .filter(Order.created_at >= since, Order.status != "cancelled")
                  .group_by(func.date(Order.created_at))
                  .order_by(func.date(Order.created_at))
                  .all())
        return [{"date": r.date, "order_count": r.order_count,
                 "revenue": round(float(r.revenue), 2)} for r in rows]
    finally:
        db.close()


# =============================================================================
# 13. LOGGING
# =============================================================================


def _log(db: Session, user_id: Optional[int], action: str,
         entity_type: Optional[str] = None, entity_id: Optional[int] = None,
         details: Optional[str] = None):
    """Insert an audit log entry within an existing open session (no commit)."""
    username = None
    if user_id:
        u = db.query(User).filter(User.id == user_id).first()
        username = u.username if u else None
    db.add(ActionLog(user_id=user_id, username=username, action=action,
                     entity_type=entity_type, entity_id=entity_id, details=details))


def log_action(user_id: Optional[int], action: str,
               entity_type: Optional[str] = None, entity_id: Optional[int] = None,
               details: Optional[str] = None):
    """Public logger that opens its own session. For use outside transactions."""
    db = _db()
    try:
        _log(db, user_id, action, entity_type, entity_id, details)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def get_logs(limit: int = 200, entity_type: Optional[str] = None) -> list:
    """Return recent audit log entries, newest first."""
    db = _db()
    try:
        q = db.query(ActionLog).order_by(ActionLog.created_at.desc())
        if entity_type:
            q = q.filter(ActionLog.entity_type == entity_type)
        return [
            {
                "id":          l.id,
                "user_id":     l.user_id,
                "username":    l.username,
                "action":      l.action,
                "entity_type": l.entity_type,
                "entity_id":   l.entity_id,
                "details":     l.details,
                "created_at":  l.created_at.isoformat(),
            }
            for l in q.limit(limit).all()
        ]
    finally:
        db.close()
