# Xác thực — JWT access token

MVP dùng email/password và một JWT access token. Không lưu token, hash token, phiên đăng nhập hoặc danh sách thu hồi trong DB/Redis. Không có refresh token; token hết hạn phải đăng nhập lại.

## API và cách gửi token

| Endpoint | Xác thực / request | Response |
| --- | --- | --- |
| POST /api/v1/auth/register | Public; JSON email, password, display_name tùy chọn | 201 UserResponse; email đã tồn tại 409; không tự đăng nhập |
| POST /api/v1/auth/login | Public; JSON email, password | 200 TokenResponse |
| POST /api/v1/auth/logout | Bearer access token hợp lệ, user active | 200 LogoutResponse; client phải xóa token |

Mọi response dùng `{statusCode: int, data: {...}}`; HTTP status khớp statusCode. Các response trong bảng nằm trong data.

TokenResponse gồm access_token, token_type="bearer", expires_in (giây). Response chứa token phải có Cache-Control: no-store. Không trả password_hash.

Mọi API documents/users/retry/polling phải gửi:

```http
Authorization: Bearer <access_token>
```

Login/register không cần token. Truyền credentials/token qua HTTPS, không gửi token trên URL. Chưa chốt cơ chế lưu token cho browser frontend; không tự mặc định localStorage.

Thiếu/sai/hết hạn JWT hoặc user không còn tồn tại: 401 + WWW-Authenticate: Bearer. User inactive: 403. Sai email hoặc mật khẩu trả cùng lỗi 401, không tiết lộ tài khoản có tồn tại qua login. Rate-limit register/login theo IP và tài khoản; quá hạn mức trả 429.

## Access token

- TTL mặc định 15 phút, cấu hình qua ENV; thời hạn tính từ lúc cấp, không tự gia hạn theo hoạt động.
- JWT gồm sub=users.id dạng chuỗi UUID, iss, aud, iat, nbf, exp và token_type="access". Không chứa mật khẩu, DSN hoặc nội dung tài liệu. JWT được ký, không mặc định được mã hóa.
- Dùng PyJWT; pin thuật toán server HS256 với secret ngẫu nhiên ít nhất 256 bit. Kiểm tra signature, tất cả claims bắt buộc, kiểu dữ liệu, UUID sub, issuer/audience, thời gian và token_type. Không tin alg/key URL do client cung cấp; từ chối none/sai thuật toán.
- Dependency FastAPI dùng HTTPBearer; xác minh JWT trước khi tra users qua storage/users.py. User phải tồn tại và active; owner lấy từ user đã xác minh. Stateless ở cấp token không loại bỏ kiểm tra tài khoản và quyền tài liệu.
- Key đọc từ ENV/secret store, không fallback secret mặc định. Config gồm JWT_SECRET, JWT_ISSUER, JWT_AUDIENCE và JWT_ACCESS_TTL_SECONDS; validate khi triển khai auth.

## Hết hạn và logout

- JWT hết hạn trả 401; client đăng nhập lại để nhận token mới. Không có endpoint refresh. POST /api/v1/auth/logout xác nhận yêu cầu, trả data.message hướng dẫn client xóa token; không thu hồi JWT.
- Logout phía client xóa token và ngừng gửi Authorization. Không thay đổi exp hoặc vô hiệu bản sao JWT đã cấp; token đó vẫn hợp lệ đến hạn nếu user còn active và key còn được chấp nhận.
- Không hỗ trợ thu hồi riêng từng token hoặc từng thiết bị. Đổi signing key làm token dùng key cũ mất hiệu lực nếu ngừng chấp nhận key cũ; ảnh hưởng toàn bộ người dùng dùng key đó.

## Mật khẩu và tài khoản

- Email đăng nhập chuẩn hóa trim/lowercase thống nhất, UNIQUE tại DB. Request chấp nhận email ASCII dạng dot-atom với domain DNS có dấu chấm, tối đa 254 ký tự (local part tối đa 64); chưa hỗ trợ địa chỉ quoted-local hoặc SMTPUTF8. Đây là định danh sản phẩm, không tự coi email đã được xác minh hoặc liên kết tài khoản ngoài.
- Password không trim/lowercase, không lưu plaintext; hash Argon2id bằng thư viện đã kiểm chứng, salt do thư viện quản lý. Dùng dummy hash khi user không tồn tại để giảm chênh lệch thời gian login.
- MVP chấp nhận 6–128 ký tự, không ép mẫu ký tự; giới hạn kích thước request và không cắt mật khẩu. Tham số hash phải đo trên môi trường triển khai, tránh chặn event loop bằng hash CPU nặng.
- Đổi/quên mật khẩu và xác minh email chưa thuộc MVP; cần đặc tả riêng nếu bổ sung. Không mặc định đổi mật khẩu sẽ vô hiệu JWT đã cấp.

## Trạng thái triển khai và vận hành

Đã triển khai register/login/logout, GET /api/v1/users/me, JWT helpers, Argon2id, body limit và PostgreSQL rate limit. Bảng users/auth_rate_limits thuộc revision 0001; phải migrate trước khi gọi auth. Không có dữ liệu token cần migrate/xóa.

Auth payload tối đa 16 KiB, kể cả request không có Content-Length; vượt giới hạn trả 413 trước JSON parsing. Hash/verify mật khẩu chạy trong threadpool.

Register/login dùng chung rate limit cửa sổ cố định PostgreSQL; IP được đếm trước khi đọc/parse body, email chuẩn hóa được đếm sau validation, trước hash/verify. Logout chỉ kiểm tra JWT/user, không tăng counter đăng nhập. Cấu hình: AUTH_RATE_WINDOW_SECONDS=60, AUTH_IP_LIMIT=60 cho tổng request auth cùng IP, AUTH_IDENTITY_LIMIT=10 cho account (register/login dùng chung). Đây là counter chống lạm dụng, không chứa token hoặc phiên. 429 có Retry-After (giây); các instance dùng cùng cấu hình. Không tự tin X-Forwarded-For; chỉ tin reverse proxy đã cấu hình.

Không ghi token/password/Authorization vào logs, traces hoặc error responses. Schema tại [database.md](database.md), kiểm thử tại [testing.md](testing.md).
