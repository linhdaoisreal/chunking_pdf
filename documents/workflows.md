# Pipeline bất đồng bộ và vòng đời công việc

Hiện thực: upload/status, worker/OCR/chunk, chunks/retry và retention đã có.
Upload dọn nguồn dư hoặc rollback khi xác minh không được tham chiếu. Nếu mất kết nối
khi commit và chưa xác minh được kết quả, giữ nguồn và log để tránh xóa file đã nhận.
Worker đối soát mỗi 60 giây. Upload giữ shared advisory transaction lock trong save+enqueue;
cleanup giữ exclusive lock cùng khóa trước quét file mồ côi. Chỉ dọn tên UUID do server sinh,
quá ORPHAN_GRACE_SECONDS và không có document tham chiếu. Retention khóa document;
không xóa nguồn queued/processing. Retry khóa job rồi document, tránh đua retention.
`--once` xử lý tối đa một job/slot, không chạy maintenance định kỳ.

Mỗi lần xử lý chạy trong tiến trình spawn riêng. Worker kiểm tra RAM cây tiến trình mỗi
100 ms, deadline, heartbeat và lease; vượt giới hạn thì kill cả tiến trình OCR con.
Giới hạn RAM là giám sát lấy mẫu, không phải sandbox/kernel quota; có thể vượt tạm thời
giữa hai lần đo. Production cần container/OS memory limit bổ sung.
Manual retry reset attempt_count cho lượt mới, giữ snapshot xử lý; bị giới hạn theo owner
bằng RETRY_OWNER_LIMIT trong AUTH_RATE_WINDOW_SECONDS. PDF lỗi dữ liệu không retry.

API: `Authenticate → Validate upload → Persist file → Commit queued document → 202`

Worker: `Claim → Parse/OCR → Analyze → Chunk → Evaluate/Repair → Commit chunks + completed`

## 1. Nhận upload

1. Nhận multipart file và `Idempotency-Key` bắt buộc; lấy owner_id từ danh tính đã xác thực.
2. Giới hạn byte khi đọc, kiểm tra chữ ký PDF và MIME; khả năng parse được kiểm tra trong worker. Filename không được dùng làm đường dẫn hoặc lệnh shell.
3. Lưu file bằng tên server sinh vào vùng lưu trữ bền vững mà worker đọc được. MVP một host dùng persistent volume chung API/worker; không dùng thư mục tạm có thể biến mất sau response/restart.
4. Sau khi file đã lưu bền vững, tạo document `queued` trong transaction PostgreSQL. Document và job ingestion được tạo cùng transaction; không có bước enqueue ngoài DB có thể bị thất lạc.
5. Chỉ trả `202` sau commit, gồm document_id, status, status_url, chunks_url; header Location trỏ tới status_url.
6. Cùng owner/key/hash: trả document cũ, `202` nếu queued/processing, `200` nếu completed/failed; không tạo thêm công việc. Cùng key khác hash: `409`. UNIQUE constraint xử lý cả upload đồng thời.
7. Upload gián đoạn trước commit: không tạo công việc; dọn file dở. File lưu thành công nhưng DB lỗi phải được dọn; có tác vụ dọn file mồ côi sau khoảng đệm, tránh xóa file đang upload/xử lý.

Bất đồng bộ loại thời gian OCR/chunk khỏi request upload; việc truyền file vẫn có timeout và giới hạn kích thước riêng.

## 2. Worker và retry

- Trạng thái document (chunk): `queued → processing → completed|failed`; job tương ứng dùng succeeded khi thành công. Retry có kiểm soát: `processing → queued` cho lỗi tạm thời, hoặc `failed → queued` qua API.
- Claim atomic trong transaction ngắn, dùng khóa hàng và bỏ qua hàng đã bị khóa; cập nhật attempt_count, lease_token và lease_expires_at của job rồi commit. Không giữ transaction lúc parse/OCR/gọi provider.
- Worker có heartbeat gia hạn lease. Khi lease hết hạn, Storage thu hồi và requeue nếu còn lượt; hết lượt thì failed. Mọi heartbeat/ghi kết quả phải kiểm tra lease_token và thời hạn, ngăn worker cũ ghi đè worker mới.
- Công việc có thể chạy lại sau crash: lưu kết quả idempotent, không hứa xử lý đúng một lần. Giới hạn attempts, backoff và deadline từng lần chạy; lỗi PDF hỏng/mã hóa/rỗng không tự retry.
- Stage báo bước thực tế như parsing, ocr, chunking, saving; không bịa phần trăm hoàn thành khi không đo được.
- Worker parse theo trang/block, giữ thứ tự đọc. OCR trang thiếu text và vùng ảnh mang nội dung trên trang hỗn hợp, tránh ghép trùng text/OCR.
- Giữ heading, bảng, danh sách, nhiều cột theo khả năng parser. PDF cần mật khẩu chưa hỗ trợ: failed với lỗi rõ. Không có nội dung hữu dụng sau OCR: failed, không completed với chunks rỗng.
- Trang không đọc được hoặc OCR kém phải xuất hiện trong warnings; không âm thầm bỏ qua. Chạy adaptive chunking ở [chunking.md](chunking.md).
- Lưu toàn bộ chunks, chunk_count, quality_summary, status=`completed`, đánh dấu job succeeded trong cùng transaction có kiểm tra lease. Chưa completed thì không công khai chunks lưu dở.
- Parse/OCR chạy trong worker, giới hạn concurrency, bộ nhớ, số trang/chunks và thời gian; có cơ chế dừng tiến trình OCR quá deadline. Không dùng tác vụ chỉ tồn tại trong bộ nhớ API làm hàng đợi duy nhất.

## 3. Lưu file và phục hồi

Giữ PDF gốc trong khi queued/processing và trong cửa sổ retry khi failed. Sau completed hoặc hết hạn giữ file thất bại, dọn theo retention cấu hình; cập nhật thông tin nguồn; source_available là giá trị suy ra, không phải cột DB. Chunks vẫn đọc được sau khi xóa PDF gốc.

Retry chỉ khi failed, lỗi có thể retry và file nguồn còn tồn tại; nếu file đã hết hạn, yêu cầu upload lại bằng key mới. Retry đồng thời chỉ tạo một lần queued; giới hạn retry thủ công theo owner. Lỗi dữ liệu không thể sửa bằng chạy lại phải upload file đã sửa với key mới.

## 4. Hoàn tất và concurrency

- MVP xử lý từng tài liệu với concurrency mặc định 1 trên một worker; chỉ tăng sau kiểm chứng tài nguyên. Muốn tuần tự toàn hệ thống chỉ chạy một worker/một slot.
- Mỗi tài liệu hoàn thành độc lập; client ngừng polling khi completed/failed. Không có job embedding sau chunk.
- Semantic splitting có thể dùng embedding tạm trong ingestion theo [chunking.md](chunking.md); không lưu vectors vào DB.

Schema job và transaction: [database.md](database.md). Hợp đồng HTTP: [api.md](api.md).
