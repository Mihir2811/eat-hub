# =============================================================================
# seed_data.py - Populate DB with Realistic Test and Demo Data
# =============================================================================
#
# USAGE:
#   python seed_data.py
#
# FLOW:
# 1. init_db()      -> creates all tables (safe on existing DB)
# 2. Staff accounts -> admin, 3 waiters, 2 kitchen (skips existing)
# 3. Tables         -> 10 tables, capacities 2-8
# 4. Menu           -> 28 items across Starters, Mains, Sides, Drinks, Desserts
# 5. Active sessions-> 6 open tables with orders in each status stage
# 6. History        -> 30 days of completed orders for analytics data
#
# Re-running is safe - duplicates are silently skipped.
# =============================================================================

import os, random
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

import functions as fn
from functions import _db, User, MenuItem, Table, TableSession, Order, OrderItem, _log


# =============================================================================
# DATA DEFINITIONS
# =============================================================================

STAFF = [
    {"username": "admin",    "password": "admin123",   "role": "admin"},
    {"username": "raj",      "password": "waiter123",  "role": "waiter"},
    {"username": "priya",    "password": "waiter123",  "role": "waiter"},
    {"username": "arjun",    "password": "waiter123",  "role": "waiter"},
    {"username": "kitchen1", "password": "kitchen123", "role": "kitchen"},
    {"username": "kitchen2", "password": "kitchen123", "role": "kitchen"},
]

MENU = [
    # Starters
    {"name": "Bruschetta",           "category": "Starters",  "price": 8.50,  "description": "Grilled bread, tomato, basil"},
    {"name": "Soup of the Day",      "category": "Starters",  "price": 7.00,  "description": "Ask your waiter"},
    {"name": "Garlic Prawns",        "category": "Starters",  "price": 13.50, "description": "Pan-fried, garlic butter"},
    {"name": "Caesar Salad",         "category": "Starters",  "price": 10.00, "description": "Romaine, parmesan, croutons"},
    {"name": "Chicken Wings",        "category": "Starters",  "price": 11.00, "description": "6 wings, honey glaze or buffalo"},
    # Mains
    {"name": "Grilled Salmon",       "category": "Mains",     "price": 28.50, "description": "Atlantic salmon, seasonal veg"},
    {"name": "Ribeye Steak",         "category": "Mains",     "price": 38.00, "description": "250g, fries, house salad"},
    {"name": "Chicken Parmesan",     "category": "Mains",     "price": 22.00, "description": "Crumbed, napoli, cheese"},
    {"name": "Mushroom Risotto",     "category": "Mains",     "price": 19.50, "description": "Arborio, mixed mushrooms, parmesan"},
    {"name": "Fish and Chips",       "category": "Mains",     "price": 20.00, "description": "Beer battered, thick cut chips"},
    {"name": "Beef Burger",          "category": "Mains",     "price": 18.50, "description": "180g patty, cheese, pickles, fries"},
    {"name": "Penne Arrabbiata",     "category": "Mains",     "price": 16.00, "description": "Spicy tomato, basil"},
    # Sides
    {"name": "Fries",                "category": "Sides",     "price": 5.50,  "description": "Thin cut, seasoned"},
    {"name": "Sweet Potato Fries",   "category": "Sides",     "price": 6.50,  "description": "With aioli"},
    {"name": "Garden Salad",         "category": "Sides",     "price": 6.00,  "description": "Mixed greens, cherry tomato"},
    {"name": "Steamed Vegetables",   "category": "Sides",     "price": 5.00,  "description": "Seasonal mix"},
    # Drinks
    {"name": "Still Water",          "category": "Drinks",    "price": 3.00,  "description": "500ml bottle"},
    {"name": "Sparkling Water",      "category": "Drinks",    "price": 3.50,  "description": "500ml bottle"},
    {"name": "Fresh Orange Juice",   "category": "Drinks",    "price": 6.00,  "description": "Freshly squeezed"},
    {"name": "Soft Drink",           "category": "Drinks",    "price": 4.00,  "description": "Coke, Diet Coke, Sprite"},
    {"name": "House Wine (Glass)",   "category": "Drinks",    "price": 9.00,  "description": "Red or White"},
    {"name": "Beer",                 "category": "Drinks",    "price": 8.00,  "description": "Local draught pint"},
    {"name": "Espresso",             "category": "Drinks",    "price": 3.50,  "description": "Single shot"},
    {"name": "Flat White",           "category": "Drinks",    "price": 4.50,  "description": "Double ristretto, steamed milk"},
    # Desserts
    {"name": "Tiramisu",             "category": "Desserts",  "price": 9.50,  "description": "Mascarpone, espresso"},
    {"name": "Cheesecake",           "category": "Desserts",  "price": 8.50,  "description": "New York style, berry coulis"},
    {"name": "Chocolate Lava Cake",  "category": "Desserts",  "price": 10.00, "description": "Warm, vanilla ice cream"},
    {"name": "Ice Cream (3 Scoops)", "category": "Desserts",  "price": 7.00,  "description": "Vanilla, chocolate, strawberry"},
]

TABLES = [
    (1,2),(2,2),(3,4),(4,4),(5,4),(6,4),(7,6),(8,6),(9,8),(10,8)
]


# =============================================================================
# HELPERS
# =============================================================================

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _try_create(label, fn_call):
    """Call fn_call(), print result. Returns value or None on duplicate."""
    try:
        result = fn_call()
        print(f"  [create] {label}")
        return result
    except ValueError as e:
        if "already exists" in str(e):
            print(f"  [skip]   {label}")
            return None
        raise


# =============================================================================
# SEED SECTIONS
# =============================================================================

