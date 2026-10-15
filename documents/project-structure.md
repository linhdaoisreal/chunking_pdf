# Cấu trúc project

Cây dưới đây là cấu trúc mục tiêu. Khung, models, revision `0001`, auth đã có.
Upload/status có tại api/documents.py, storage/documents.py, services/files.py,
schemas/responses/documents.py; pipelines/state.py định nghĩa dữ liệu nội bộ.
Đã có pipeline ingestion, services/pdf.py + chunking.py, storage/jobs.py + maintenance.py
và worker handlers. Semantic provider chưa có; xem [README.md](../README.md).
Không tạo file rỗng để đủ cây.

```text
AGENTS.md
 documents/                    # Đặc tả auth, DB, API, workflows, chunking, coding, tests
 app/
   main.py                     # FastAPI lifespan, đăng ký routes
   errors.py                   # Auth domain errors, ánh xạ tại API boundary
   config.py                   # Settings ENV, gồm database_url và pool/timeouts
   api/
     health.py                 # Liveness và DB connectivity
     dependencies.py           # Verify bearer JWT, current user, DB scope
     auth.py                   # Register, login, logout
     auth_body.py              # Request ID, no-store, IP limit, giới hạn body trước parse JSON
     error_handlers.py         # Lỗi HTTP/validation/DB dùng envelope chung
     documents.py              # Upload, status, chunks, retry
     users.py                  # Hồ sơ người dùng hiện tại
   schemas/                    # Pydantic, không phải ORM tables
     requests/
       auth.py                 # RegisterRequest, LoginRequest
       documents.py            # Validation upload, pagination parameters
     responses/
       auth.py                 # TokenResponse
       common.py               # ApiResponse, ErrorData, StatusData
       users.py                # UserResponse, không lộ auth secrets
       documents.py            # UploadResponse, DocumentResponse, ChunkPageResponse
     shared.py                 # Chunk dùng chung trong responses
   pipelines/
     state.py                  # State/Block/Chunk nội bộ có type
     ingestion.py              # Parse → chunk → commit
   services/
     auth.py                   # Password hashing, JWT signing/verification
     pdf.py                    # Parser/OCR/source mapping
     chunking.py                # Chọn, đánh giá và sửa chiến lược
     embeddings.py             # Embedding tạm cho semantic splitting
     files.py                  # File nguồn bền vững, retention, cleanup
   storage/
     session.py                # create_async_engine, async_sessionmaker, scope session
     models/
       base.py                 # DeclarativeBase + naming convention duy nhất
       users.py                # User
       rate_limits.py          # AuthRateLimit
       documents.py            # Document, DocumentChunk
       jobs.py                 # ProcessingJob
       __init__.py             # Import/export đủ models cho Alembic metadata
     users.py                  # Tìm user theo email/id, tạo user
     rate_limits.py            # PostgreSQL counter dùng chung nhiều process
     documents.py              # Owner, metadata, chunks
     jobs.py                   # Claim/lease/retry
     maintenance.py            # Retention + đối soát file mồ côi, khóa với upload
   worker.py                   # Entry point riêng, dispatch jobs/heartbeat
 migrations/
   env.py                      # Đọc config, import models, target_metadata=Base.metadata
   versions/                   # Migration tables, constraints, schema có version
 tests/
   unit/
   integration/
   fixtures/                   # PDF nhỏ, không chứa dữ liệu riêng tư
 alembic.ini                    # Cấu hình Alembic, không chứa DSN/credentials
 pyproject.toml                 # Dependencies và tool config
 .env.example                  # Tên biến và giá trị mẫu không bí mật
```

## Ranh giới trách nhiệm

- config.py chỉ đọc/validate ENV. storage/session.py là nơi duy nhất khởi tạo engine/session factory; models/base.py không mở kết nối. API và worker mỗi process có engine riêng, dùng chung cấu hình.
- api/dependencies.py lấy danh tính đã xác thực, xác minh JWT qua services/auth.py và tra user active theo sub qua storage/users.py và cấp session qua factory; không chứa query nghiệp vụ. Storage là nơi truy vấn DB.
- Pydantic requests kiểm tra input, responses định nghĩa dữ liệu public. ORM models mô tả bảng; không trả thẳng ORM object chứa dữ liệu nội bộ, không dùng ORM model làm request schema.
- Routes mỏng, không SQL/OCR. Pipeline điều phối State qua Runnables; helper cùng trách nhiệm được gọi bình thường.
- Storage thao tác nhiều bảng trong một transaction khi nghiệp vụ yêu cầu; helper không tự commit. Không giữ transaction khi gọi model.
- API/worker dùng persistent volume chung cho nguồn PDF trong MVP; PostgreSQL lưu job bền vững. Không thêm broker khi chưa cần.

Cột/ràng buộc: [database.md](database.md). Hợp đồng request/response: [api.md](api.md). Quy tắc code/session: [coding-rules.md](coding-rules.md).


Auth contract tại [auth.md](auth.md): services/auth.py không tự query DB; đọc/ghi tài khoản qua storage/users.py, không lưu token/phiên. config.py chứa JWT issuer/audience/secret, access TTL, auth rate limits; không hardcode key.
