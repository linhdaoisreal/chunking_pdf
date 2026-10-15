# Adaptive chunking

Hiện thực local: pypdf layout text, PDFium render và Tesseract vie+eng, tiktoken
`cl100k_base`. Pipeline LangChain không gửi text/ảnh ra ngoài; semantic chưa có provider,
luôn dùng recursive fallback có lý do. Tên/version parser/OCR/tokenizer lưu cùng kết quả.

Heading/list/bảng được suy ra từ text; chưa có phân tích layout học máy. OCR trang scan;
trang có ảnh được render, che glyph native trước OCR để giảm trùng. Text OCR bổ sung sau
native trong trang, warning `mixed_reading_order_approximate`. Cột/bảng phức tạp và thứ tự
đọc cần đánh giá bằng tài liệu thật. Không dùng coverage=1.0 để khẳng định OCR chính xác.
Chuẩn hóa CRLF/CR → LF; trim whitespace biên block có warning; NUL → U+FFFD có warning.
Span trỏ text block đã chuẩn hóa, end-exclusive; trang từ 1.

Splitter ưu tiên section/đoạn/câu/hàng/mục rồi ký tự Unicode dưới budget tokenizer.
Table header quá lớn không lặp; cắt cấu trúc có quality_flags. Overlap tối đa config,
đồng thời không vượt nửa chunk để luôn tiến lên. Repair gộp text ngắn cùng section;
đo coverage/token budget trước public. Metrics chủ đề là chưa đo vì không dùng model.

## 1. Nguyên tắc

- Chọn chiến lược **theo vùng nội dung**, không áp một splitter cho toàn PDF.
- Ưu tiên cấu trúc và quy tắc xác định được; không gọi LLM chỉ để chọn splitter.
- Đo bằng tokenizer được cấu hình và lưu tên/version cùng kết quả; không coi số ký tự là số token.
- Cấu hình: min_tokens, target_tokens, max_tokens, overlap_tokens, max_repair_rounds.
- Ràng buộc: `0 < min_tokens <= target_tokens <= max_tokens`; `0 <= overlap_tokens < target_tokens`.
- max_tokens là giới hạn đầu ra chunk. Khi dùng semantic splitting, mỗi input embedding phải tuân thủ giới hạn riêng của model, kể cả heading/metadata được đưa vào.

## 2. Lựa chọn ban đầu

| Tình trạng | Chiến lược |
| --- | --- |
| Heading/section rõ | Chia theo section; section dài tiếp tục chia theo đoạn/câu |
| Văn xuôi có đoạn/câu rõ | Recursive splitting: đoạn → câu → từ; cắt token là fallback cuối |
| Đổi chủ đề, thiếu ranh giới cấu trúc | Semantic splitting trên nhóm câu có giới hạn kích thước; vẫn giữ token budget |
| Bảng | Giữ nguyên nếu vừa budget; bảng dài tách theo nhóm hàng, lặp header và đánh dấu phần lặp |
| Danh sách | Giữ mục với heading; mục quá dài mới tách sâu hơn |
| OCR nhiễu/cấu trúc yếu | Chuẩn hóa thận trọng, recursive splitting và warnings; không dùng semantic splitting để che lỗi OCR |

Semantic splitting dùng embedding tùy chọn nội bộ để so sánh các đơn vị liền kề; không lưu vectors vào DB. Chưa cấu hình provider thì dùng recursive splitting và ghi lý do. Provider lỗi hoặc vượt ngân sách → fallback recursive, ghi lý do; không chặn việc trả chunks hợp lệ. Ngưỡng đổi chủ đề phải cấu hình, hiệu chỉnh bằng dữ liệu mẫu; không coi một ngưỡng dùng được cho mọi model.

## 3. Đánh giá và điều chỉnh

Sau lần chia đầu, đo chunks rỗng/quá dài/quá ngắn, ranh giới câu/heading/hàng bảng bị cắt, chủ đề bị trộn, overlap/trùng và coverage theo source spans. Header bảng lặp có chủ đích không tính là lỗi trùng.

Coverage yêu cầu mọi nội dung được giữ sau chuẩn hóa xuất hiện trong ít nhất một chunk. Phần bị loại khi chuẩn hóa phải có lý do; overlap không được tính là nội dung mới.

1. Chunk quá dài → chia tại ranh giới nhỏ hơn; đến token fallback nếu cần, đánh dấu khi phải cắt cấu trúc.
2. Chunk quá ngắn → gộp với chunk liền kề cùng section/chủ đề nếu vừa budget. Không gộp bảng với văn xuôi chỉ để đủ dài; đoạn cuối ngắn nhưng đủ ý được giữ.
3. Chunk trộn chủ đề → thử semantic splitting nếu text sạch, cấu hình và ngân sách cho phép.
4. Thiếu heading/ngữ cảnh → bổ sung metadata hoặc overlap trong cùng vùng, kiểm tra lại token budget.
5. Thiếu coverage → sửa mapping hoặc chia lại vùng lỗi; không chấp nhận mất nội dung im lặng.

Mặc định tối đa **2 vòng sửa**. Chỉ giữ thay đổi cải thiện tiêu chí mà không vi phạm ràng buộc cứng. Hết số vòng: fallback xác định được rồi kiểm tra lại; không đạt coverage/token budget hoặc không có chunk hợp lệ thì báo lỗi. Hạn chế mềm như OCR kém phải trả warnings.

Lưu strategy, lý do, số vòng sửa, metrics và config_version. Không tuyên bố chiến lược tối ưu khi chưa đánh giá trên tài liệu đại diện.


