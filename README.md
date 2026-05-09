# Eat-Hub Restaurant Order Management System

A real-time restaurant management application built with FastAPI that streamlines restaurant operations for Admin, Waiter, and Kitchen staff. The system provides role-based dashboards, live order tracking, kitchen workflow management, billing, analytics, and WebSocket-powered real-time updates.

---

# Features

## Admin Panel (`/admin`)

* Manage menu items and availability
* Create and manage staff accounts
* View restaurant analytics and earnings
* Access billing and invoice generation
* Monitor logs and restaurant activity
* Close customer sessions after payment

## Waiter Panel (`/waiter`)

* Manage restaurant tables and active sessions
* Add menu items to customer carts
* Send orders directly to the kitchen
* Track live order status updates
* Simplified touch-friendly interface

## Kitchen Panel (`/kitchen`)

* Real-time Kanban-style kitchen board
* Receive instant order notifications
* Update preparation status:

  * Received
  * Preparing
  * Ready
* Notify waiters automatically using WebSockets

---

# Project Structure

```text
restaurant/
│
├── functions.py        # Business logic and database functions
├── main.py             # FastAPI routes and WebSocket hub
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variable template
│
├── static/
│   ├── shared.css      # Shared styles
│   ├── login.html      # Staff login page
│   ├── waiter.html     # Waiter dashboard
│   ├── kitchen.html    # Kitchen Kanban board
│   └── admin.html      # Admin dashboard
```

---

# Installation

## 1. Clone the Repository

```bash
git clone <repository-url>
cd restaurant
```

## 2. Install Dependencies

```bash
pip install -r requirements.txt
```

## 3. Configure Environment Variables

```bash
cp .env.example .env
```

Edit the `.env` file and configure:

```env
SECRET_KEY=your_secret_key
ADMIN_USERNAME=admin
ADMIN_PASSWORD=password
DATABASE_PATH=restaurant.db
TOKEN_EXPIRY_HOURS=12
```

---

# Run the Application

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Application URLs:

| Role    | URL        |
| ------- | ---------- |
| Admin   | `/admin`   |
| Waiter  | `/waiter`  |
| Kitchen | `/kitchen` |

---

# Workflow

## Waiter Workflow

1. Login to the waiter dashboard
2. Select a table to open a session
3. Add menu items to the cart
4. Send order to the kitchen
5. Monitor live order status updates

## Kitchen Workflow

1. Login to the kitchen dashboard
2. View incoming orders in the "Received" column
3. Move orders to:

   * Preparing
   * Ready
4. Waiters receive instant status updates

## Admin Workflow

1. Login to the admin dashboard
2. Manage menu items and staff
3. Monitor earnings and analytics
4. Generate invoices and bills
5. Close completed sessions

---

# API Endpoints

## Authentication

```http
POST /auth/login
GET  /auth/me
```

## Users (Admin Only)

```http
POST   /users
GET    /users
DELETE /users/{id}
```

## Menu

```http
GET   /menu
POST  /menu
PATCH /menu/{id}
PATCH /menu/{id}/toggle
```

## Tables

```http
GET  /tables
POST /tables
```

## Sessions

```http
POST   /sessions
GET    /sessions/{id}
DELETE /sessions/{id}
```

## Orders

```http
POST   /orders
GET    /orders/kitchen
GET    /orders/{id}
PATCH  /orders/{id}/status
DELETE /orders/{id}
```

## Billing

```http
GET /bill/{session_id}
GET /invoice/{session_id}
```

## Analytics

```http
GET /stats
GET /earnings
```

## Logs

```http
GET /logs
```

---

# WebSocket Support

Real-time updates are powered through WebSockets.

```http
WS /ws/{role}?token=...
```

Used for:

* Instant kitchen notifications
* Live order tracking
* Real-time dashboard updates

---

# Production Deployment Notes

* Replace SQLite with PostgreSQL for production environments
* Configure a strong cryptographic `SECRET_KEY`
* Run behind an HTTPS reverse proxy such as:

  * Nginx
  * Caddy
* Configure appropriate token expiry durations
* Use process managers like:

  * Gunicorn
  * Supervisor
  * Docker

---

# Tech Stack

* Python
* FastAPI
* WebSockets
* SQLite / PostgreSQL
* HTML, CSS, JavaScript
* REST API Architecture

---

# Highlights

* Real-time order management
* Role-based dashboards
* Kitchen Kanban system
* Secure authentication
* Billing and invoice generation
* Analytics and earnings tracking
* Lightweight and production-ready architecture
