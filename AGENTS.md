# AGENTS.md — Hướng dẫn làm việc với dự án

## Phạm vi đã chốt

Dịch vụ FastAPI + LangChain + PostgreSQL, dùng SQLAlchemy ORM và Alembic migrations: upload PDF bất đồng bộ, OCR, adaptive chunking, lấy chunks qua API phân trang trên tài liệu thuộc người dùng; lưu users. Phạm vi chỉ xử lý PDF/chunks, không có chat, hội thoại, tin nhắn hoặc hỏi đáp.

Upload trả 202 sau khi lưu file và công việc bền vững. Worker xử lý độc lập từng tài liệu; mặc định một worker/một slot. Chunks hoàn tất được đọc ngay. Embedding chỉ dùng tùy chọn trong semantic chunking, không lưu vector phục vụ truy xuất. OCR có trong MVP. Provider/model cấu hình qua ENV, chưa chốt nhà cung cấp. Xác thực bằng JWT access token trong header Bearer, không lưu token/phiên, không dùng refresh token; hết hạn phải đăng nhập lại, API logout xác nhận yêu cầu; client xóa token, JWT đã cấp không bị thu hồi.

Đặc tả trong documents là mục tiêu. Source có auth, upload/status/chunks/retry, worker PostgreSQL, pipeline OCR/chunk local và retention. Trạng thái/giới hạn thực tế tại [README.md](README.md); hướng dẫn chạy tại [documents/run-project.md](documents/run-project.md). Không coi chất lượng MVP đã được xác nhận trên corpus đại diện.

## Đọc quy tắc theo công việc

Trước sửa, đọc tài liệu tương ứng và mã/callers/tests liên quan. Không cần đọc toàn bộ đặc tả cho thay đổi không liên quan.

| Công việc | Tài liệu bắt buộc |
| --- | --- |
| Chạy project local, migration, healthcheck | [Hướng dẫn chạy](documents/run-project.md) |
| Tìm hiểu cấu trúc, thêm module, xác định trách nhiệm | [Cấu trúc project](documents/project-structure.md) |
| Viết/sửa mã, cấu hình, bảo mật, logging | [Coding rules](documents/coding-rules.md) |
| Đăng ký/đăng nhập, JWT, logout phía client | [Auth](documents/auth.md) + [Database](documents/database.md) |
| DB, ORM, migrations, hàng đợi | [Database](documents/database.md) |
| Upload, worker, retry, retention, trạng thái | [Workflows](documents/workflows.md) + [Database](documents/database.md) |
| OCR, chọn/sửa chiến lược chunk | [Chunking](documents/chunking.md) + [Workflows](documents/workflows.md) |
| Endpoint, request/response, phân trang | [API](documents/api.md) |
| Viết/chạy test, đánh giá hoàn thành | [Testing](documents/testing.md) |

## Nguyên tắc bắt buộc

- Bảo toàn nội dung và nguồn trang; không âm thầm bỏ đoạn/trang lỗi.
- Mọi truy cập tài liệu phải kiểm tra quyền từ danh tính đã xác thực.
- Job bền vững, commit atomic, retry idempotent; không dùng bộ nhớ API làm hàng đợi duy nhất.
- Các bước truyền State có kiểu dữ liệu qua pipeline; chỉ tầng storage truy cập DB.
- Đổi contract phải cập nhật callers, tests và tài liệu nguồn tương ứng trong cùng thay đổi. Không sao chép toàn bộ quy tắc vào AGENTS.md.
- Chọn cách triển khai nhỏ nhất đủ yêu cầu; không thêm agent/layer/dependency chỉ để bọc lời gọi.

Đã chọn OCR local Tesseract vie+eng, PDFium render, tiktoken cl100k_base. Còn mở: embedding provider/model cho semantic chunking và giới hạn/retention vận hành; defaults local chưa phải cấu hình production đã chốt.
