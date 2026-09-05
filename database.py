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

DEFAULT_MEMBERS = ["madhu", "satya", "siva", "manohar", "yashwanth"]
DEFAULT_ROTATION_ORDER = ["madhu", "satya", "siva", "manohar", "yashwanth"]

DEFAULT_CONFIG = {
    "_id": CONFIG_DOC_ID,
    "month": "September",
    "leader": "madhu",
    "base_amount": 1000.0,
    "upi_id": "",
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

    # 4. Handle legacy unique email index in users collection
    try:
        # Backfill distinct email for any existing document with missing/null email
        for u in users_col.find({"$or": [{"email": None}, {"email": {"$exists": False}}]}):
            uname = u.get("username") or str(u.get("_id"))
            users_col.update_one(
                {"_id": u["_id"]},
                {"$set": {"email": f"{uname}@roomsplit.local"}}
            )
        # Drop legacy unique email index if present
        idx_info = users_col.index_information()
        if "email_1" in idx_info:
            users_col.drop_index("email_1")
            print("[MongoDB] Successfully dropped legacy unique index 'email_1' from users collection.")
    except Exception as idx_err:
        print(f"[MongoDB Warning] Index fix notice: {idx_err}")


# ==========================================
# SEPARATE COLLECTION ACCESSORS
# ==========================================

def generate_default_rotation_schedule(starting_month: str = "September", starting_holder: str = "madhu") -> Dict[str, str]:
    order = DEFAULT_ROTATION_ORDER.copy()
    if starting_holder in order:
        idx = order.index(starting_holder)
        order = order[idx:] + order[:idx]

    months_seq = [
        "September", "October", "November", "December",
        "January", "February", "March", "April", "May", "June", "July", "August"
    ]
    if starting_month in months_seq:
        s_idx = months_seq.index(starting_month)
        months_seq = months_seq[s_idx:] + months_seq[:s_idx]

    sched = {}
    for i, m in enumerate(months_seq):
        sched[m] = order[i % len(order)]
    return sched


def get_month_schedule() -> Dict[str, str]:
    db = get_db()
    cfg = db["config"].find_one({"_id": CONFIG_DOC_ID})
    sched = cfg.get("month_schedule") if cfg else None
    if not sched or not isinstance(sched, dict) or len(sched) < 12:
        sched = generate_default_rotation_schedule()
        db["config"].update_one(
            {"_id": CONFIG_DOC_ID},
            {"$set": {"month_schedule": sched}},
            upsert=True
        )
    return sched


def update_month_holder(month: str, holder: str):
    db = get_db()
    clean_holder = holder.lower().strip()
    sched = get_month_schedule()

    # Canonical Title case month normalization
    matched_month = next((m for m in [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ] if m.lower() == month.lower().strip()), month.strip().capitalize())

    # Remove any conflicting case keys
    for k in list(sched.keys()):
        if k.lower() == matched_month.lower():
            del sched[k]
    sched[matched_month] = clean_holder

    update_set = {
        "month_schedule": sched,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    cfg = db["config"].find_one({"_id": CONFIG_DOC_ID})
    cur_m = (cfg.get("month") or "September").strip() if cfg else "September"
    if cur_m.lower() == matched_month.lower():
        update_set["leader"] = clean_holder

    db["config"].update_one(
        {"_id": CONFIG_DOC_ID},
        {"$set": update_set},
        upsert=True
    )
    return sched


def admin_set_account_holder(new_leader: str) -> Dict[str, Any]:
    """Admin explicitly assigns the active room account holder."""
    db = get_db()
    clean_leader = new_leader.lower().strip()
    cfg = db["config"].find_one({"_id": CONFIG_DOC_ID}) or {}
    cur_m = (cfg.get("month") or datetime.now().strftime("%B")).strip()
    matched_month = next((m for m in [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ] if m.lower() == cur_m.lower()), cur_m.capitalize())

    sched = get_month_schedule()
    for k in list(sched.keys()):
        if k.lower() == matched_month.lower():
            del sched[k]
    sched[matched_month] = clean_leader

    update_set = {
        "leader": clean_leader,
        "month": matched_month,
        "month_schedule": sched,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    db["config"].update_one(
        {"_id": CONFIG_DOC_ID},
        {"$set": update_set},
        upsert=True
    )
    return {"leader": clean_leader, "month": matched_month, "schedule": sched}



def swap_month_holders(month_a: str, month_b: str):
    db = get_db()
    sched = get_month_schedule()
    holder_a = sched.get(month_a, "madhu")
    holder_b = sched.get(month_b, "satya")
    sched[month_a] = holder_b
    sched[month_b] = holder_a

    update_set = {
        "month_schedule": sched,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    cfg = db["config"].find_one({"_id": CONFIG_DOC_ID})
    if cfg:
        cur_m = cfg.get("month")
        if cur_m == month_a:
            update_set["leader"] = holder_b
            update_set["upi_id"] = get_user_upi(holder_b)
        elif cur_m == month_b:
            update_set["leader"] = holder_a
            update_set["upi_id"] = get_user_upi(holder_a)

    db["config"].update_one(
        {"_id": CONFIG_DOC_ID},
        {"$set": update_set},
        upsert=True
    )
    return sched


def reset_month_schedule():
    db = get_db()
    sched = generate_default_rotation_schedule()
    cfg = db["config"].find_one({"_id": CONFIG_DOC_ID})
    cur_m = cfg.get("month", "September") if cfg else "September"
    reset_leader = sched.get(cur_m, "madhu").lower().strip()
    update_set = {
        "month_schedule": sched,
        "leader": reset_leader,
        "upi_id": get_user_upi(reset_leader),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    db["config"].update_one(
        {"_id": CONFIG_DOC_ID},
        {"$set": update_set},
        upsert=True
    )
    return sched


def get_config() -> Dict[str, Any]:
    db = get_db()
    cfg = db["config"].find_one({"_id": CONFIG_DOC_ID})
    if not cfg:
        cfg = DEFAULT_CONFIG.copy()

    # Auto-calendar sync: if auto_calendar is enabled (default True), automatically match the real-world calendar month
    auto_cal = cfg.get("auto_calendar", True)
    cur_cal_month = datetime.now().strftime("%B")  # e.g., "September"
    sched = get_month_schedule()
    if auto_cal and (cfg.get("month") or "").strip().lower() != cur_cal_month.lower():
        cfg["month"] = cur_cal_month
        cfg["auto_calendar"] = True
        matched_sched_leader = next((h for m, h in sched.items() if m.lower() == cur_cal_month.lower()), None)
        if matched_sched_leader:
            cfg["leader"] = matched_sched_leader.lower().strip()
        db["config"].update_one(
            {"_id": CONFIG_DOC_ID},
            {"$set": {
                "month": cur_cal_month,
                "leader": cfg.get("leader", "madhu"),
                "auto_calendar": True,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
            }},
            upsert=True
        )
    return cfg


def set_month(month: str, auto_calendar: bool = False):
    db = get_db()
    sched = get_month_schedule()
    matched_month = next((m for m in [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ] if m.lower() == month.lower().strip()), month.strip().capitalize())
    new_leader = next((h for m, h in sched.items() if m.lower() == matched_month.lower()), "madhu").lower().strip()
    set_fields = {
        "month": matched_month,
        "leader": new_leader,
        "auto_calendar": auto_calendar,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    db["config"].update_one(
        {"_id": CONFIG_DOC_ID},
        {"$set": set_fields},
        upsert=True
    )


def update_config(month: str, leader: str, base_amount: float, auto_calendar: bool = False, upi_id: str = ""):
    db = get_db()
    clean_leader = leader.lower().strip()
    matched_month = next((m for m in [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ] if m.lower() == month.lower().strip()), month.strip().capitalize())

    sched = get_month_schedule()
    for k in list(sched.keys()):
        if k.lower() == matched_month.lower():
            del sched[k]
    sched[matched_month] = clean_leader

    db["config"].update_one(
        {"_id": CONFIG_DOC_ID},
        {"$set": {
            "month": matched_month,
            "leader": clean_leader,
            "base_amount": float(base_amount),
            "auto_calendar": auto_calendar,
            "upi_id": "",
            "month_schedule": sched,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }},
        upsert=True
    )


def update_upi_id(upi_id: str):
    return update_upi_and_holder(upi_id)


def update_upi_and_holder(upi_id: str, leader: Optional[str] = None):
    db = get_db()
    clean_upi = upi_id.strip().lower()
    update_fields = {
        "upi_id": clean_upi,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    cfg = db["config"].find_one({"_id": CONFIG_DOC_ID})
    cur_leader = (leader.lower().strip() if leader else (cfg.get("leader") or "madhu").lower().strip()) if cfg else "madhu"
    if leader:
        clean_leader = leader.lower().strip()
        update_fields["leader"] = clean_leader
        cur_m = cfg.get("month", "September") if cfg else "September"
        sched = get_month_schedule()
        sched[cur_m] = clean_leader
        update_fields["month_schedule"] = sched

    save_user_upi(cur_leader, clean_upi)

    db["config"].update_one(
        {"_id": CONFIG_DOC_ID},
        {"$set": update_fields},
        upsert=True
    )
    return clean_upi


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
    try:
        db["users"].update_one(
            {"username": u_clean},
            {
                "$set": {
                    "username": u_clean,
                    "password_hash": password_hash,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
                },
                "$setOnInsert": {
                    "email": f"{u_clean}@roomsplit.local"
                }
            },
            upsert=True
        )
    except Exception as e:
        print(f"[MongoDB Warning] save_user_password fallback: {e}")
        try:
            db["users"].drop_index("email_1")
        except Exception:
            pass
        db["users"].update_one(
            {"username": u_clean},
            {"$set": {
                "password_hash": password_hash,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
            }}
        )


def get_user_upi(username: str) -> str:
    """Retrieve the personal room money UPI ID for a specific roommate."""
    if not username:
        return ""
    try:
        db = get_db()
        u_clean = username.lower().strip()
        user_doc = db["users"].find_one({"username": u_clean})
        if user_doc and user_doc.get("upi_id"):
            return str(user_doc["upi_id"]).strip().lower()
    except Exception as err:
        print(f"[MongoDB Warning] Error in get_user_upi: {err}")
    return ""


def save_user_upi(username: str, upi_id: str) -> str:
    """Save a roommate's personal room money UPI ID."""
    db = get_db()
    u_clean = username.lower().strip()
    clean_upi = upi_id.strip().lower()

    try:
        db["users"].update_one(
            {"username": u_clean},
            {
                "$set": {
                    "username": u_clean,
                    "upi_id": clean_upi,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
                },
                "$setOnInsert": {
                    "email": f"{u_clean}@roomsplit.local"
                }
            },
            upsert=True
        )
    except Exception as e:
        print(f"[MongoDB Warning] save_user_upi upsert notice: {e}")
        try:
            db["users"].drop_index("email_1")
        except Exception:
            pass
        db["users"].update_one(
            {"username": u_clean},
            {"$set": {
                "upi_id": clean_upi,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
            }}
        )

    # If this user is currently the active room account holder, sync config upi_id
    cfg = db["config"].find_one({"_id": CONFIG_DOC_ID})
    cur_leader = (cfg.get("leader") or "").lower().strip() if cfg else ""
    if u_clean == cur_leader:
        db["config"].update_one(
            {"_id": CONFIG_DOC_ID},
            {"$set": {
                "upi_id": clean_upi,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
            }},
            upsert=True
        )
    return clean_upi


def get_all_user_upis() -> Dict[str, str]:
    """Returns mapping of all roommates to their personal UPI IDs."""
    db = get_db()
    cursor = db["users"].find({"upi_id": {"$exists": True, "$ne": ""}})
    res = {}
    for doc in cursor:
        u = doc.get("username")
        upi = doc.get("upi_id")
        if u and upi:
            res[u.lower().strip()] = upi
    return res


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
    leader = (cfg.get("leader") or "").lower().strip()
    members_cursor = db["members"].find({})
    members_map = {m["name"].lower().strip(): m.get("base_payment", 0.0) for m in members_cursor if "name" in m}
    
    users_cursor = db["users"].find({})
    passwords_map = {u["username"].lower().strip(): True for u in users_cursor if "username" in u}

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

    sched = get_month_schedule()
    current_month = cfg.get("month", "September")
    matched_sched_leader = next((h for m, h in sched.items() if m.lower() == current_month.lower()), "madhu")
    active_leader = (cfg.get("leader") or matched_sched_leader).lower().strip()

    user_upis = get_all_user_upis()

    return {
        "month": current_month,
        "leader": active_leader,
        "base_amount": float(cfg.get("base_amount", 1000.0)),
        "upi_id": "",
        "user_upis": user_upis,
        "month_schedule": sched,
        "holder_schedule": sched,
        "rotation_order": DEFAULT_ROTATION_ORDER,
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
    if "month" in data or "leader" in data or "base_amount" in data or "upi_id" in data:
        update_config(
            month=data.get("month", "September"),
            leader=data.get("leader", "yashwanth"),
            base_amount=float(data.get("base_amount", 1000.0)),
            upi_id=data.get("upi_id", "")
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
