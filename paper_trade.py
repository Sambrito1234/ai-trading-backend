portfolio = {
    "cash": 100000,
    "stocks": {},
    "history": []
}


def buy(symbol, price, quantity):

    total_cost = price * quantity

    if portfolio["cash"] < total_cost:
        return {"message": "Insufficient Funds"}

    portfolio["cash"] -= total_cost

    if symbol not in portfolio["stocks"]:
        portfolio["stocks"][symbol] = 0

    portfolio["stocks"][symbol] += quantity

    portfolio["history"].append({
        "action": "BUY",
        "symbol": symbol,
        "price": price,
        "quantity": quantity
    })

    return {
        "message": "Bought Successfully",
        "remaining_cash": round(portfolio["cash"], 2)
    }


def sell(symbol, price, quantity):

    if symbol not in portfolio["stocks"]:
        return {"message": "Stock Not Owned"}

    if portfolio["stocks"][symbol] < quantity:
        return {"message": "Not Enough Shares"}

    portfolio["stocks"][symbol] -= quantity

    portfolio["cash"] += price * quantity

    portfolio["history"].append({
        "action": "SELL",
        "symbol": symbol,
        "price": price,
        "quantity": quantity
    })

    return {
        "message": "Sold Successfully",
        "remaining_cash": round(portfolio["cash"], 2)
    }