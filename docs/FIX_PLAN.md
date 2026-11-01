# Kế hoạch sửa lỗi

Mỗi nhóm có commit và regression test riêng. Giữ dữ liệu thí nghiệm cũ; dùng output mới cho replay. Không gọi LLM hoặc chạy lại toàn bộ dataset để kiểm tra.

1. Validation preparation: danh sách file niêm phong bắt buộc, trace dừng sớm, exclude config mất, liên kết dataset/scope và timeout kiểm tra Docker. Đồng bộ logic liên quan sang bản gốc.
2. Kết quả: score trên tổng mutant chuẩn bị; fail không có score; không mutant N/A; best trên mọi attempt, hòa lấy sớm; dừng khi candidate pass đạt ngưỡng. Giữ best submission/latest riêng; giữ action lịch sử.
3. Prompt và feedback: thống nhất thẻ response, mô tả input sau lời gọi, nêu lời gọi bị cấm, giữ traceback/phản ví dụ hữu ích. Pass bộ test không chứng minh đúng toàn API.
4. Rà soát phản ví dụ bằng replay hẹp; phân biệt assertion quá mạnh, test thiếu và lỗi evaluator. Không sửa lại kết quả cũ.

Project config và tách gather/trace là thay đổi kiến trúc tiếp theo, không thuộc nhóm sửa lỗi này. Docker container smoke test được chủ repo cho phép bỏ qua; kiểm tra dựng lệnh vẫn phải chạy.
