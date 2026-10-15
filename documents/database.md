# Database — PostgreSQL

Đây là schema mục tiêu. Đã có ORM Base, async engine/session factory và Alembic environment; đã có 5 models nghiệp vụ và revision `0001`; cần chạy migration để tạo bảng. Health ready hiện chỉ kiểm tra kết nối, chưa kiểm tra schema head. Dùng SQLAlchemy 2.x ORM và Alembic để quản lý migration có version. Chỉ storage truy cập DB. API contract tại [api.md](api.md).

## Quy ước SQLAlchemy và schema

- Một DeclarativeBase/MetaData dùng chung tại app/storage/models/base.py; các models trong app/storage/models/ dùng Mapped/mapped_column và được import đầy đủ qua models/__init__.py. Models là định nghĩa schema trong code; Alembic migrations là lịch sử thay đổi DB. Tài liệu này mô tả contract, phải cập nhật cùng models/migrations.
- app/storage/session.py quản lý AsyncEngine, async_sessionmaker và AsyncSession với driver PostgreSQL async tương thích. Mỗi process có engine/pool riêng; không tạo engine mỗi request, dispose khi shutdown.
- Mỗi request hoặc thao tác DB trong worker có session riêng; heartbeat chạy đồng thời phải dùng session riêng. Không chia sẻ AsyncSession giữa concurrent tasks hoặc truyền session từ API sang worker.
- Storage xác định transaction cho toàn thao tác nghiệp vụ; dùng context manager để commit/rollback và đóng session. Helper bên trong không tự commit làm vỡ transaction. Không giữ session có transaction mở trong lúc OCR/gọi model.
- Bảng/cột dùng snake_case, tên bảng số nhiều. Khai báo tường minh nullable, server defaults cần thiết, FK, UNIQUE, CHECK và index trong models; ràng buộc quan trọng không chỉ nằm ở Pydantic.
- Status dùng TEXT với CHECK constraint có tên; dùng một tập giá trị dùng chung ở code validation và models. Dùng naming convention ổn định cho constraint/index để migration nhất quán.
- Cột DB tên metadata được map thành thuộc tính Python khác như metadata_ bằng mapped_column("metadata", ...), tránh trùng Base.metadata. API vẫn dùng tên metadata.
- Alembic env.py import đủ models, target_metadata=Base.metadata. Autogenerate chỉ tạo bản nháp: phải đọc lại rename, defaults, constraints và thao tác dữ liệu trước khi áp dụng.
- Chạy migration như bước deploy riêng, không để mỗi API/worker tự migrate hoặc gọi create_all khi startup.
- Test migration trên PostgreSQL: tạo DB mới đến head, nâng cấp từ revision trước và kiểm tra schema không lệch models. Migration mất dữ liệu cần kế hoạch bảo toàn/phục hồi, không tự downgrade dữ liệu sản xuất.

## Bảng users

| Cột | Kiểu / ràng buộc |
| --- | --- |
| id | UUID PK; ID nội bộ dùng cho mọi owner_id |
| password_hash | TEXT NOT NULL; Argon2id hash, không public |
| email | TEXT NOT NULL UNIQUE; trim/lowercase trước lưu/login; CHECK email = lower(btrim(email)) và không rỗng |
| display_name | TEXT nullable |
| is_active | BOOLEAN NOT NULL DEFAULT true |
| created_at, updated_at | TIMESTAMPTZ NOT NULL DEFAULT now(); updated_at cập nhật mỗi lần ghi |

Đăng nhập email/password, JWT access token theo [auth.md](auth.md). users.id là JWT sub; không lưu issuer/subject của nhà cung cấp ngoài trong MVP.

User inactive bị chặn khỏi API nghiệp vụ. Không hard-delete user bằng cascade âm thầm: documents tham chiếu RESTRICT; luồng xóa dữ liệu cá nhân chưa thuộc API MVP. Không thêm bảng roles/permissions khi chỉ có quyền sở hữu cá nhân.

Không lưu JWT hoặc trạng thái phiên đăng nhập. auth_rate_limits chỉ lưu counter chống lạm dụng.

## Bảng auth_rate_limits

| Cột | Kiểu / ràng buộc |
| --- | --- |
| key_hash | TEXT PK; SHA-256 của loại khóa + IP/email; không chứa token hoặc phiên đăng nhập |
| window_started_at | TIMESTAMPTZ NOT NULL, có index để cleanup |
| attempts | INTEGER NOT NULL CHECK > 0 |

UPSERT atomic tăng số lần trong cửa sổ cố định; quá hạn thì reset. Commit counter trước khi trả 429 để giới hạn nhất quán qua nhiều process. Entries quá hai cửa sổ được dọn khi có request auth/retry. Retry tài liệu dùng khóa hash `document_retry:owner_id`, cùng độ dài cửa sổ auth để cleanup nhất quán. Không chứa FK vì khóa IP/email có thể chưa thuộc tài khoản nào.

## Bảng documents

