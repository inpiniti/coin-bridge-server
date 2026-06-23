import time
import hmac
import hashlib
import json
import logging
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel
import httpx

router = APIRouter(prefix="/api/bybit", tags=["Bybit"])
logger = logging.getLogger("bybit")

BYBIT_BASE_URL = "https://api.bybit.com"

class WithdrawRequest(BaseModel):
    coin: str
    chain: str
    address: str
    amount: str
    tag: str = None  # Destination Tag / Memo for XRP, EOS, etc.

def generate_bybit_signature(api_secret: str, api_key: str, timestamp: str, recv_window: str, payload_str: str) -> str:
    """Bybit V5 API signature generator"""
    val = timestamp + api_key + recv_window + payload_str
    return hmac.new(api_secret.encode("utf-8"), val.encode("utf-8"), hashlib.sha256).hexdigest()

async def make_bybit_request(
    method: str,
    path: str,
    api_key: str,
    api_secret: str,
    params: dict = None,
    body: dict = None
):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    
    payload_str = ""
    if method.upper() == "GET" and params:
        # Sort query params as Bybit V5 requires
        sorted_params = sorted(params.items())
        payload_str = "&".join([f"{k}={v}" for k, v in sorted_params])
    elif method.upper() == "POST" and body:
        payload_str = json.dumps(body)
        
    signature = generate_bybit_signature(api_secret, api_key, timestamp, recv_window, payload_str)
    
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }
    
    url = f"{BYBIT_BASE_URL}{path}"
    
    async with httpx.AsyncClient() as client:
        try:
            if method.upper() == "GET":
                response = await client.get(url, params=params, headers=headers, timeout=10.0)
            else:
                response = await client.post(url, content=payload_str, headers=headers, timeout=10.0)
            
            res_json = response.json()
            if response.status_code != 200 or res_json.get("retCode") != 0:
                logger.error(f"Bybit API error: {res_json}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Bybit API Error: {res_json.get('retMsg', 'Unknown error')} (code: {res_json.get('retCode')})"
                )
            return res_json
        except httpx.RequestError as exc:
            logger.error(f"HTTP request failed: {exc}")
            raise HTTPException(status_code=500, detail=f"Bybit connection failed: {str(exc)}")

@router.get("/balance")
async def get_bybit_balance(
    x_bybit_api_key: str = Header(..., description="Bybit API Key"),
    x_bybit_api_secret: str = Header(..., description="Bybit API Secret")
):
    """Bybit Unified 및 Funding 계정 자산을 동시 조회하여 보유 중인 코인 리스트를 반환합니다."""
    # 1. Unified Wallet Balance 조회
    unified_params = {"accountType": "UNIFIED"}
    unified_res = await make_bybit_request("GET", "/v5/account/wallet-balance", x_bybit_api_key, x_bybit_api_secret, params=unified_params)
    
    # 2. Funding Wallet Balance 조회
    funding_params = {"accountType": "FUNDING"}
    funding_res = await make_bybit_request("GET", "/v5/asset/transfer/query-account-coins-balance", x_bybit_api_key, x_bybit_api_secret, params=funding_params)
    
    balances = {}
    
    # Unified 잔고 파싱
    try:
        list_data = unified_res.get("result", {}).get("list", [])
        if list_data:
            coins = list_data[0].get("coin", [])
            for coin_info in coins:
                coin_name = coin_info.get("coin")
                wallet_balance = float(coin_info.get("walletBalance", 0) or 0)
                usd_value = float(coin_info.get("usdValue", 0) or 0)
                if wallet_balance > 0:
                    balances[coin_name] = {
                        "coin": coin_name,
                        "unifiedBalance": wallet_balance,
                        "fundingBalance": 0.0,
                        "totalBalance": wallet_balance,
                        "usdValue": usd_value
                    }
    except Exception as e:
        logger.warning(f"Failed to parse Unified balance: {e}")
        
    # Funding 잔고 파싱
    try:
        coins = funding_res.get("result", {}).get("balance", [])
        for coin_info in coins:
            coin_name = coin_info.get("coin")
            wallet_balance = float(coin_info.get("walletBalance", 0) or 0)
            if wallet_balance > 0:
                if coin_name in balances:
                    balances[coin_name]["fundingBalance"] = wallet_balance
                    balances[coin_name]["totalBalance"] += wallet_balance
                else:
                    balances[coin_name] = {
                        "coin": coin_name,
                        "unifiedBalance": 0.0,
                        "fundingBalance": wallet_balance,
                        "totalBalance": wallet_balance,
                        "usdValue": 0.0  # Funding은 usdValue 계산 직접 노출 안 될 수 있으므로 일단 0.0
                    }
    except Exception as e:
        logger.warning(f"Failed to parse Funding balance: {e}")
        
    return list(balances.values())

@router.post("/withdraw")
async def withdraw_to_upbit(
    req: WithdrawRequest,
    x_bybit_api_key: str = Header(..., description="Bybit API Key"),
    x_bybit_api_secret: str = Header(..., description="Bybit API Secret")
):
    """Bybit에서 업비트로 코인을 출금(전송) 신청합니다."""
    body = {
        "coin": req.coin,
        "chain": req.chain,
        "address": req.address,
        "amount": req.amount,
        "timestamp": int(time.time() * 1000),
        "forceChain": 1  # 강제로 체인 고정
    }
    if req.tag:
        body["memo"] = req.tag  # Bybit withdraw memo (XRP, EOS 등 목적지 태그)
        
    res = await make_bybit_request("POST", "/v5/asset/withdraw/create", x_bybit_api_key, x_bybit_api_secret, body=body)
    return {
        "success": True,
        "withdrawId": res.get("result", {}).get("withdrawId"),
        "detail": res
    }
