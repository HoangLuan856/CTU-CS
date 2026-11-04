"""
storage.py - Lưu / xóa file tài liệu do người dùng tải lên (PDF, slide, ảnh...)
"""
import base64
import binascii
import os
import re
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
MAX_UPLOAD_MB = 20

# Chỉ cho phép các loại tài liệu học tập thông dụng
ALLOWED_EXT = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
               ".txt", ".md", ".csv", ".zip", ".png", ".jpg", ".jpeg"}
INLINE_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".txt"} # mở thẳng trên trình duyệt, loại khác thì tải về

# "Chữ ký" đầu file: kiểm tra nội dung có đúng với đuôi file khai báo không
_OLE, _ZIP = b"\xd0\xcf\x11\xe0", b"PK"
MAGIC = {".pdf": (b"%PDF",), ".png": (b"\x89PNG",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
         ".docx": (_ZIP,), ".pptx": (_ZIP,), ".xlsx": (_ZIP,), ".zip": (_ZIP,),
         ".doc": (_OLE,), ".ppt": (_OLE,), ".xls": (_OLE,)}

STORED_RE = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{2,5}$")      # dạng tên file mà hệ thống tự sinh

def clean_display_name(name):
    """Tên gốc để hiển thị: bỏ đường dẫn và ký tự điều khiển, tối đa 150 ký tự."""
    name = os.path.basename((name or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "", name).strip()
    return name[:150] or "tai-lieu"

def save_upload(contents, filename):
    if not contents or not filename:
        raise ValueError("Chưa chọn file.")
    header, sep, data = contents.partition(",")
    if not sep or ";base64" not in header:
        raise ValueError("File tải lên không hợp lệ.")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Không đọc được file tải lên.")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"Không hỗ trợ loại file «{ext or 'không có đuôi'}». "
                         f"Chỉ nhận: {', '.join(sorted(ALLOWED_EXT))}.")
    if not raw:
        raise ValueError("File rỗng.")
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"File lớn hơn {MAX_UPLOAD_MB} MB.")
    if ext in MAGIC and not raw.startswith(MAGIC[ext]):
        raise ValueError(f"Nội dung file không đúng định dạng {ext}.")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    stored = uuid.uuid4().hex + ext
    with open(os.path.join(UPLOAD_DIR, stored), "wb") as f:
        f.write(raw)
    return stored, clean_display_name(filename)

def full_path(stored):
    """Đường dẫn thật của file đã lưu, None nếu tên không hợp lệ hoặc file không tồn tại."""
    if not STORED_RE.match(stored or ""):
        return None
    path = os.path.join(UPLOAD_DIR, stored)
    return path if os.path.isfile(path) else None

def delete_file(stored):
    """Xóa file khỏi ổ đĩa (bỏ qua nếu tên không hợp lệ hoặc file không còn)."""
    if STORED_RE.match(stored or ""):
        try:
            os.remove(os.path.join(UPLOAD_DIR, stored))
        except FileNotFoundError:
            pass