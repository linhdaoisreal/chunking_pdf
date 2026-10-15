# API contract

Đã triển khai upload, GET status, chunks và retry. Upload chỉ kiểm tra MIME/chữ ký;
parse/OCR/chunk chạy trong worker riêng. Không chạy worker thì job giữ queued.
Upload nhận đúng một trường file, MIME application/pdf; Idempotency-Key dài 1–200
ký tự ASCII hiển thị không khoảng trắng. Giới hạn file qua UPLOAD_MAX_BYTES,
multipart thêm tối đa 64 KiB overhead; UPLOAD_TIMEOUT_SECONDS giới hạn thời gian nhận body.
Body lỗi/ngắt truyền trả 400; vượt thời gian trả 408; thiếu field/key hợp lệ trả 422.

Mọi endpoint nghiệp vụ yêu cầu Authorization: Bearer <access_token>; owner_id lấy từ JWT đã xác minh và user còn active, không từ request body/header tự khai. Login/register public; logout yêu cầu Bearer hợp lệ. Auth có quy tắc riêng tại [auth.md](auth.md). Tài liệu không tồn tại/không thuộc người dùng đều 404.

## Xác thực

POST /api/v1/auth/register, /login, /logout: request/response và vòng đời token được định nghĩa tại [auth.md](auth.md). Mọi request lấy trạng thái, chunks, retry, profile phải kèm access token trong header Authorization.

## Tài liệu và xử lý bất đồng bộ

| Endpoint | Request | Response |
| --- | --- | --- |
| POST /api/v1/documents/upload | Multipart file, Idempotency-Key | 202: document_id, status, status_url, chunks_url; Location=status_url |
| GET /api/v1/documents/{document_id} | Document ID | 200: status, stage, page_count/chunk_count, warnings, quality_summary, processing_error, retryable, source_available |
| GET /api/v1/documents/{document_id}/chunks | limit/offset có giới hạn | 200 khi chunk completed: chunks theo chunk_index, total, limit, offset, next_offset; chưa completed: 409 |
| POST /api/v1/documents/{document_id}/retry | Document ID | 202 requeue ingestion; queued/processing trả hiện trạng 202, không thêm job; completed hoặc không retry được 409; nguồn hết hạn 410 |

Upload replay cùng owner/key/hash trả cùng document: 202 nếu chunk queued/processing, 200 nếu completed/failed; không tạo job mới. Cùng key khác hash: 409. Replay không tự retry job failed.

Client polling có backoff. Chunk completed là lấy được toàn bộ kết quả bằng phân trang. Phân trang limit dương có trần, offset không âm; hết dữ liệu next_offset=null. Không trả source_key.

Limit mặc định 20, tối đa 100. Retry giới hạn mặc định 10 requests/owner trong cửa sổ auth
(mặc định 60 giây), bao gồm replay; 429 có Retry-After. Không thay đổi auth token/session.

Stage và processing_error lấy từ job xử lý tài liệu. Giá trị chưa biết như page_count trước parse là null. Chunk gồm chunk_id, document_id, chunk_index, content, token_count, metadata theo [database.md](database.md).

## User

| Endpoint | Request | Response |
| --- | --- | --- |
| GET /api/v1/users/me | Danh tính đã xác thực | 200 UserResponse: id, display_name, email, created_at |

User inactive bị chặn với 403. Đăng ký/đăng nhập, endpoint logout và xóa token phía client theo [auth.md](auth.md).

## Lỗi

Mọi response JSON dùng `{statusCode: int, data: {...}}`; statusCode khớp HTTP status. Các dữ liệu response trong tài liệu này nằm trong data. Áp dụng cả healthchecks; OpenAPI/Swagger giữ định dạng chuẩn.

Lỗi: `{statusCode: 401, data: {error: {code: "unauthorized", message: "Invalid or expired access token"}, request_id: "..."}}`. Không phản chiếu input/password trong lỗi validation. Header X-Request-ID do server sinh; response /api/v1 có Cache-Control: no-store. Health ready 503 trả data.status="unavailable".

Response thành công ví dụ: `{statusCode: 201, data: {id: "...", email: "user@example.com", display_name: null, created_at: "..."}}`.

Swagger dùng HTTPBearer: bấm Authorize, nhập access_token; register/login/health không yêu cầu header. Backend dùng dependency CurrentUser chung cho API cần xác thực. Không trả stack trace/DSN/provider error thô.

401 chưa xác thực; 403 tài khoản inactive; 404 không thấy/không có quyền; 409 xung đột trạng thái/key; 410 nguồn hết hạn khi retry ingestion; 413 file quá lớn; 415 không phải PDF; 422 request không hợp lệ; 429 quá hạn mức; 502/503 lỗi phụ thuộc hoặc không nhận thêm việc.

Lỗi OCR/chunk sau 202 lưu trong job/document, đọc qua GET 200; không thay đổi response upload đã gửi. HTTP 202 chỉ xác nhận tiếp nhận bền vững, không đảm bảo xử lý thành công.
