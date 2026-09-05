import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from database import (
    load_db, save_db, get_db,
    get_config, update_config, update_upi_id, update_upi_and_holder, set_month,
    get_month_schedule, update_month_holder, swap_month_holders, reset_month_schedule, DEFAULT_ROTATION_ORDER,
    get_members_list, get_base_payments, set_member_payment, add_member_if_missing, set_all_members,
    get_all_expenses, add_expense_record, delete_expense_record,
    get_all_deposits, add_deposit_record, delete_deposit_record,
    get_user_passwords, save_user_password,
    get_user_upi, save_user_upi, get_all_user_upis,
    delete_member_record, admin_reset_user_password,
    reset_all_expenses, reset_all_deposits, factory_reset_all_data, get_detailed_users_list
)
from models import (
    SetupData, PaymentData, ExpenseData, TopUpData, LoginData, ChangePasswordData,
    CreateUserData, AdminCreateUserData, AdminResetPasswordData, SetMonthData, UserUpiData
)
from calculator import calculate_summary
from auth import DEFAULT_PASSWORD, hash_password, verify_password, create_jwt_token, decode_jwt_token

app = FastAPI(title="RoomMate Finance Tracker")

# Mount static files directory
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/admin")
def serve_admin():
    return FileResponse("static/admin.html")