def seed_staff() -> tuple:
    """Create all test staff accounts. Returns (admin_id, [waiter_ids])."""
    print("\n[Staff]")
    admin_id, waiter_ids = None, []
    for s in STAFF:
        r = _try_create(
            f"{s['role']}: {s['username']} / {s['password']}",
            lambda s=s: fn.create_user(s["username"], s["password"], s["role"])
        )
        if r:
            if s["role"] == "admin":
                admin_id = r["id"]
            elif s["role"] == "waiter":
                waiter_ids.append(r["id"])
    # Resolve if already existed
    if not admin_id:
        u = fn.get_user_by_username("admin")
        admin_id = u["id"] if u else None
    if not waiter_ids:
        for s in STAFF:
            if s["role"] == "waiter":
                u = fn.get_user_by_username(s["username"])
                if u: waiter_ids.append(u["id"])
    return admin_id, waiter_ids


def seed_tables(admin_id: int) -> list:
    """Create 10 tables. Returns list of table IDs."""
    print("\n[Tables]")
    for num, cap in TABLES:
        _try_create(f"Table {num} (cap {cap})",
                    lambda num=num, cap=cap: fn.create_table(num, cap, admin_id))
    ids = [t["id"] for t in fn.get_all_tables()]
    print(f"  Tables ready: {len(ids)}")
    return ids


def seed_menu(admin_id: int) -> list:
    """Create all menu items. Returns list of item IDs."""
    print("\n[Menu]")
    for item in MENU:
        _try_create(
            f"[{item['category']}] {item['name']} ${item['price']}",
            lambda i=item: fn.create_menu_item(
                i["name"], i["description"], i["price"], i["category"], admin_id)
        )
    ids = [i["id"] for i in fn.get_menu_items(available_only=False)]
    print(f"  Menu items ready: {len(ids)}")
    return ids


def seed_active_sessions(waiter_ids: list, table_ids: list, item_ids: list):
    """Create 6 live sessions with orders spread across all status stages."""
    print("\n[Active Sessions]")
    stages = ["received", "received", "preparing", "preparing", "ready", "served"]
    db = _db()
    try:
        for i, table_id in enumerate(table_ids[:6]):
            existing = fn.get_active_session(table_id)
            if existing:
                print(f"  [skip]   table {table_id} already has active session")
                continue
            waiter_id = random.choice(waiter_ids)
            s = TableSession(table_id=table_id, opened_by=waiter_id,
                             opened_at=_now() - timedelta(minutes=random.randint(10, 60)))
            db.add(s)
            db.flush()

            status = stages[i % len(stages)]
            o = Order(session_id=s.id, table_id=table_id, waiter_id=waiter_id,
                      status=status, updated_at=_now())
            db.add(o)
            db.flush()

            chosen = random.sample(item_ids, min(random.randint(2, 5), len(item_ids)))
            for mid in chosen:
                mi = db.query(MenuItem).filter(MenuItem.id == mid).first()
                db.add(OrderItem(order_id=o.id, menu_item_id=mid,
                                 item_name=mi.name, price_at_time=mi.price,
                                 quantity=random.randint(1, 3)))
            print(f"  [create] active session for table {table_id} ({status})")

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def seed_history(waiter_ids: list, table_ids: list, item_ids: list, days: int = 30):
    """Insert 3-8 completed sessions per day for the past N days."""
    print("\n[Historical Orders]")
    db = _db()
    session_count = 0
    try:
        for day_offset in range(days, 0, -1):
            base = _now() - timedelta(days=day_offset)
            for _ in range(random.randint(3, 8)):
                table_id  = random.choice(table_ids)
                waiter_id = random.choice(waiter_ids)
                opened    = base.replace(hour=random.randint(11, 21),
                                         minute=random.randint(0, 59),
                                         second=0, microsecond=0)
                s = TableSession(table_id=table_id, opened_by=waiter_id,
                                 opened_at=opened, is_active=False,
                                 closed_at=opened + timedelta(hours=random.randint(1, 2)))
                db.add(s)
                db.flush()
                for _ in range(random.randint(1, 3)):
                    o = Order(session_id=s.id, table_id=table_id,
                              waiter_id=waiter_id, status="served",
                              created_at=opened, updated_at=opened)
                    db.add(o)
                    db.flush()
                    chosen = random.sample(item_ids, min(random.randint(1, 5), len(item_ids)))
                    for mid in chosen:
                        mi = db.query(MenuItem).filter(MenuItem.id == mid).first()
                        db.add(OrderItem(order_id=o.id, menu_item_id=mid,
                                         item_name=mi.name, price_at_time=mi.price,
                                         quantity=random.randint(1, 3)))
                session_count += 1
        db.commit()
        print(f"  Historical sessions created: {session_count} ({days} days)")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# =============================================================================
# MAIN
# =============================================================================

def main():
    print()
    print("=" * 55)
    print("  Restaurant System - Seed Data")
    print("=" * 55)

    fn.init_db()

    admin_id, waiter_ids = seed_staff()
    table_ids = seed_tables(admin_id)
    item_ids  = seed_menu(admin_id)

    seed_active_sessions(waiter_ids, table_ids, item_ids)
    seed_history(waiter_ids, table_ids, item_ids, days=30)

    print()
    print("=" * 55)
    print("  Seed complete. Login credentials:")
    print()
    for s in STAFF:
        print(f"  {s['role']:<10} {s['username']:<14} {s['password']}")
    print()
    print("  uvicorn main:app --host 0.0.0.0 --port 8000 --reload")
    print("=" * 55)
    print()


if __name__ == "__main__":
    main()
