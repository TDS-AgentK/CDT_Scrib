import mimetypes
import os
import uuid

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024

CONTENT_TYPE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def build_filename(content_type: str) -> str:
    ext = CONTENT_TYPE_EXTENSIONS.get(content_type) or mimetypes.guess_extension(content_type) or ""
    return f"{uuid.uuid4().hex}{ext}"


def save_bytes(uploads_dir: str, filename: str, content: bytes) -> str:
    os.makedirs(uploads_dir, exist_ok=True)
    path = os.path.join(uploads_dir, filename)
    with open(path, "wb") as f:
        f.write(content)
    return path
