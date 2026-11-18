# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 6`

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | Đạt (`tp=24, fp=4, fn=6, tn=66`; fail rate 0.28 → RG 0.30) | **dùng** |
| helpful | 100 | 0.375 | 1.000 | 0.300 | Không đạt (`tp=9, fp=0, fn=21, tn=70`; fail rate 0.09 → RG 0.30) | **loại** |

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú & Hành động sửa rubric |
|---|---|---|---|---|---|
| 1 | `H-008` (`tone`) | PASS | FAIL | Người | `"Em đã hoàn tiền cho anh rồi nha, anh check giúp em."` dùng đại từ thân mật (`em`/`anh`, `nha`). Sửa rubric: cấm xưng hô suồng sã (`nha`, `em/anh`), bắt buộc `Dạ` + `anh/chị` + `ạ`. |
| 2 | `H-017` (`tone`) | PASS | FAIL | Người | `"Dạ, bạn ơi LogiX đã hoàn 25.000đ..."` gọi khách là `bạn ơi` và kết thúc `nha`. Sửa rubric: bổ sung danh sách từ cấm (`bạn ơi`, `nha`) vào negative examples. |
| 3 | `H-029` (`tone`) | PASS | FAIL | Người | `"Dạ, vui lòng chờ. Hệ thống đang xử lý, không cần nhắn thêm."` giọng cộc lốc, thiếu hỗ trợ. Sửa rubric: phạt câu ra lệnh/đuổi khéo (`không cần nhắn thêm`). |
| 4 | `H-042` (`tone`) | FAIL | PASS | Người | `"Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ."` lịch sự, chuẩn mực nhưng judge bắt nhầm (`fp`). Sửa rubric: nêu rõ thông báo thời gian hoàn tiền 3–5 ngày là hợp lệ. |
| 5 | `HL-015` (`helpful`) | PASS | FAIL | Người | `"Dạ, LogiX đã hoàn 55.000đ cho đơn O-112 ạ."` trôi chảy nhưng thiếu giải thích/sai thông tin hữu ích. Sửa rubric: không chấm PASS chỉ vì câu có `Dạ` và số tiền; bắt buộc đối chiếu nội dung giải quyết với yêu cầu. |
| 6 | `HL-016` (`helpful`) | PASS | FAIL | Người | `"Dạ, LogiX thành thật xin lỗi anh/chị vì sự bất tiện này..."` chỉ xin lỗi dài dòng mà không trả lời kết quả/hành động cụ thể. Sửa rubric: câu chỉ xin lỗi mà không nêu trạng thái xử lý hoặc bước tiếp theo = FAIL. |

## Kết luận
- **Tiêu chí bị loại:** `helpful` (`κ = 0.375 < 0.6`, `Recall = 0.300 < 0.7`). Ma trận nhầm lẫn (`tp=9, fp=0, fn=21, tn=70`) cho thấy judge bỏ sót tới 21/30 lỗi thực tế (`fn=21`), bị đánh lừa bởi câu trả lời lịch sự/xin lỗi dài dòng hoặc nêu số tiền trôi chảy, làm tỉ lệ lỗi quan sát bị giảm giả tạo (`0.09` thay vì `0.30` sau hiệu chỉnh Rogan-Gladen). Vì vậy **không được dùng `helpful` làm CI gate**.
- **Rủi ro còn lại:** judge chỉ được kiểm trên dữ liệu replay đã công khai; cần nhãn người mới để có held-out thật.