def get_token_from_request(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token.strip()
    return None


def require_admin(request: Request) -> str:
    token = get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    payload = decode_jwt_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    sub = payload.get("sub", "").lower().strip()
    data = load_db()
    leader = (data.get("leader") or "").lower().strip()
    if sub != "admin" and sub != leader:
        raise HTTPException(status_code=403, detail="Administrative privileges required. Only Admin or Account Holder can access this.")
    return sub


@app.get("/")
def serve_frontend():
    return FileResponse("static/index.html")


@app.post("/api/login")
def login(login_data: LoginData, response: Response):
    data = load_db()
    username = login_data.username.strip().lower()
    password = login_data.password.strip()

    members = data.get("members", [])
    members_lower = [m.lower() for m in members]
    valid_usernames = set(members_lower) | {"admin", "room", (data.get("leader") or "").lower()}

    if username not in valid_usernames:
        raise HTTPException(status_code=401, detail="Roommate not found in this passbook.")

    passwords = data.get("passwords", {})
    if username in passwords:
        if not verify_password(passwords[username], password):
            raise HTTPException(status_code=401, detail="Incorrect password.")
    else:
        if password != DEFAULT_PASSWORD:
            raise HTTPException(status_code=401, detail="Incorrect password.")

    token = create_jwt_token(username, expires_days=30)
    response.set_cookie(
        key="access_token",
        value=token,
        max_age=30 * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/"
    )

    display_name = username
    for m in members:
        if m.lower() == username:
            display_name = m
            break

    user_upi = get_user_upi(username)

    return {
        "status": "success",
        "token": token,
        "username": display_name,
        "user_upi": user_upi,
        "has_upi": bool(user_upi and "@" in user_upi)
    }


@app.get("/api/me")
def get_current_session(request: Request):
    token = get_token_from_request(request)
    if not token:
        return {"authenticated": False}
    payload = decode_jwt_token(token)
    if not payload:
        return {"authenticated": False}

    data = load_db()
    sub = payload.get("sub", "")
    members = data.get("members", [])
    display_name = sub
    for m in members:
        if m.lower() == sub:
            display_name = m
            break

    leader = (data.get("leader") or "").lower()
    is_admin = (sub.lower() == "admin" or sub.lower() == leader)
    user_upi = get_user_upi(sub)

    return {
        "authenticated": True,
        "username": display_name,
        "members": members,
        "leader": data.get("leader", ""),
        "is_admin": is_admin,
        "has_custom_password": sub in data.get("passwords", {}),
        "user_upi": user_upi,
        "has_upi": bool(user_upi and (len(user_upi.strip()) >= 10 or "@" in user_upi))
    }


@app.post("/api/user/upi")
def save_personal_upi(payload: UserUpiData, request: Request):
    token = get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    token_payload = decode_jwt_token(token)
    if not token_payload:
        raise HTTPException(status_code=401, detail="Invalid session.")

    sub = token_payload.get("sub", "").strip().lower()
    clean_upi = payload.upi_id.strip().lower()

    if clean_upi:
        import re
        is_phone = bool(re.match(r"^[6-9]\d{9}$", clean_upi) or re.match(r"^\d{10}$", clean_upi))
        is_upi = bool("@" in clean_upi and len(clean_upi.split("@")) == 2 and clean_upi.split("@")[1])
        if not (is_phone or is_upi):
            raise HTTPException(
                status_code=400,
                detail="Please enter a valid 10-digit mobile number (e.g., 7732087737) or UPI handle."
            )

    saved_upi = save_user_upi(sub, clean_upi)
    return {
        "status": "success",
        "username": sub,
        "upi_id": saved_upi,
        "message": "Payment mobile number / UPI credentials saved successfully."
    }


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(key="access_token", path="/")
    return {"status": "success"}


@app.post("/api/change_password")
def change_password(payload: ChangePasswordData, request: Request):
    username = payload.username.strip().lower()
    old_pw = payload.old_password.strip()
    new_pw = payload.new_password.strip()

    if len(new_pw) < 4:
        raise HTTPException(status_code=400, detail="New password must be at least 4 characters long.")

    passwords = get_user_passwords()
    if username in passwords:
        if not verify_password(passwords[username], old_pw):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")
    else:
        if old_pw != DEFAULT_PASSWORD:
            raise HTTPException(status_code=400, detail="Current password is incorrect.")

    save_user_password(username, hash_password(new_pw))
    return {"status": "success", "message": "Password updated successfully."}


@app.get("/api/public_info")
def get_public_info():
    cfg = get_config()
    return {
        "month": cfg.get("month", "September"),
        "leader": cfg.get("leader", "")
    }


@app.post("/api/create_user")
def create_user(payload: CreateUserData, request: Request, response: Response):
    require_admin(request)
    username = payload.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username cannot be empty.")

    password = (payload.password or "").strip() or DEFAULT_PASSWORD
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters long.")

    add_member_if_missing(username, 0.0)
    save_user_password(username, hash_password(password))

    # Check if a user is already authenticated
    existing_token = get_token_from_request(request)
    existing_payload = decode_jwt_token(existing_token) if existing_token else None

    token = None
    if not existing_payload:
        token = create_jwt_token(username, expires_days=30)
        response.set_cookie(
            key="access_token",
            value=token,
            max_age=30 * 24 * 3600,
            httponly=True,
            samesite="lax",
            secure=False,
            path="/"
        )

    return {
        "status": "success",
        "token": token,
        "username": username,
        "message": f"Roommate {username} account created successfully!"
    }


@app.get("/api/data")
def get_data():
    data = load_db()
    data["summary"] = calculate_summary(data)
    return data


VALID_CALENDAR_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]


@app.post("/api/set_month")
def set_active_month(payload: SetMonthData):
    raw_m = (payload.month or "").strip()
    if payload.auto_calendar or raw_m.lower() in ["auto", "current"]:
        current_cal_month = datetime.now().strftime("%B")
        set_month(current_cal_month, auto_calendar=True)
        return {"status": "success", "month": current_cal_month, "auto_calendar": True}

    matched = next((vm for vm in VALID_CALENDAR_MONTHS if vm.lower() == raw_m.lower()), None)
    if not matched:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid month: '{payload.month}'. Please choose from valid calendar months: {', '.join(VALID_CALENDAR_MONTHS)}."
        )

    set_month(matched, auto_calendar=False)
    return {"status": "success", "month": matched, "auto_calendar": False}


