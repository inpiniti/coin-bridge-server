import time
import uuid
import hashlib
from urllib.parse import urlencode, unquote
import jwt
import logging
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
import httpx

router = APIRouter(prefix="/api/upbit", tags=["Upbit"])
logger = logging.getLogger("upbit")

UPBIT_BASE_URL = "https://api.upbit.com"

class SellRequest(BaseModel):
    market: str  # e.g., "KRW-XRP"
    volume: str  # 수량
    ord_type: str = "market"  # market or limit
    price: str = None  # 지정가인 경우 필요

class WithdrawKrwRequest(BaseModel):
    amount: str  # 출금 금액

def make_upbit_headers(access_key: str, secret_key: str, params: dict = None) -> dict:
    """Upbit API JWT Authorization Header generator"""
    payload = {
        "access_key": access_key,
        "nonce": str(uuid.uuid4()),
    }
    
    if params:
        query_string = urlencode(params).encode("utf-8")
        m = hashlib.sha512()
        m.update(query_string)
        query_hash = m.hexdigest()
        payload["query_hash"] = query_hash
        payload["query_hash_alg"] = "SHA512"
        
    jwt_token = jwt.encode(payload, secret_key, algorithm="HS256")
    authorization_token = f"Bearer {jwt_token}"
    return {
        "Authorization": authorization_token,
        "Content-Type": "application/json"
    }

async def make_upbit_request(
    method: str,
    path: str,
    access_key: str,
    secret_key: str,
    params: dict = None,
    body: dict = None
):
    # GET/POST 모두 query_hash 계산을 위해 params 또는 body를 query parameter 형태로 인코딩
    req_params = params or {}
    if method.upper() == "POST" and body:
        # Upbit POST는 JSON body 형식이 아닌 x-www-form-urlencoded 방식을 사용하는 API가 많으므로
        # body를 query parameter 형태로 묶어서 JWT query_hash를 만듭니다.
        req_params = body

    headers = make_upbit_headers(access_key, secret_key, params=req_params)
    url = f"{UPBIT_BASE_URL}{path}"
    
    async with httpx.AsyncClient() as client:
        try:
            if method.upper() == "GET":
                response = await client.get(url, params=req_params, headers=headers, timeout=10.0)
            else:
                # Upbit 주문/출금 POST API는 application/json 또는 application/x-www-form-urlencoded 둘 다 지원하나,
                # form parameter 형태로 전송하는 것이 가장 호환성이 좋습니다.
                response = await client.post(url, json=body, headers=headers, timeout=10.0)
            
            res_json = response.json()
            if response.status_code not in [200, 201]:
                logger.error(f"Upbit API error: {res_json} (status: {response.status_code})")
                error_msg = res_json.get("error", {}).get("message", "Unknown error")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Upbit API Error: {error_msg}"
                )
            return res_json
        except httpx.RequestError as exc:
            logger.error(f"HTTP request failed: {exc}")
            raise HTTPException(status_code=500, detail=f"Upbit connection failed: {str(exc)}")

@router.get("/balance")
async def get_upbit_balance(
    x_upbit_access_key: str = Header(..., description="Upbit Access Key"),
    x_upbit_secret_key: str = Header(..., description="Upbit Secret Key")
):
    """업비트의 자산 보유 정보를 조회합니다. (원화 및 보유 코인 수량)"""
    res = await make_upbit_request("GET", "/v1/accounts", x_upbit_access_key, x_upbit_secret_key)
    # 업비트는 KRW 포함 자산 목록을 리스트로 리턴함
    return res

@router.get("/deposit-address")
async def get_deposit_address(
    currency: str,
    net_type: str = None,
    x_upbit_access_key: str = Header(..., description="Upbit Access Key"),
    x_upbit_secret_key: str = Header(..., description="Upbit Secret Key")
):
    """특정 코인의 업비트 입금 주소를 조회합니다. 주소가 없다면 자동 생성을 시도합니다."""
    # 업비트의 기본 net_type 매핑 (XRP->XRP, BTC->BTC, ETH->ETH 등. 대문자 매핑)
    if not net_type:
        net_type = currency.upper()
        
    params = {
        "currency": currency.upper(),
        "net_type": net_type
    }
    
    try:
        # 1. 기존 입금 주소 조회 시도
        res = await make_upbit_request("GET", "/v1/deposits/coin_address", x_upbit_access_key, x_upbit_secret_key, params=params)
        return res
    except HTTPException as e:
        # 만약 입금 주소가 없다는 에러인 경우 (보통 에러 메시지에 'deposit_address_not_exist' 등 포함) 생성 시도
        if "not_exist" in str(e.detail) or "없습니다" in str(e.detail):
            logger.info(f"Deposit address for {currency} not exist, trying to generate...")
            try:
                gen_res = await make_upbit_request("POST", "/v1/deposits/generate_coin_address", x_upbit_access_key, x_upbit_secret_key, body=params)
                return gen_res
            except Exception as gen_err:
                logger.error(f"Failed to generate deposit address: {gen_err}")
                raise HTTPException(status_code=400, detail=f"입금 주소 조회 및 생성 실패: {str(gen_err)}")
        else:
            raise e

@router.post("/sell")
async def sell_upbit_coin(
    req: SellRequest,
    x_upbit_access_key: str = Header(..., description="Upbit Access Key"),
    x_upbit_secret_key: str = Header(..., description="Upbit Secret Key")
):
    """업비트에서 코인을 시장가 또는 지정가로 매도(판매)합니다."""
    body = {
        "market": req.market.upper(),
        "side": "ask",
        "volume": req.volume,
        "ord_type": req.ord_type
    }
    if req.ord_type == "limit":
        if not req.price:
            raise HTTPException(status_code=400, detail="지정가 매도 주문 시 price는 필수입니다.")
        body["price"] = req.price
        
    res = await make_upbit_request("POST", "/v1/orders", x_upbit_access_key, x_upbit_secret_key, body=body)
    return {
        "success": True,
        "order": res
    }

@router.post("/withdraw-krw")
async def withdraw_krw(
    req: WithdrawKrwRequest,
    x_upbit_access_key: str = Header(..., description="Upbit Access Key"),
    x_upbit_secret_key: str = Header(..., description="Upbit Secret Key")
):
    """업비트에 등록 및 연동된 케이뱅크 계좌로 원화(KRW) 출금을 신청합니다."""
    body = {
        "amount": req.amount
    }
    res = await make_upbit_request("POST", "/v1/withdraws/krw", x_upbit_access_key, x_upbit_secret_key, body=body)
    return {
        "success": True,
        "withdraw": res
    }
