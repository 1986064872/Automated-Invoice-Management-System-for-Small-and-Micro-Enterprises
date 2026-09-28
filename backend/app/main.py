"""FastAPI 应用入口。

启动：
    cd backend
    uvicorn app.main:app --reload --port 8000

接口文档：http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import PROJECT_DIR, settings
from .database import SessionLocal, init_db
from .ocr import OCRProviderError, shutdown_paddle_workers
from .routers import dashboard, exports, files, invoices, ledger, rules
from .seed import seed_system_rules
from .services.storage import UploadRejected


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        created = seed_system_rules(db)
        if created:
            print(f"[init] 已写入 {created} 条系统分类规则")
    finally:
        db.close()
    yield
    # 关服务时把 PaddleOCR 子进程一起收掉，别留孤儿进程占着显存
    shutdown_paddle_workers()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "小微企业票据记账助手 V1 —— 上传票据 → 自动识别 → 规则校验 → 人工复核 → "
        "生成账目 → 导出 Excel。OCR 负责识别，规则负责确定性校验，人工负责最终确认。"
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# 统一异常处理：把内部错误码翻译成前端能用的结构
# --------------------------------------------------------------------------
@app.exception_handler(UploadRejected)
async def upload_rejected_handler(_request: Request, exc: UploadRejected):
    return JSONResponse(status_code=400, content={"detail": exc.message, "code": "upload_rejected"})


@app.exception_handler(OCRProviderError)
async def ocr_error_handler(_request: Request, exc: OCRProviderError):
    # 注意：只返回 code 和中文说明，绝不回传密钥或完整票据内容
    return JSONResponse(
        status_code=502, content={"detail": exc.message, "code": exc.code}
    )


# --------------------------------------------------------------------------
# 业务路由
# --------------------------------------------------------------------------
for module in (invoices, files, ledger, exports, rules, dashboard):
    app.include_router(module.router, prefix=settings.api_prefix)


@app.get(f"{settings.api_prefix}/health", tags=["系统"], summary="健康检查")
def health() -> dict:
    return {
        "ok": True,
        "app": settings.app_name,
        "ocr_provider": settings.resolved_ocr_provider,
    }


# --------------------------------------------------------------------------
# 如果前端已经 build 过，就直接由后端托管静态文件 —— 单进程即可演示
# --------------------------------------------------------------------------
_DIST_DIR = PROJECT_DIR / "frontend" / "dist"
_INDEX_FILE = _DIST_DIR / "index.html"


@app.get("/", include_in_schema=False)
def root():
    if _INDEX_FILE.is_file():
        return FileResponse(_INDEX_FILE)
    return {
        "app": settings.app_name,
        "docs": "/docs",
        "api": settings.api_prefix,
        "hint": "前端还没构建。开发时请另外运行 cd frontend && npm run dev",
    }


if _DIST_DIR.is_dir():
    _assets = _DIST_DIR / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        """前端用的是 BrowserRouter，刷新 /ledger、/review 这类地址要有兜底，
        否则会 404。这里把非 API 的未知路径统统交回 index.html。"""
        if full_path.startswith(("api/", "docs", "redoc", "openapi.json")):
            raise HTTPException(status_code=404, detail="接口不存在")
        if _INDEX_FILE.is_file():
            return FileResponse(_INDEX_FILE)
        raise HTTPException(status_code=404, detail="前端资源不存在")