@app.post("/api/admin/update_config")
def admin_update_config(payload: dict, request: Request):
    require_admin(request)
    raw_month = payload.get("month", "").strip()
    matched_month = next((vm for vm in VALID_CALENDAR_MONTHS if vm.lower() == raw_month.lower()), datetime.now().strftime("%B"))
    leader = payload.get("leader", "yashwanth")
    base_amount = float(payload.get("base_amount", 1000.0))
    auto_cal = bool(payload.get("auto_calendar", False))
    upi_id = str(payload.get("upi_id", "")).strip().lower()

    # Validate payment handle or 10-digit mobile number
    if upi_id:
        import re
        is_phone = bool(re.match(r"^[6-9]\d{9}$", upi_id) or re.match(r"^\d{10}$", upi_id))
        is_upi = bool(re.match(r"^[a-zA-Z0-9.\-_]{2,64}@[a-zA-Z0-9]{2,32}$", upi_id))
        if not (is_phone or is_upi):
            raise HTTPException(
                status_code=400,
                detail="Please provide a valid 10-digit mobile number (e.g. 7732087737) or UPI ID."
            )

    update_config(matched_month, leader, base_amount, auto_cal, upi_id=upi_id)
    return {"status": "success", "message": "Passbook configuration updated successfully."}


@app.post("/api/update_upi")
def api_update_upi(payload: dict, request: Request):
    token = get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    token_payload = decode_jwt_token(token)
    if not token_payload:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    sub = token_payload.get("sub", "").lower().strip()
    data = load_db()
    current_leader = (data.get("leader") or "").lower().strip()
    if sub != "admin" and sub != current_leader:
        raise HTTPException(
            status_code=403,
            detail=f"Access restricted: Only the current account holder ({current_leader.title()}) can change the account holder or update room UPI credentials."
        )
    
    raw_upi = str(payload.get("upi_id", "")).strip().lower()
    if raw_upi:
        import re
        is_phone = bool(re.match(r"^[6-9]\d{9}$", raw_upi) or re.match(r"^\d{10}$", raw_upi))
        is_upi = bool(re.match(r"^[a-zA-Z0-9.\-_]{2,64}@[a-zA-Z0-9]{2,32}$", raw_upi))
        if not (is_phone or is_upi):
            raise HTTPException(
                status_code=400,
                detail="Please provide a valid 10-digit mobile number (e.g. 7732087737) or UPI ID."
            )
    new_leader = payload.get("leader")
    clean_leader = None
    if new_leader:
        members = get_members_list()
        matched = next((m for m in members if m.lower() == str(new_leader).strip().lower()), None)
        if matched:
            clean_leader = matched
        else:
            clean_leader = str(new_leader).strip().lower()

    clean_upi = update_upi_and_holder(raw_upi, clean_leader)
    return {
        "status": "success",
        "upi_id": clean_upi,
        "leader": clean_leader,
        "message": "UPI details and account holder updated successfully."
    }


@app.get("/api/holder_schedule")
def api_get_holder_schedule(request: Request):
    token = get_token_from_request(request)
    sub = ""
    if token:
        payload = decode_jwt_token(token)
        if payload:
            sub = payload.get("sub", "").lower().strip()
    data = load_db()
    current_leader = (data.get("leader") or "").lower().strip()
    is_admin = (sub == "admin")
    sched = get_month_schedule()
    return {
        "status": "success",
        "rotation_order": DEFAULT_ROTATION_ORDER,
        "schedule": sched,
        "current_month": data.get("month", "September"),
        "current_leader": current_leader,
        "can_manage": is_admin
    }


@app.post("/api/holder_schedule/set")
def api_set_month_holder(payload: dict, request: Request):
    caller = require_admin(request)
    month = str(payload.get("month", "")).strip()
    holder = str(payload.get("holder", "")).strip().lower()
    if month not in VALID_CALENDAR_MONTHS:
        raise HTTPException(status_code=400, detail=f"Invalid month '{month}'.")
    members = get_members_list()
    if holder not in [m.lower() for m in members]:
        raise HTTPException(status_code=400, detail=f"Unknown roommate '{holder}'.")
    sched = update_month_holder(month, holder)
    return {"status": "success", "schedule": sched, "message": f"{holder.title()} assigned as holder for {month}."}


