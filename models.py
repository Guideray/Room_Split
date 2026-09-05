from pydantic import BaseModel
from typing import List, Optional, Dict

class SetupData(BaseModel):
    month: str
    leader: str
    base_amount: float
    members: List[str]

class PaymentData(BaseModel):
    member: str
    amount: float

class ExpenseData(BaseModel):
    payer: str
    desc: str
    amount: float
    split_between: Optional[List[str]] = None
    split_mode: Optional[str] = "equal"  # "equal" or "unequal"
    custom_splits: Optional[Dict[str, float]] = None

class TopUpData(BaseModel):
    member: str
    amount: float
    note: Optional[str] = "Pool Top-up"

class LoginData(BaseModel):
    username: str
    password: str

class ChangePasswordData(BaseModel):
    username: str
    old_password: str
    new_password: str

class CreateUserData(BaseModel):
    username: str
    password: Optional[str] = None

class AdminCreateUserData(BaseModel):
    username: str
    initial_deposit: Optional[float] = 0.0
    password: Optional[str] = None

class AdminResetPasswordData(BaseModel):
    username: str
    new_password: str

class SetMonthData(BaseModel):
    month: str
    auto_calendar: Optional[bool] = False

class UserUpiData(BaseModel):
    upi_id: str


