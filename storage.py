"""Lưu / xóa file tài liệu do quản trị viên tải lên (PDF, slide, ảnh...)"""

import base64
import binascii
import os
import uuid

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
MAX_UPLOAD_MB = 20
ALLOWED_EXT = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
               ".txt", ".md", ".csv", ".zip", ".png", ".jpg", ".jpeg"} # không nhận .html, .js, .exe...
INLINE_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".txt"} # mở thẳng trên trình duyệt; loại khác tải về

def save_upload(contents, filename):
    """Nhận dữ liệu từ dcc.Upload, kiểm tra rồi lưu. Trả về (tên file đã lưu, tên gốc)"""
    if not contents or not filename:
        raise ValueError("Chưa chọn file.")
    try:
        raw = base64.b64decode(contents.split(",", 1)[1], validate=True)
    except (IndexError, binascii.Error, ValueError):
        raise ValueError("Không đọc được file tải lên.")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"Không hỗ trợ loại file «{ext or 'không có đuôi'}». Chỉ nhận: {', '.join(sorted(ALLOWED_EXT))}.")
    if not raw or len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"File rỗng hoặc lớn hơn {MAX_UPLOAD_MB} MB.")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    stored = uuid.uuid4().hex + ext
    with open(os.path.join(UPLOAD_DIR, stored), "wb") as f:
        f.write(raw)
    return stored, os.path.basename(filename.replace("\\", "/"))[:150]

def delete_file(stored):
    """Xóa file khỏi ổ đĩa (bỏ qua nếu không có tên hoặc file không còn)"""
    if stored:
        try:
            os.remove(os.path.join(UPLOAD_DIR, os.path.basename(stored)))   # basename: không thoát khỏi uploads/
        except FileNotFoundError:
            pass