@app.post("/api/holder_schedule/swap")
def api_swap_month_holders(payload: dict, request: Request):
    caller = require_admin(request)
    month_a = str(payload.get("month_a", "")).strip()
    month_b = str(payload.get("month_b", "")).strip()
    if month_a not in VALID_CALENDAR_MONTHS or month_b not in VALID_CALENDAR_MONTHS:
        raise HTTPException(status_code=400, detail="Invalid month(s) specified.")
    if month_a == month_b:
        raise HTTPException(status_code=400, detail="Please select two different months to swap.")
    sched = swap_month_holders(month_a, month_b)
    return {"status": "success", "schedule": sched, "message": f"Successfully swapped account holders for {month_a} and {month_b}."}


@app.post("/api/holder_schedule/reset")
def api_reset_holder_schedule(request: Request):
    caller = require_admin(request)
    sched = reset_month_schedule()
    return {"status": "success", "schedule": sched, "message": "Reset to standard rotation schedule: Madhu → Satya → Siva → Manohar → Yashwanth."}




@app.post("/api/setup")
def setup_month(setup: SetupData):
    update_config(setup.month, setup.leader, setup.base_amount)
    set_all_members(setup.members, setup.base_amount)
    db = get_db()
    db["expenses"].delete_many({})
    db["pool_deposits"].delete_many({})
    return {"status": "success"}


def check_can_manage_deposit_for_member(request: Request, target_member: str) -> str:
    """Verify that the caller is Admin, Account Holder (Leader), or the target member themselves."""
    token = get_token_from_request(request)
    if not token:
        return target_member
    payload = decode_jwt_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    sub = payload.get("sub", "").lower().strip()
    cfg = get_config()
    leader = (cfg.get("leader") or "").lower().strip()
    target_clean = target_member.lower().strip()
    if sub != "admin" and sub != leader and sub != target_clean:
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied: You are signed in as '{sub}'. Only the account holder ({leader}) can record deposits for other roommates."
        )
    return sub


def require_leader_or_admin(request: Request) -> str:
    token = get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    payload = decode_jwt_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    sub = payload.get("sub", "").lower().strip()
    cfg = get_config()
    leader = (cfg.get("leader") or "").lower().strip()
    if sub != "admin" and sub != leader:
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied: Only the account holder ({leader}) or admin can perform this operation."
        )
    return sub