| Cột | Kiểu / ràng buộc |
| --- | --- |
| id | UUID PK |
| owner_id | UUID NOT NULL FK users(id), ON DELETE RESTRICT |
| filename, content_hash, idempotency_key | TEXT NOT NULL; UNIQUE(owner_id, idempotency_key) |
| status | queued / processing / completed / failed; trạng thái chunk |
| source_key, source_expires_at | TEXT nullable, TIMESTAMPTZ nullable; key không public |
| page_count, chunk_count | INTEGER không âm; page_count nullable trước parse |
| processing_config | JSONB: OCR, tokenizer, chunk config/version và semantic embedding provider/model/version nếu dùng |
| metadata | JSONB: block → trang và metadata tài liệu |
| warnings, quality_summary | JSONB, mặc định mảng rỗng/object rỗng tương ứng |
| created_at, updated_at | TIMESTAMPTZ NOT NULL |

source_available suy ra từ thông tin nguồn và việc file còn tồn tại; không lưu boolean này thành nguồn trạng thái độc lập.

## Bảng document_chunks

| Cột | Kiểu / ràng buộc |
| --- | --- |
| id | UUID PK |
| document_id | UUID FK documents(id), ON DELETE CASCADE |
| chunk_index | INTEGER >= 0; UNIQUE(document_id, chunk_index) |
| content | TEXT NOT NULL, không rỗng |
| token_count | INTEGER > 0 |
| metadata | JSONB NOT NULL: page_numbers, source_block_ids, source_spans, section_path, block_type, strategy, strategy_reason, source_methods, quality_flags |

Trang từ 1, chunk_index từ 0. Source span là block_id và khoảng ký tự text block chuẩn hóa. Completed chunks bất biến trong MVP. Embedding tạm của semantic chunking không lưu vào DB.

## Bảng processing_jobs

| Cột | Kiểu / ràng buộc |
| --- | --- |
| id | UUID PK |
| document_id | UUID NOT NULL UNIQUE FK documents(id), ON DELETE CASCADE |
| status | queued / processing / succeeded / failed |
| stage | TEXT nullable, bước thực tế |
| attempt_count | INTEGER >= 0 |
| lease_token, lease_expires_at | UUID nullable, TIMESTAMPTZ nullable |
| next_attempt_at | TIMESTAMPTZ NOT NULL |
| error_code, error_message | TEXT nullable, thông tin đã làm sạch |
| retryable | BOOLEAN NOT NULL |
| created_at, updated_at | TIMESTAMPTZ NOT NULL |

Một hàng cho mỗi tài liệu; retry cập nhật hàng cũ. Job là nguồn thông tin attempt/lease/error; document giữ trạng thái sản phẩm. Cập nhật trạng thái hai bảng trong cùng transaction, không để chúng lệch nhau.

## Quan hệ tổng quát

users 1–N documents; documents 1–N document_chunks; documents 1–1 processing_jobs.

## Transaction và index

- Upload: file đã lưu bền vững → transaction tạo document queued + job ingestion queued. Dọn file mồ côi khi DB thất bại; chi tiết [workflows.md](workflows.md).
- Claim: khóa job khả dụng, kiểm tra điều kiện document, đặt processing/lease và tăng attempt_count atomic; commit trước công việc dài.
- Ingestion thành công: kiểm tra lease → insert toàn bộ chunks → document completed → job succeeded → commit.
- Fail/requeue: cập nhật job và trạng thái tương ứng trên document atomic.
- Heartbeat/commit có điều kiện lease_token và lease_expires_at; worker đã mất lease không được ghi. Lease hết hạn được thu hồi có giới hạn retry.
- Index documents(owner_id), FK document_chunks(document_id), jobs(status, next_attempt_at), jobs(lease_expires_at) cho job processing. UNIQUE index chunks đã hỗ trợ prefix document_id; không tạo index trùng nếu không cần.
- Queries parameterized. Mọi đọc tài liệu/chunks/jobs từ API phải giới hạn owner.
- Advisory transaction lock 714032: upload shared, cleanup orphan exclusive. File save
  và enqueue dùng cùng session/transaction của guard; không cần thêm bảng/migration.
- Worker dùng clock_timestamp() khi kiểm tra lease, kiểm tra lại sau flush chunks trước
  publish completed; không dùng thời điểm bắt đầu transaction làm thời gian hiện tại.

## Chi tiết triển khai models

UUID được sinh bằng uuid4 phía SQLAlchemy; SQL thô phải cung cấp ID. Status dùng
DocumentStatus/JobStatus (Literal) chung với CHECK. Không khai báo ORM relationships
khi chưa có caller; quan hệ được bảo đảm bằng FK/UNIQUE.

created_at/updated_at mặc định now(); updated_at dùng SQLAlchemy onupdate=now(),
SQL thô/UPSERT phải gán updated_at rõ ràng. Document/job mặc định queued;
chunk_count/attempt_count mặc định 0, next_attempt_at mặc định now(), retryable false.
processing_config và metadata tài liệu bắt buộc cung cấp; page_count nullable.
