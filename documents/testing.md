# Kiểm thử và tiêu chí hoàn thành

Upload/status: unit tests trong `tests/unit/test_documents.py`; PostgreSQL concurrency,
ownership và rollback document/job trong `tests/integration/test_documents_postgresql.py`.
Integration cần TEST_DATABASE_URL riêng; không coi test bị skip là đã xác minh DB thật.

Ingestion: `tests/integration/test_ingestion.py` chạy upload → worker subprocess → OCR →
phân trang trên PostgreSQL thật; kiểm tra ownership, lease hết hạn, stale commit,
retry đồng thời, rollback chunks, retention/orphan và giới hạn deadline/RAM.
`tests/unit/test_pdf.py` smoke test OCR thật với scan/mixed tiếng Việt được sinh tại runtime;
cần TESSERACT_COMMAND/TESSERACT_DATA_DIR. `test_chunking.py` kiểm tra Unicode,
coverage, budget, bảng/list và gộp theo section. Chưa có corpus PDF thực tế lớn để
đánh giá độ đúng OCR/layout/đổi chủ đề, chưa benchmark tải production.

1. PDF text, scan, hỗn hợp, tiếng Việt, nhiều cột, bảng dài, danh sách, PDF hỏng/mã hóa/rỗng.
2. Chunking: coverage đủ, không rỗng/quá budget, đúng thứ tự/trang, overlap hợp lệ, tách/gộp đúng, dừng sau số vòng sửa giới hạn.
3. Semantic không cấu hình/provider lỗi → fallback có lý do; OCR kém/lỗi không bị bỏ qua im lặng.
4. Upload trả `202` sau lưu file và commit, không chờ OCR/chunk. GET status và phân trang trả đủ chunks khớp DB sau completed.
5. Restart API/worker, lease hết hạn, worker cũ ghi muộn, retry đồng thời và DB rollback không mất job/nhân bản chunks.
6. Cùng key/hash tìm lại job; key khác hash bị chặn. Upload gián đoạn/file mồ côi/retention không làm mất nguồn của job đang chạy.
7. Người A không đọc trạng thái/chunks hoặc retry tài liệu người B.
8. Hết deadline, giới hạn file/trang/chunks/concurrency, retry quá lượt và nguồn hết hạn có trạng thái/lỗi rõ.

Unit tests dùng fixture nhỏ và mock provider; integration tests dùng PostgreSQL thật và worker. OCR có smoke test với engine đã chọn. Đánh giá chất lượng chunk bằng PDF mẫu có ranh giới/nguồn tham chiếu.

MVP hoàn thành khi upload PDF text/scan được xử lý bất đồng bộ bền vững, trả đủ chunks qua API và vượt qua toàn bộ kiểm thử trên.

Kiểm thử schema: Alembic upgrade DB mới đến head, nâng cấp từ revision trước, constraints thực thi đúng và không có drift giữa SQLAlchemy metadata với DB. Session của các task đồng thời phải độc lập.

- Users/documents: FK owner_id đúng users.id; inactive user bị chặn; user A không đọc trạng thái/chunks hoặc retry tài liệu của B.
- Phân trang chunks trả đúng thứ tự, không thiếu/trùng; response schemas không lộ auth fields/DSN/source_key.

- Auth: register email chuẩn hóa/UNIQUE dưới concurrency; password lưu hash, login sai trả lỗi chung; user inactive bị chặn; response không lộ hash/secret.
- JWT: thiếu/sai signature, sai alg/type/issuer/audience, thiếu claims, sai kiểu dữ liệu/UUID sub, exp/nbf/iat sai đều bị chặn; mọi API nghiệp vụ yêu cầu Bearer. User bị xóa/inactive không truy cập được dù JWT chưa hết hạn.
- Login trả access_token, token_type, expires_in trong data; Cache-Control: no-store. Không ghi token/phiên vào DB hoặc Redis; không có API refresh; logout xác nhận nhưng không thu hồi JWT.
- Token hết hạn trả 401 và cần đăng nhập lại. Logout client không thu hồi token: bản sao còn dùng được trước exp khi user active/key hợp lệ, bị chặn từ exp. Tests mô phỏng thời gian, không sleep dài.
- Kiểm tra rate limits và redaction logs; counter không chứa token hoặc thông tin phiên đăng nhập.