@app.post("/api/pay_base")
def pay_base(payment: PaymentData, request: Request):
    m_clean = payment.member.lower().strip()
    check_can_manage_deposit_for_member(request, m_clean)
    members = get_members_list()
    if m_clean not in [m.lower() for m in members]:
        raise HTTPException(status_code=400, detail="Member not found")

    base_payments = get_base_payments()
    current = float(base_payments.get(m_clean, 0.0))
    new_amt = float(payment.amount)
    diff = new_amt - current

    set_member_payment(m_clean, new_amt)

    if diff > 0:
        add_deposit_record({
            "member": payment.member,
            "amount": round(diff, 2),
            "note": "Base Pool Deposit",
            "date": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
    return {"status": "success"}


@app.post("/api/pay_all_base")
def pay_all_base(request: Request):
    require_leader_or_admin(request)
    cfg = get_config()
    members = get_members_list()
    base_amt = float(cfg.get("base_amount", 1000.0))
    base_payments = get_base_payments()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    for m in members:
        current = float(base_payments.get(m, 0.0))
        if current < base_amt:
            diff = base_amt - current
            set_member_payment(m, base_amt)
            add_deposit_record({
                "member": m,
                "amount": round(diff, 2),
                "note": "Base Pool Deposit",
                "date": now_str
            })
    return {"status": "success"}


@app.post("/api/set_base_amount")
def set_base_amount(payload: dict, request: Request):
    require_leader_or_admin(request)
    amt = float(payload.get("amount", 1000.0))
    if amt <= 0:
        raise HTTPException(status_code=400, detail="Base amount must be greater than 0")
    cfg = get_config()
    update_config(cfg.get("month", "September"), cfg.get("leader", "yashwanth"), round(amt, 2))
    return {"status": "success", "base_amount": round(amt, 2)}


@app.post("/api/toggle_base")
def toggle_base(payment: PaymentData, request: Request):
    m_clean = payment.member.lower().strip()
    check_can_manage_deposit_for_member(request, m_clean)
    members = get_members_list()
    if m_clean not in [m.lower() for m in members]:
        raise HTTPException(status_code=400, detail="Member not found")
    base_payments = get_base_payments()
    if m_clean in base_payments and base_payments[m_clean] > 0:
        set_member_payment(m_clean, 0.0)
    else:
        set_member_payment(m_clean, payment.amount)
    return {"status": "success"}


@app.post("/api/topup")
def add_topup(topup: TopUpData, request: Request):
    m_clean = topup.member.lower().strip()
    check_can_manage_deposit_for_member(request, m_clean)
    members = get_members_list()
    if m_clean not in [m.lower() for m in members]:
        raise HTTPException(status_code=400, detail="Member not found")
    if topup.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than 0")
    if topup.amount > 100000:
        raise HTTPException(status_code=400, detail="Single deposit cannot exceed ₹1,00,000 (standard UPI transaction limit).")

    sanitized_note = (topup.note or "Pool Top-up").strip()[:60]

    new_deposit = add_deposit_record({
        "member": topup.member,
        "amount": round(float(topup.amount), 2),
        "note": sanitized_note,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M")
    })

    base_payments = get_base_payments()
    current = float(base_payments.get(m_clean, 0.0))
    set_member_payment(m_clean, round(current + float(topup.amount), 2))

    return {"status": "success", "deposit": new_deposit}


@app.delete("/api/topup/{topup_id}")
def delete_topup(topup_id: int, request: Request):
    target = delete_deposit_record(topup_id)
    if not target:
        raise HTTPException(status_code=404, detail="Top-up record not found")

    member = target.get("member", "").lower().strip()
    check_can_manage_deposit_for_member(request, member)

    amt = float(target.get("amount", 0.0))
    base_payments = get_base_payments()
    if member in base_payments:
        new_bal = max(0.0, round(base_payments[member] - amt, 2))
        set_member_payment(member, new_bal)

    return {"status": "success"}


@app.post("/api/set_member_deposit")
def set_member_deposit(payment: PaymentData, request: Request):
    m_clean = payment.member.lower().strip()
    check_can_manage_deposit_for_member(request, m_clean)
    members = get_members_list()
    if m_clean not in [m.lower() for m in members]:
        raise HTTPException(status_code=400, detail="Member not found")
    set_member_payment(m_clean, max(0.0, round(float(payment.amount), 2)))
    return {"status": "success"}


@app.post("/api/expense")
def add_expense(expense: ExpenseData):
    all_members = get_members_list()
    split_mode = expense.split_mode or "equal"
    custom_splits = expense.custom_splits or {}

    if split_mode == "unequal":
        if not custom_splits:
            raise HTTPException(status_code=400, detail="Custom split amounts must be provided for unequal split.")

        cleaned_splits = {}
        for m, amt in custom_splits.items():
            if m in all_members and float(amt) > 0:
                cleaned_splits[m] = round(float(amt), 2)

        if not cleaned_splits:
            raise HTTPException(status_code=400, detail="At least one roommate must have a split amount greater than 0.")

        total_custom = round(sum(cleaned_splits.values()), 2)
        exp_amount = round(float(expense.amount), 2)
        diff = round(abs(total_custom - exp_amount), 2)

        if diff > 0.05:
            raise HTTPException(
                status_code=400,
                detail=f"Unequal split total (₹{total_custom:,.2f}) does not match bill total (₹{exp_amount:,.2f}). Difference: ₹{diff:,.2f}. Please balance the shares."
            )
        target_split = list(cleaned_splits.keys())
        stored_splits = cleaned_splits
    else:
        target_split = expense.split_between if (expense.split_between and len(expense.split_between) > 0) else all_members
        stored_splits = None

    new_expense = add_expense_record({
        "payer": expense.payer,
        "desc": expense.desc,
        "amount": round(float(expense.amount), 2),
        "split_mode": split_mode,
        "split_between": target_split,
        "custom_splits": stored_splits,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M")
    })
    return {"status": "success", "expense": new_expense}


@app.delete("/api/expense/{expense_id}")
def delete_expense(expense_id: int):
    delete_expense_record(expense_id)
    return {"status": "success"}


# ==========================================
# ADMIN ENDPOINTS
# ==========================================



@app.get("/api/admin/users")
def admin_get_users(request: Request):
    require_admin(request)
    return {
        "users": get_detailed_users_list(),
        "config": get_config(),
        "default_password": DEFAULT_PASSWORD
    }


@app.post("/api/admin/create_user")
def admin_create_user(payload: AdminCreateUserData, request: Request):
    require_admin(request)
    username = payload.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username cannot be empty.")

    password = (payload.password or "").strip() or DEFAULT_PASSWORD
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters long.")

    init_dep = float(payload.initial_deposit or 0.0)
    add_member_if_missing(username, init_dep)
    save_user_password(username, hash_password(password))

    if init_dep > 0:
        add_deposit_record({
            "member": username,
            "amount": round(init_dep, 2),
            "note": "Initial Admin Deposit",
            "date": datetime.now().strftime("%Y-%m-%d %H:%M")
        })

    return {"status": "success", "message": f"Roommate '{username}' successfully registered with initial deposit ₹{init_dep:,.2f}!"}


@app.delete("/api/admin/delete_user/{username}")
def admin_delete_user(username: str, request: Request):
    admin_user = require_admin(request)
    u_clean = username.strip().lower()
    if u_clean == "admin":
        raise HTTPException(status_code=400, detail="The root admin account cannot be deleted.")
    if u_clean == admin_user:
        raise HTTPException(status_code=400, detail="You cannot delete your own currently logged-in account.")

    deleted = delete_member_record(u_clean)
    if not deleted:
        raise HTTPException(status_code=404, detail="User record not found.")
    return {"status": "success", "message": f"User '{u_clean}' has been completely removed from the passbook."}


@app.post("/api/admin/reset_password")
def admin_reset_password(payload: AdminResetPasswordData, request: Request):
    require_admin(request)
    u_clean = payload.username.strip().lower()
    new_pw = payload.new_password.strip()
    if len(new_pw) < 4:
        raise HTTPException(status_code=400, detail="New password must be at least 4 characters long.")

    admin_reset_user_password(u_clean, hash_password(new_pw))
    return {"status": "success", "message": f"Password for '{u_clean}' has been updated to '{new_pw}'."}


@app.post("/api/admin/reset_expenses")
def admin_reset_expenses_endpoint(request: Request):
    require_admin(request)
    count = reset_all_expenses()
    return {"status": "success", "message": f"Wiped {count} expense entries from the ledger."}


@app.post("/api/admin/reset_deposits")
def admin_reset_deposits_endpoint(request: Request):
    require_admin(request)
    count = reset_all_deposits()
    return {"status": "success", "message": f"Wiped {count} deposit records and reset all member base payments to 0."}


@app.post("/api/admin/factory_reset")
def admin_factory_reset_endpoint(request: Request):
    require_admin(request)
    factory_reset_all_data()
    return {"status": "success", "message": "Complete Passbook Factory Reset performed. Default members and clean ledger restored."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=9002, reload=True)
