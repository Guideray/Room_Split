def calculate_summary(data):
    members = data.get("members", [])
    expenses = data.get("expenses", [])
    num_members = len(members)
    
    base_payments = data.get("base_payments", {})
    pool_collected = sum(float(v) for v in base_payments.values())
    
    # Track out-of-pocket spent by members vs spent directly from the Room Account
    pool_spent = 0.0
    personal_spent = {m: 0.0 for m in members}
    shares = {m: 0.0 for m in members}
    total_expenses = 0.0

    # Room Account payer keywords
    room_acct_aliases = {"room account", "room_account", "room fund", "room pool", "pool", "room"}

    for exp in expenses:
        amount = float(exp.get("amount", 0.0))
        total_expenses += amount
        payer = str(exp.get("payer", "")).strip()

        # Check if paid by the Room Account
        if payer.lower() in room_acct_aliases:
            pool_spent += amount
        elif payer in personal_spent:
            personal_spent[payer] += amount

        # Determine split breakdown (unequal custom amounts or equal split)
        split_mode = exp.get("split_mode", "equal")
        custom_splits = exp.get("custom_splits")

        if split_mode == "unequal" and isinstance(custom_splits, dict) and len(custom_splits) > 0:
            for m, custom_amt in custom_splits.items():
                if m in shares:
                    shares[m] += float(custom_amt)
        else:
            split_between = exp.get("split_between")
            if split_between and isinstance(split_between, list) and len(split_between) > 0:
                target_members = [m for m in split_between if m in shares]
            else:
                target_members = list(members)

            if target_members:
                per_person_share = amount / len(target_members)
                for m in target_members:
                    shares[m] += per_person_share

    # Whole base amount stays in the Room Account (less any expenses paid from it)
    room_account_balance = pool_collected - pool_spent

    # Calculate net balance for each roommate:
    # Net Balance = (Deposited to Room Account + Personal Out-of-Pocket) - Consumed Expense Share
    # Positive (> 0): Gets back from Room Account
    # Negative (< 0): Owes into Room Account
    balances = {}
    member_breakdown = {}
    for m in members:
        dep = round(float(base_payments.get(m, 0.0)), 2)
        oop = round(float(personal_spent.get(m, 0.0)), 2)
        sh = round(float(shares.get(m, 0.0)), 2)
        bal = round(dep + oop - sh, 2)
        balances[m] = bal
        member_breakdown[m] = {
            "deposited": dep,
            "out_of_pocket": oop,
            "share": sh,
            "room_balance": bal,
            "net_balance": bal,
            "status": "in_fund" if bal >= 0 else "deficit",
            "status_text": f"₹{bal:,.2f} in room fund" if bal >= 0 else f"Deficit ₹{abs(bal):,.2f} (top-up needed)"
        }

    # Overall average fair share for display
    fair_share = total_expenses / num_members if num_members > 0 else 0.0

    # 4. Settle debts:
    # The split happens directly from the Room Amount given by each roommate.
    # Roommates who have a positive balance (bal >= 0) keep their money in the Room Account.
    # Only roommates with a DEFICIT (bal < -0.01) need to deposit the shortfall into the Room Account.
    settlements = []
    for m in members:
        bal = balances[m]
        if bal < -0.01:
            settlements.append({
                "from": m,
                "to": "Room Account",
                "amount": round(abs(bal), 2),
                "reason": "Deficit Top-up"
            })

    return {
        "total_expenses": round(total_expenses, 2),
        "fair_share": round(fair_share, 2),
        "pool_collected": round(pool_collected, 2),
        "pool_spent": round(pool_spent, 2),
        "room_account_balance": round(room_account_balance, 2),
        "member_breakdown": member_breakdown,
        "balances": {k: round(v, 2) for k, v in balances.items()},
        "settlements": settlements
    }

