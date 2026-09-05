import os
import json
from datetime import datetime
from typing import Dict, Any, List, Optional

MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb+srv://guideray:bximVAjvmAubFQKi@guideray.rh3tjqr.mongodb.net/GuideRay?retryWrites=true&w=majority"
)
DB_NAME = "GuideRay"
CONFIG_DOC_ID = "room_config"

DEFAULT_MEMBERS = ["yashwanth", "madhu", "siva", "satya", "manohar"]
DEFAULT_CONFIG = {
    "_id": CONFIG_DOC_ID,
    "month": "September",
    "leader": "yashwanth",
    "base_amount": 1000.0,
    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
}

_client = None
_db = None


def get_db():
    """Get or initialize the MongoDB database instance."""
    global _client, _db
    if _db is not None:
        return _db

    try:
        from pymongo import MongoClient
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
        _db = _client[DB_NAME]
        # Run one-time migration if needed
        _init_db_and_migrate(_db)
        return _db
    except ImportError:
        raise RuntimeError(
            "pymongo is required to connect to MongoDB. Please run: pip install \"pymongo[srv]\" or uv add pymongo dnspython"
        )
    except Exception as e:
        print(f"[MongoDB Warning] Connection error: {e}")
        # Try returning the client handle regardless
        if _client:
            _db = _client[DB_NAME]
            return _db
        raise e


