import logging
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import bybit, upbit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("main")

app = FastAPI(
    title="Coin Bridge Server",
    version="1.0.0",
    description="Bybit & Upbit API Proxy Server for Coin Bridge App",
)

# React 앱과의 원활한 CORS 연동을 위한 미들웨어 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 실 배포 환경에서는 프론트엔드 주소로 명시
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 라우터 등록
app.include_router(bybit.router)
app.include_router(upbit.router)

@app.get("/", summary="Health Check")
async def health():
    return {
        "status": "ok",
        "service": "coin-bridge-server",
        "version": "1.0.0"
    }

@app.get("/api/ip", summary="Get Server Public IP")
async def get_server_ip():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.ipify.org?format=json", timeout=5.0)
            if response.status_code == 200:
                return response.json()
            else:
                return {"ip": "Unknown (Status: {})".format(response.status_code)}
    except Exception as e:
        logger.error(f"Failed to get server public IP: {e}")
        return {"ip": "Unknown (Error)"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

