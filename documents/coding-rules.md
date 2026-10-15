# Coding rules

## Phạm vi và thiết kế

- Đọc mã, callers, schemas, config và tests trước khi sửa. Cấu trúc mục tiêu tại [project-structure.md](project-structure.md); không tạo lớp/file rỗng để đủ cây.
- Đổi contract phải cập nhật callers, tests và tài liệu nguồn liên quan. Không nhân bản quy tắc chi tiết trong AGENTS.md.
- Dùng helper/thư viện sẵn có; ưu tiên code nhỏ, rõ. Không thêm factory, wrapper, framework, dependency chỉ cho nhu cầu giả định.
- Python dùng type hints ở giao diện; Pydantic validation tại biên API/config. State/Block/Chunk có type rõ; không truyền dict tùy ý giữa các bước.
- Tên hàm/biến diễn đạt trách nhiệm; comment giải thích lý do, không chép lại code. Hạn chế có chủ đích ghi `shortcut: <giới hạn>, <khi cần nâng cấp>`.

## I/O, transaction và lỗi

- Routes mỏng; OCR/parse chạy trong worker, không chặn event loop API. I/O async dùng nhất quán; tác vụ CPU có giới hạn tiến trình/tài nguyên.
- Chỉ storage truy cập DB qua SQLAlchemy 2.x. Dùng expressions/bind parameters; Alembic migration có version. Models, migration và database.md cập nhật cùng nhau khi đổi schema; tuân thủ session/transaction tại database.md.
- Không giữ transaction/khóa DB khi OCR/gọi provider. Tuân thủ claim/lease/atomic commit tại [database.md](database.md).
- Retry chỉ lỗi tạm thời với backoff, max attempts và deadline; không nuốt exception hoặc trả kết quả thành công giả. Phân biệt lỗi dữ liệu, phụ thuộc và lỗi nội bộ.
- Cleanup file không được xóa nguồn của job đang chạy. Cần kiểm tra phạm vi đường dẫn trước thao tác xóa; không ghép shell command từ input.

## Bảo mật và cấu hình

- Xác thực danh tính trước truy cập; kiểm tra owner cho trạng thái/chunks/retry. Client không tự chọn owner_id đáng tin cậy.
- ENV quản lý DSN, credentials, OCR, tokenizer, chunk budgets, semantic embedding provider/model và giới hạn input, upload/page/chunk limits, worker concurrency, lease/heartbeat, retry/deadline và retention.
- Validate quan hệ cấu hình khi startup; giá trị chưa chốt phải nêu rõ. Không hardcode credentials/provider vào business logic.
- `.env.example` không bí mật. Không commit `.env`, file người dùng hoặc logs riêng tư; không ghi DSN/key/nội dung PDF vào lỗi HTTP.
- Parser/OCR có giới hạn tài nguyên. Trước triển khai, ghi rõ text/ảnh gửi ra provider ngoài; không bật tracing toàn văn mặc định.

## Quan sát và kiểm thử

- Log request_id/document_id/job_id, attempt, stage, thời gian chờ/OCR/DB/provider, số trang/chunks, strategy/fallback và lỗi đã làm sạch.
- LangChain callbacks ghi bước LangChain và usage khi provider cung cấp; không bịa token count thiếu. DB/OCR/worker có instrumentation cùng correlation IDs. LangSmith tùy chọn.
- Logic không tầm thường cần test chứng minh hành vi; không thêm test chỉ phản chiếu implementation. Danh mục bắt buộc tại [testing.md](testing.md).
- Thay đổi tài liệu: rà liên kết, trạng thái, API/schema và tham chiếu chéo. Không tuyên bố test runtime đã chạy nếu mới chỉnh đặc tả.
- Kết quả công việc nêu thay đổi, kiểm chứng và giới hạn còn lại. Không mở rộng phạm vi ngoài yêu cầu.


- ORM models đặt trong storage/models/, dùng một Base từ base.py; schemas/requests và schemas/responses là Pydantic riêng. Cấu hình DB đọc ở config.py, khởi tạo kết nối tại storage/session.py. Responses phải whitelist trường public; không lộ password_hash, source_key hoặc secrets.
- Quyền tài liệu dựa users.id; kiểm tra active và ownership khi đọc trạng thái/chunks hoặc retry.

- Xác thực bắt buộc theo [auth.md](auth.md): Bearer JWT access token hợp lệ, user tồn tại/active; không lưu token hoặc phiên đăng nhập. Không log Authorization, password hoặc token; không tự triển khai mật mã.