def _clean_doc(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Remove MongoDB's _id ObjectId from returning dicts if needed."""
    if not doc:
        return None
    doc = dict(doc)
    if "_id" in doc and not isinstance(doc["_id"], str):
        doc["_id"] = str(doc["_id"])
    return doc


def _clean_docs(cursor) -> List[Dict[str, Any]]:
    """Clean a list of MongoDB documents."""
    result = []
    for doc in cursor:
        d = dict(doc)
        if "_id" in d:
            del d["_id"]
        result.append(d)
    return result


def _init_db_and_migrate(db):
    """
    Check if database needs initial setup or migration from legacy data.json.
    Splits data into separate collections:
    - config: month, leader, base_amount
    - members: name, base_payment
    - expenses: expense transactions
    - pool_deposits: deposits into the room fund
    - users: usernames and password hashes
    """
    config_col = db["config"]
    members_col = db["members"]
    expenses_col = db["expenses"]
    deposits_col = db["pool_deposits"]
    users_col = db["users"]

    # 1. Check if legacy data.json exists to migrate
    legacy_file = "data.json"
    if os.path.exists(legacy_file):
        try:
            with open(legacy_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict) and data:
                # Migrate config
                config_col.update_one(
                    {"_id": CONFIG_DOC_ID},
                    {"$set": {
                        "month": data.get("month", "September"),
                        "leader": data.get("leader", "yashwanth"),
                        "base_amount": float(data.get("base_amount", 1000.0)),
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
                    }},
                    upsert=True
                )

                # Migrate members & base_payments
                members = data.get("members", DEFAULT_MEMBERS)
                base_payments = data.get("base_payments", {})
                for m in members:
                    m_clean = str(m).strip().lower()
                    members_col.update_one(
                        {"name": m_clean},
                        {"$set": {
                            "name": m_clean,
                            "base_payment": float(base_payments.get(m, base_payments.get(m_clean, 0.0)))
                        }},
                        upsert=True
                    )

                # Migrate expenses
                if expenses_col.count_documents({}) == 0 and "expenses" in data:
                    for exp in data["expenses"]:
                        exp_copy = dict(exp)
                        expenses_col.update_one(
                            {"id": exp_copy.get("id")},
                            {"$set": exp_copy},
                            upsert=True
                        )

                # Migrate pool deposits
                if deposits_col.count_documents({}) == 0 and "pool_deposits" in data:
                    for dep in data["pool_deposits"]:
                        dep_copy = dict(dep)
                        deposits_col.update_one(
                            {"id": dep_copy.get("id")},
                            {"$set": dep_copy},
                            upsert=True
                        )

                # Migrate passwords / users
                if "passwords" in data:
                    for username, pw_hash in data["passwords"].items():
                        users_col.update_one(
                            {"username": username.lower().strip()},
                            {"$set": {
                                "username": username.lower().strip(),
                                "password_hash": pw_hash,
                                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
                            }},
                            upsert=True
                        )

                print("[MongoDB] Successfully migrated data.json into MongoDB separate collections!")
            
            # Remove data.json after successful migration as requested
            try:
                # Keep a temporary backup file just in case before removing
                with open(".data.json.bak", "w", encoding="utf-8") as bf:
                    json.dump(data, bf, indent=2)
                os.remove(legacy_file)
                print("[MongoDB] Removed local data.json file.")
            except Exception as rem_err:
                print(f"[MongoDB] Could not remove data.json: {rem_err}")

        except Exception as e:
            print(f"[MongoDB Migration Error]: {e}")

    # 2. Ensure initial config exists if empty
    if not config_col.find_one({"_id": CONFIG_DOC_ID}):
        config_col.insert_one(DEFAULT_CONFIG)

    # 3. Ensure default members exist if collection is empty
    if members_col.count_documents({}) == 0:
        for m in DEFAULT_MEMBERS:
            members_col.insert_one({
                "name": m,
                "base_payment": 0.0
            })


# ==========================================
# SEPARATE COLLECTION ACCESSORS
# ==========================================

def get_config() -> Dict[str, Any]:
    db = get_db()
    cfg = db["config"].find_one({"_id": CONFIG_DOC_ID})
    if not cfg:
        cfg = DEFAULT_CONFIG.copy()
    
    # Auto-calendar sync: if auto_calendar is enabled (default True), automatically match the real-world calendar month
    auto_cal = cfg.get("auto_calendar", True)
    cur_cal_month = datetime.now().strftime("%B")  # e.g., "September"
    if auto_cal and cfg.get("month") != cur_cal_month:
        cfg["month"] = cur_cal_month
        cfg["auto_calendar"] = True
        db["config"].update_one(
            {"_id": CONFIG_DOC_ID},
            {"$set": {
                "month": cur_cal_month,
                "auto_calendar": True,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
            }},
            upsert=True
        )
    return cfg


def set_month(month: str, auto_calendar: bool = False):
    db = get_db()
    db["config"].update_one(
        {"_id": CONFIG_DOC_ID},
        {"$set": {
            "month": month,
            "auto_calendar": auto_calendar,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }},
        upsert=True
    )


def update_config(month: str, leader: str, base_amount: float, auto_calendar: bool = False):
    db = get_db()
    db["config"].update_one(
        {"_id": CONFIG_DOC_ID},
        {"$set": {
            "month": month,
            "leader": leader.lower().strip(),
            "base_amount": float(base_amount),
            "auto_calendar": auto_calendar,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }},
        upsert=True
    )


def get_members_list() -> List[str]:
    db = get_db()
    cursor = db["members"].find({})
    members = [doc.get("name") for doc in cursor if doc.get("name")]
    return members if members else DEFAULT_MEMBERS.copy()


def get_base_payments() -> Dict[str, float]:
    db = get_db()
    cursor = db["members"].find({})
    payments = {}
    for doc in cursor:
        name = doc.get("name")
        if name:
            payments[name] = float(doc.get("base_payment", 0.0))
    return payments


def set_member_payment(member: str, amount: float):
    db = get_db()
    m_clean = member.lower().strip()
    db["members"].update_one(
        {"name": m_clean},
        {"$set": {"name": m_clean, "base_payment": float(amount)}},
        upsert=True
    )


def add_member_if_missing(member: str, base_payment: float = 0.0):
    db = get_db()
    m_clean = member.lower().strip()
    db["members"].update_one(
        {"name": m_clean},
        {"$setOnInsert": {"name": m_clean, "base_payment": float(base_payment)}},
        upsert=True
    )


def set_all_members(members: List[str], base_amount: float = 1000.0):
    db = get_db()
    members_clean = [m.lower().strip() for m in members if m.strip()]
    
    # Remove members not in new list
    db["members"].delete_many({"name": {"$nin": members_clean}})
    
    # Upsert new members
    for m in members_clean:
        db["members"].update_one(
            {"name": m},
            {"$setOnInsert": {"name": m, "base_payment": 0.0}},
            upsert=True
        )


def get_all_expenses() -> List[Dict[str, Any]]:
    db = get_db()
    cursor = db["expenses"].find({}).sort("id", 1)
    return _clean_docs(cursor)


def add_expense_record(expense: Dict[str, Any]) -> Dict[str, Any]:
    db = get_db()
    col = db["expenses"]
    if "id" not in expense or not expense["id"]:
        last_exp = col.find_one(sort=[("id", -1)])
        expense["id"] = (last_exp["id"] + 1) if last_exp and "id" in last_exp else 1
    
    doc = dict(expense)
    col.insert_one(doc)
    if "_id" in doc:
        del doc["_id"]
    return doc


def delete_expense_record(expense_id: int):
    db = get_db()
    db["expenses"].delete_one({"id": expense_id})


def get_all_deposits() -> List[Dict[str, Any]]:
    db = get_db()
    cursor = db["pool_deposits"].find({}).sort("id", 1)
    return _clean_docs(cursor)


def add_deposit_record(deposit: Dict[str, Any]) -> Dict[str, Any]:
    db = get_db()
    col = db["pool_deposits"]
    if "id" not in deposit or not deposit["id"]:
        last_dep = col.find_one(sort=[("id", -1)])
        deposit["id"] = (last_dep["id"] + 1) if last_dep and "id" in last_dep else 1
    
    doc = dict(deposit)
    col.insert_one(doc)
    if "_id" in doc:
        del doc["_id"]
    return doc


def delete_deposit_record(deposit_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    target = db["pool_deposits"].find_one({"id": deposit_id})
    if target:
        db["pool_deposits"].delete_one({"id": deposit_id})
        return _clean_doc(target)
    return None


def get_user_passwords() -> Dict[str, str]:
    db = get_db()
    cursor = db["users"].find({})
    passwords = {}
    for doc in cursor:
        u = doc.get("username")
        p = doc.get("password_hash")
        if u and p:
            passwords[u.lower().strip()] = p
    return passwords


def save_user_password(username: str, password_hash: str):
    db = get_db()
    u_clean = username.lower().strip()
    db["users"].update_one(
        {"username": u_clean},
        {"$set": {
            "username": u_clean,
            "password_hash": password_hash,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }},
        upsert=True
    )


# ==========================================
# ADMIN OPERATIONS
# ==========================================

def delete_member_record(member_name: str) -> bool:
    """Delete a member from the room and remove their user account."""
    db = get_db()
    m_clean = member_name.lower().strip()
    res1 = db["members"].delete_one({"name": m_clean})
    res2 = db["users"].delete_one({"username": m_clean})
    return bool(res1.deleted_count > 0 or res2.deleted_count > 0)


def admin_reset_user_password(username: str, password_hash: str):
    """Admin forcibly sets or resets a user's password."""
    save_user_password(username, password_hash)


def reset_all_expenses() -> int:
    """Wipe all expense entries from the ledger."""
    db = get_db()
    res = db["expenses"].delete_many({})
    return res.deleted_count


def reset_all_deposits() -> int:
    """Wipe all pool deposits and reset member base payments to 0."""
    db = get_db()
    res = db["pool_deposits"].delete_many({})
    db["members"].update_many({}, {"$set": {"base_payment": 0.0}})
    return res.deleted_count


def factory_reset_all_data():
    """Complete factory reset: restores defaults and clears all transactions."""
    db = get_db()
    # 1. Reset config
    db["config"].update_one(
        {"_id": CONFIG_DOC_ID},
        {"$set": DEFAULT_CONFIG},
        upsert=True
    )
    # 2. Reset members to default members with 0 deposit
    db["members"].delete_many({})
    for m in DEFAULT_MEMBERS:
        db["members"].insert_one({"name": m, "base_payment": 0.0})
    # 3. Wipe expenses & pool deposits
    db["expenses"].delete_many({})
    db["pool_deposits"].delete_many({})
    # 4. Clear users except admin
    db["users"].delete_many({"username": {"$ne": "admin"}})
    return True


def get_detailed_users_list() -> List[Dict[str, Any]]:
    """Returns detailed info for all members and accounts for the admin console."""
    db = get_db()
    cfg = get_config()
    leader = cfg.get("leader", "")
    members_cursor = db["members"].find({})
    members_map = {m["name"]: m.get("base_payment", 0.0) for m in members_cursor if "name" in m}
    
    users_cursor = db["users"].find({})
    passwords_map = {u["username"]: True for u in users_cursor if "username" in u}

    all_names = set(members_map.keys()) | set(passwords_map.keys()) | {"admin"}
    result = []
    for name in sorted(all_names):
        is_admin = (name == "admin")
        is_leader = (name == leader)
        result.append({
            "username": name,
            "is_member": name in members_map,
            "base_payment": members_map.get(name, 0.0),
            "role": "Admin" if is_admin else ("Account Holder" if is_leader else "Roommate"),
            "has_custom_password": name in passwords_map
        })
    return result


# ==========================================
# AGGREGATED STATE INTERFACE (For Passbook & Calculator)
# ==========================================

def load_db() -> Dict[str, Any]:
    """
    Fetches the combined room state from all separate collections
    to pass cleanly to calculators and frontend payloads.
    """
    cfg = get_config()
    members = get_members_list()
    base_payments = get_base_payments()
    expenses = get_all_expenses()
    deposits = get_all_deposits()
    passwords = get_user_passwords()

    return {
        "month": cfg.get("month", "September"),
        "leader": cfg.get("leader", "yashwanth"),
        "base_amount": float(cfg.get("base_amount", 1000.0)),
        "members": members,
        "base_payments": base_payments,
        "expenses": expenses,
        "pool_deposits": deposits,
        "passwords": passwords
    }


def save_db(data: Dict[str, Any]):
    """
    Synchronizes any changes in the combined state dictionary back
    to the respective separate collections in MongoDB.
    """
    if not isinstance(data, dict):
        return

    # Update config collection
    if "month" in data or "leader" in data or "base_amount" in data:
        update_config(
            month=data.get("month", "September"),
            leader=data.get("leader", "yashwanth"),
            base_amount=float(data.get("base_amount", 1000.0))
        )

    # Update members & payments collection
    if "members" in data or "base_payments" in data:
        members = data.get("members", [])
        payments = data.get("base_payments", {})
        for m in members:
            set_member_payment(m, payments.get(m, 0.0))

    # Update passwords collection
    if "passwords" in data:
        for u, h in data["passwords"].items():
            save_user_password(u, h)
