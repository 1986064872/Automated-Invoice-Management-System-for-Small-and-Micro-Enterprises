"""原票读取：浏览器直接预览 PDF / 图片（复核工作台的左半边靠它）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Invoice, InvoiceFile

router = APIRouter(tags=["文件"])

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}


@router.get("/files/{file_id}/raw", summary="读取原票文件")
def get_raw_file(
    file_id: str,
    download: bool = Query(default=False, description="true 则作为附件下载"),
    db: Session = Depends(get_db),
):
    inv_file = db.get(InvoiceFile, file_id)
    if inv_file is None:
        raise HTTPException(status_code=404, detail="文件记录不存在")
    path = Path(inv_file.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="原票文件已不在磁盘上，可能已被清理")

    return FileResponse(
        path=str(path),
        media_type=MEDIA_TYPES.get(inv_file.file_type, "application/octet-stream"),
        filename=inv_file.original_name,
        # inline 让浏览器内嵌预览；PDF 用浏览器自带阅读器，不用额外渲染库
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/invoices/{invoice_id}/file", summary="按票据 id 读原票")
def get_raw_by_invoice(invoice_id: str, download: bool = False, db: Session = Depends(get_db)):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="票据不存在")
    return get_raw_file(invoice.file_id, download=download, db=db)
