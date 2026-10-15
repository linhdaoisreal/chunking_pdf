# Chạy project local

Hiện có auth/profile, upload/status/chunks/retry, worker PostgreSQL và pipeline OCR/chunk
local. Có 5 ORM models, revision `0001`; thay đổi ingestion không cần migration mới.
Giới hạn chất lượng tại [README](../README.md).

## 1. Môi trường và dependencies

PowerShell tại thư mục dự án, dùng `venv` hiện có, không cần activate:

```powershell
Set-Location D:\self_source\chunking_pdf
.\venv\Scripts\python.exe --version
.\venv\Scripts\python.exe -m pip check
```

Máy mới chưa có venv: `python -m venv venv`. Cài snapshot Windows/Python 3.14.6:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\venv\Scripts\python.exe -m pip install --no-deps -e .
```

Python khai báo 3.12–3.14; nền tảng khác resolve bằng `pip install -e '.[dev]'`
rồi kiểm thử. Tesseract là dependency hệ thống; tokenizer nằm trong pip dependencies.

### OCR local

Cài Tesseract 5 và language data `vie`, `eng`. Trên Windows:

```powershell
winget install --id UB-Mannheim.TesseractOCR --exact --source winget
```

Binary Windows theo [hướng dẫn Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html).
Dữ liệu tiếng Việt từ [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast).
Ví dụ thư mục riêng trong project (không ghi đè dữ liệu đã tùy chỉnh):

```powershell
New-Item -ItemType Directory -Force data/tessdata/configs | Out-Null
Copy-Item 'C:\Program Files\Tesseract-OCR\tessdata\eng.traineddata' data/tessdata/eng.traineddata
Copy-Item 'C:\Program Files\Tesseract-OCR\tessdata\configs\tsv' data/tessdata/configs/tsv
Invoke-WebRequest 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/vie.traineddata' -OutFile data/tessdata/vie.traineddata
```

Thêm vào `.env`, điều chỉnh đường dẫn theo máy:

```dotenv
TESSERACT_COMMAND=C:/Program Files/Tesseract-OCR/tesseract.exe
TESSERACT_DATA_DIR=D:/self_source/chunking_pdf/data/tessdata
OCR_ENGINE=tesseract
OCR_LANGUAGES=vie+eng
TOKENIZER_NAME=cl100k_base
```

Lần đầu tiktoken có thể tải bảng tokenizer vào cache; không gửi nội dung PDF.
Máy offline cần warm cache trước bằng `python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"`.
Không bật LangSmith tracing toàn văn; ingestion tắt tracing ngoài và chỉ log ID/timing.

## 2. Cấu hình

Không ghi đè `.env` đang có:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

Sửa DATABASE_URL theo PostgreSQL của bạn; URL-encode ký tự đặc biệt trong credentials.
Ví dụ minh họa, không chứa credentials thật:

```dotenv
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@localhost:5432/chunking_pdf
DATABASE_POOL_SIZE=5
DATABASE_POOL_TIMEOUT=5
DATABASE_CONNECT_TIMEOUT=5
DATABASE_COMMAND_TIMEOUT=10
```

Pool tối đa 5 kết nối/process mặc định, không overflow; timeout tính bằng giây.
API bắt buộc JWT_SECRET ít nhất 32 byte, ngẫu nhiên; không có secret mặc định. Sinh một lần:

