# Chunking PDF

Khung FastAPI xử lý PDF bất đồng bộ. Mục tiêu: upload → OCR → adaptive chunking → API chunks phân trang, với JWT access token và PostgreSQL. Auth mục tiêu không lưu token/phiên, không dùng refresh token; hết hạn đăng nhập lại, logout chỉ xóa token phía client.

## Hiện có

- App factory, Swagger, `/health/live`, `/health/ready`.
- Settings ENV/`.env`, kiểm tra DSN/timeouts và che credentials.
- Async SQLAlchemy engine/session factory, ORM Base, naming convention.
- 5 ORM models nghiệp vụ; Alembic revision `0001` tạo schema PostgreSQL (cần chạy migration).
- Response chung `{statusCode, data}`; register/login/logout và `/api/v1/users/me`.
- HTTPBearer/Swagger Authorize, JWT HS256, Argon2id, body limit, PostgreSQL rate limit.
- Worker độc lập: claim/lease/heartbeat, backoff, deadline và giới hạn RAM tiến trình con.
- Upload PDF + GET status có kiểm tra owner; file bền vững, hash/idempotency,
  document + job queued cùng transaction. State/Block/Chunk có type; snapshot config.
- Unit tests, PostgreSQL integration test tùy chọn, Ruff.
- Parse pypdf + render PDFium + Tesseract vie/eng local; không gửi PDF ra provider.
- Adaptive chunking theo block, tiktoken, source spans/coverage, repair giới hạn.
- Chunks phân trang, retry có kiểm tra owner; retention và dọn file mồ côi có khóa upload.

Đã có luồng upload → worker → OCR/chunks → API; cần chạy worker riêng và cài Tesseract
cùng dữ liệu vie/eng. Semantic embedding chưa cấu hình: recursive fallback có lý do.
Nhận diện heading/bảng/cột là heuristic; thứ tự text/ảnh trên trang hỗn hợp còn gần đúng,
có warnings. Coverage đo trên text sau trích xuất/chuẩn hóa, không chứng minh OCR đọc đủ ảnh.
Chưa tuyên bố đạt chất lượng MVP trên bộ PDF đại diện hoặc tải production.
Logout yêu cầu client xóa token; bản sao JWT còn hiệu lực đến exp. Không có chat/hỏi đáp.
Readiness hiện chỉ kiểm tra kết nối DB, chưa xác nhận schema hoặc khả năng xử lý PDF.

## Chạy local

Dùng `venv` hiện có, xem lệnh cài đặt/cấu hình tại [hướng dẫn chạy](documents/run-project.md).

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
.\venv\Scripts\python.exe -m app.worker --check
.\venv\Scripts\python.exe -m app.worker
.\venv\Scripts\python.exe -m pytest -q
.\venv\Scripts\python.exe -m ruff check app tests migrations
.\venv\Scripts\python.exe -m ruff format --check app tests migrations
.\venv\Scripts\python.exe -m pip check
```

API cần DATABASE_URL và JWT_SECRET hợp lệ trong ENV/`.env`; DB chưa chạy vẫn dùng được liveness. Auth cần schema đã migrate.
Swagger: [localhost:8000/docs](http://localhost:8000/docs). Không tự tạo DB/migrate.

## Dependencies

`pyproject.toml`: dependencies trực tiếp; `requirements.lock.txt`: snapshot pin phiên bản,
gồm dev tools, đã resolve trên Windows/Python 3.14.6. Project khai báo Python 3.12–3.14;
OS/Python khác cần resolve và kiểm thử lại. Khi đổi dependencies, cài `-e '.[dev]'`,
kiểm thử rồi cập nhật snapshot bằng `pip freeze --exclude-editable`.

- FastAPI/Uvicorn, Pydantic/Settings, multipart: API/config/upload.
- SQLAlchemy/asyncpg/Alembic: PostgreSQL async và migrations.
- LangChain Core: Runnables điều phối pipeline; callback chỉ log ID/timing, tracing ngoài tắt.
- pypdf, PDFium, Pillow: đọc text/render; Tesseract CLI: OCR local; tiktoken: token budget.
- psutil: giám sát RAM và dừng cây tiến trình khi hết deadline/mất lease.
- PyJWT/argon2-cffi: ký/verify access token và hash/verify mật khẩu.
- pytest/pytest-asyncio/httpx/Ruff: kiểm thử/lint.

Không có SDK embedding provider hoặc pgvector; không lưu vectors. Tesseract/language data
là dependencies hệ thống riêng, không nằm trong pip lock.

Đặc tả: [cấu trúc mục tiêu](documents/project-structure.md), [API](documents/api.md).
