# 💸 RoomMate Finance Tracker & Debt Splitter

A modern, fast, and easy-to-use web application built with **FastAPI**, **Pydantic**, **Alpine.js**, and **Tailwind CSS** to track monthly room expenses, base pool contributions, and compute optimal net debt settlements among roommates.

---

## 📁 Project Structure

```text
newm/
├── main.py              # FastAPI server, static file mounting & API endpoints
├── calculator.py        # Core debt solver algorithm & fair-share math
├── database.py          # MongoDB Atlas database layer with separate collections
├── auth.py              # HS256 JWT auth & salted password security
├── models.py            # Pydantic data schemas for request validation
├── requirements.txt     # Python dependencies (FastAPI, uvicorn, pymongo, dnspython)
├── README.md            # Project documentation & usage guide
└── static/
    ├── index.html       # Roommate Passbook Portal (HTML, Tailwind, Alpine.js)
    └── admin.html       # Dedicated Administration Console (/admin)
```

---

## 🚀 Getting Started

### 1. Install Dependencies

Ensure Python 3.8+ is installed, then run:

```bash
pip install -r requirements.txt
```

### 2. Start the Server

Run the Uvicorn development server:

```bash
uvicorn main:app --port 9002 --reload
```

### 3. Access Portals

- **Roommate Passbook Portal:** 👉 **[http://127.0.0.1:9002](http://127.0.0.1:9002)**
  - Enter roommate username (or tap quick selector) and password (`room@123` by default).
  - Clean view for recording expenses, tracking room pool balance, and viewing settlements.
  - No user registration or admin destruction actions exposed.

- **Dedicated Admin Console:** 👉 **[http://127.0.0.1:9002/admin](http://127.0.0.1:9002/admin)**
  - Access restricted to `admin` or room `leader` (Account Holder).
  - **User Management:** Enroll new roommates, delete roommates, and reset user passwords.
  - **Passbook Configuration:** Change month, designated leader, and base deposit targets.
  - **Danger Zone / Reset Operations:**
    - Wipe expense entries only
    - Reset room pool deposits back to ₹0
    - Complete Factory Reset (restores default members & clean ledger)

---

## ✨ Key Features

- **MongoDB Atlas Integration:** Persistent storage across separate collections (`config`, `members`, `expenses`, `pool_deposits`, `users`).
- **Dedicated Admin Governance (`/admin`):** Complete centralized user management and database purge controls away from the everyday roommate portal.
- **Secure Authentication:** HS256 JWT sessions stored in 30-day HTTP-only cookies and local storage tokens.
- **Monthly Base Pool Tracking:** Track who paid their monthly fixed room contribution (e.g. ₹1,000 to the Room Leader).
- **Out-of-Pocket Expense Logging:** Record individual expenses (groceries, WiFi, electricity) paid by any roommate or directly deducted from the room account.
- **Fair-Share Calculation:** Automatically computes the fair share of total room expenses per roommate.
- **Greedy Debt Settlement Engine:** Calculates net balances and generates the minimum number of transactions needed to settle up ("Who Owes Whom?").
- **Vintage Paper Ledger Passbook Design:** Premium editorial feel with IBM Plex Mono numbers, Zilla Slab typography, stamp badges, and ink accents.