```powershell
.\venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

Lưu kết quả vào JWT_SECRET trong `.env`; giữ ổn định giữa các lần restart và giống nhau
trên các API instances. Không commit `.env`. Không dùng chuỗi ví dụ làm secret thật.
Cấu hình còn lại:

```dotenv
JWT_ISSUER=chunking-pdf
JWT_AUDIENCE=chunking-pdf-api
JWT_ACCESS_TTL_SECONDS=900
AUTH_RATE_WINDOW_SECONDS=60
AUTH_IP_LIMIT=60
AUTH_IDENTITY_LIMIT=10
```

Migration/worker không bắt buộc JWT_SECRET nếu biến không được khai báo; nếu khai báo,
giá trị phải hợp lệ. Đo chi phí Argon2id trên máy deploy trước khi tăng tải.

## 3. API

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

- [Swagger](http://127.0.0.1:8000/docs).
- [Liveness](http://127.0.0.1:8000/health/live): 200, `{"statusCode":200,"data":{"status":"ok"}}`, không truy cập DB.
- [Readiness](http://127.0.0.1:8000/health/ready): 200 khi SELECT 1 thành công;
  503, `{"statusCode":503,"data":{"status":"unavailable"}}` khi lỗi DB/timeout. Chưa kiểm tra migration head.

API cần DATABASE_URL và JWT_SECRET hợp lệ; DB chưa chạy không chặn liveness.
Migrate trước khi dùng auth. Trên Swagger:

1. POST /api/v1/auth/register: email, password 6–128 ký tự, display_name tùy chọn.
2. POST /api/v1/auth/login: lấy data.access_token.
3. Authorize: dán token (không thêm chữ Bearer); gọi GET /api/v1/users/me.
4. POST /api/v1/auth/logout: nhận hướng dẫn rồi client xóa token; Swagger phải bấm Logout
   trong hộp Authorize để ngừng gửi token. Bản sao token vẫn hợp lệ đến exp.

Không có refresh token. Dừng bằng Ctrl+C.

Sau migrate, Authorize rồi POST `/api/v1/documents/upload`: chọn trường `file` PDF,
nhập `Idempotency-Key` (1–200 ký tự ASCII hiển thị, không khoảng trắng).
Nhận `202`, gọi GET theo `Location` để xem status. Cùng key/file trả document cũ;
key khác nội dung trả `409`. Chạy worker ở terminal riêng. Poll status có backoff;
completed thì GET chunks_url với limit/offset, failed thì đọc processing_error/warnings.
POST retry chỉ dùng khi retryable và nguồn còn tồn tại.

`PDF_STORAGE_DIR` mặc định `data/pdfs`, tính từ cwd. Deploy dùng đường dẫn tuyệt đối
đến persistent volume chung API/worker; không đặt trong thư mục tạm.
Giới hạn tạm: file 20 MiB, tổng multipart thêm tối đa 64 KiB overhead, truyền 120 giây.
Các giá trị trong `.env.example` là mặc định local, chưa phải giới hạn vận hành đã chốt.
OCR_ENGINE=tesseract và TOKENIZER_NAME=cl100k_base đã chọn; config khác chưa hỗ trợ.
Snapshot config lưu lúc upload; kết quả bổ sung version thực tế. Job cũ có engine/tokenizer
null được resolve sang defaults local. Retry giữ snapshot, không âm thầm đổi chunk budgets.
Giới hạn mặc định: 500 trang, 10.000 chunks, 5 triệu ký tự, 25 triệu pixels/render,
OCR 120 giây, cả lượt 900 giây, RAM cây tiến trình 1024 MiB (đo mỗi 100 ms).

File thất bại/replay được dọn ngay nếu an toàn. Khi kết quả commit không rõ và DB vẫn lỗi,
giữ file + log `Source cleanup deferred` để tránh mất nguồn đã commit.
Worker dọn mỗi 60 giây: nguồn completed sau 1 ngày, failed sau 7 ngày;
file mồ côi UUID sau 1 giờ, có khóa chống đua upload. Không xóa tay nguồn đang xử lý.

## 4. PostgreSQL và Alembic

PostgreSQL server chuẩn bị riêng; không cần pgvector. Không tự tạo DB/migrate lúc startup.
Đã có revision `0001`. Cấu hình DATABASE_URL trỏ DB đã tạo, xem SQL rồi migrate:

```powershell
.\venv\Scripts\python.exe -m alembic heads
.\venv\Scripts\python.exe -m alembic upgrade head --sql
.\venv\Scripts\python.exe -m alembic upgrade head
.\venv\Scripts\python.exe -m alembic current
.\venv\Scripts\python.exe -m alembic check
```

Revision có sẵn; không cần autogenerate lần đầu. `upgrade head --sql` chỉ xuất SQL;
`upgrade head` tạo bảng. Kiểm thử trên DB riêng trước deploy.
Lần sau đổi models: `alembic revision --autogenerate -m "describe change"`, review rồi upgrade.

## 5. Worker

```powershell
.\venv\Scripts\python.exe -m app.worker --check
.\venv\Scripts\python.exe -m app.worker
# Kiểm thử/vận hành một lượt: tối đa một job/slot, không chạy cleanup định kỳ.
.\venv\Scripts\python.exe -m app.worker --once
```

`--check` kiểm tra DB, Tesseract/languages và tokenizer, thoát 0 khi thành công, 1 khi lỗi;
chưa kiểm tra migration head. Mặc định worker chạy liên tục, concurrency=1.
Chạy nhiều workers sẽ xử lý song song; muốn tuần tự toàn hệ thống chỉ chạy một worker/slot.
Ctrl+C dừng tiến trình con; job processing còn trong DB sẽ được thu hồi sau lease hết hạn.

## 6. Kiểm thử

```powershell
.\venv\Scripts\python.exe -m pytest -q
.\venv\Scripts\python.exe -m ruff check app tests migrations
.\venv\Scripts\python.exe -m ruff format --check app tests migrations
.\venv\Scripts\python.exe -m pip check
```

Integration test skip khi chưa có TEST_DATABASE_URL. Khi có PostgreSQL test:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+asyncpg://USER:PASSWORD@localhost:5432/chunking_pdf_test'
$env:TESSERACT_COMMAND = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
$env:TESSERACT_DATA_DIR = 'D:\self_source\chunking_pdf\data\tessdata'
.\venv\Scripts\python.exe -m pytest tests/integration -q
Remove-Item Env:TEST_DATABASE_URL
```

