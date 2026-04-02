# Restaurant Order Management System

## Structure

```
restaurant/
  functions.py        # All business logic and database functions
  main.py             # FastAPI endpoints and WebSocket hub
  requirements.txt    # Python dependencies
  .env.example        # Environment variable template
  static/
    shared.css        # Shared styles
    login.html        # Staff login page
    waiter.html       # Waiter order interface
    kitchen.html      # Kitchen board (Kanban-style)
    admin.html        # Admin dashboard
```

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your admin credentials and secret key

# 3. Run
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Roles and URLs

| Role    | URL           | Access                              |
|---------|---------------|-------------------------------------|
| Admin   | /admin        | Full system, stats, billing, users  |
| Waiter  | /waiter       | Tables, orders, sessions            |
| Kitchen | /kitchen      | Kitchen board, status updates       |

## Key Flows

### Waiter Flow
1. Login -> redirected to /waiter
2. Tap a table -> session opens automatically
3. Tap menu items -> adds to cart
4. Send to Kitchen -> order created, kitchen notified via WebSocket
5. Monitor order status in session strip

### Kitchen Flow
1. Login -> /kitchen Kanban board
2. New orders appear in "Received" column with alert sound
3. Tap "Start Preparing" -> moves to Preparing
4. Tap "Mark Ready" -> moves to Ready (waiter notified)

### Admin Flow
1. Login -> /admin dashboard
2. Statistics and earnings (admin-only)
3. Menu management (add, toggle items)
4. Create staff accounts
5. View bill and download PDF invoice
6. Close session after payment

## API Endpoints

### Auth
- POST /auth/login
- GET  /auth/me

### Users (admin only)
- POST   /users
- GET    /users
- DELETE /users/{id}

### Menu
- GET    /menu
- POST   /menu              (admin)
- PATCH  /menu/{id}         (admin)
- PATCH  /menu/{id}/toggle  (admin)

### Tables
- GET  /tables
- POST /tables  (admin)

### Sessions
- POST   /sessions
- GET    /sessions/{id}
- DELETE /sessions/{id}  (admin)

### Orders
- POST   /orders
- GET    /orders/kitchen
- GET    /orders/{id}
- PATCH  /orders/{id}/status
- DELETE /orders/{id}

### Billing (admin only)
- GET /bill/{session_id}
- GET /invoice/{session_id}

### Analytics (admin only)
- GET /stats
- GET /earnings

### Logs (admin only)
- GET /logs

### WebSocket
- WS /ws/{role}?token=...

## Production Notes

- Replace SQLite with PostgreSQL by updating DATABASE_PATH to a DSN
- Set SECRET_KEY to a cryptographically random 256-bit value
- Run behind HTTPS reverse proxy (nginx, Caddy)
- Set TOKEN_EXPIRY_HOURS appropriately for shift length