TEST_DATABASE_URL phải trỏ DB test rỗng. Test schema tạo bảng trong transaction,
kiểm tra drift/constraints rồi rollback. Tests auth tạo schema auth_test_<UUID>,
kiểm tra API/đăng ký đồng thời/rate limit rồi xóa schema test. User DB test cần quyền CREATE SCHEMA.
Không dùng DB production. Tests ingestion tạo schema riêng, kiểm tra OCR/worker thật,
lease/retry/retention và lỗi subprocess. Tests OCR cần Tesseract/lang data thực tế;
fixtures PDF được sinh tại thư mục tạm, không dùng tài liệu riêng tư.
tiêu chí MVP tại [testing.md](testing.md).

Nếu Python báo `Failed to find real location ...Python314...`, kiểm tra đường dẫn Python
hệ thống; tạo lại venv khi interpreter không tồn tại. Nếu asyncio treo trong sandbox Windows,
chạy tests từ PowerShell local trước khi kết luận code lỗi.


## 7. Theo dõi upload và xử lý PDF

Restart API và worker sau khi cập nhật mã. Log INFO xuất ra terminal của từng process,
kèm thời gian. Logger `app` có handler riêng, không phụ thuộc root logger của Uvicorn/IDE.
Không cần bật DEBUG hoặc tracing toàn văn. Worker báo `Worker starting` khi kiểm tra
phụ thuộc và `Worker ready` khi đã sẵn sàng nhận job. Nếu chỉ thấy access log `202`,
restart API bằng mã mới; mở terminal thứ hai chạy `python -m app.worker` trong cùng
venv/cwd/cấu hình DB. API không tự chạy worker; job đã queued sẽ được worker nhận,
không cần upload lại.

- Terminal API: `Upload started` → `Upload receiving` (bytes multipart, tối đa một lần
  mỗi 5 giây khi có dữ liệu đến) → `Upload multipart parsed` → `Upload waiting for storage`
  → `Upload saving file` → `Upload file saved; enqueueing` → `Upload accepted`
  → `Upload response`. `accepted` xuất sau transaction, chứa document ID và created
  để phân biệt upload mới/replay. Bytes gồm multipart overhead, không phải kích thước PDF.
- Khi lỗi: tìm `request=` bằng `X-Request-ID` hoặc `data.request_id` của response.
  `Upload multipart rejected` có reason: `missing_boundary`, `too_many_files`,
  `unexpected_text_field`, `missing_field_name`, `invalid_multipart`, hoặc mã timeout/limit.
  `unexpected_text_field`: request chứa trường Text ngoài file; chỉ gửi một field File tên `file`.
  Lỗi khác có `Upload rejected` với status/code. Response API giữ contract cũ.
- Terminal worker: `Document claimed` chứa document/job/attempt; `Worker stage` và
  `Worker active` cho biết stage và thời gian từ lúc khởi chạy subprocess.
  `PDF page started/completed` cho biết trang hiện tại/tổng trang; OCR có started/completed
  và confidence; `Chunking progress` cho biết block đã xử lý/tổng block và số chunks tạm.
  `Chunk evaluation` là bước kiểm tra coverage/budget; `Saving chunks` là bắt đầu lưu DB;
  chỉ `Document completed` mới xác nhận commit thành công. Số chunks có thể giảm sau repair.
- Trong Postman: gọi `GET /api/v1/documents/{{document_id}}` với Bearer token; xem
  `data.status`, `data.stage`, `data.processing_error`, `data.retryable` và `data.warnings`.
  Trang/block chi tiết chỉ nằm trong log, không phải phần trăm hoàn thành trong API.
  `queued` chưa được worker nhận; `processing` đang chạy; `completed` mới lấy chunks.
  Lỗi tạm thời có thể tự requeue; kiểm tra status sau log lỗi để biết trạng thái bền vững.

Log ứng dụng chỉ ghi IDs, số lượng, timing và mã lỗi; không ghi password/token,
nội dung PDF, tên file người dùng hay raw exception. Không gửi toàn bộ headers/body để debug.
