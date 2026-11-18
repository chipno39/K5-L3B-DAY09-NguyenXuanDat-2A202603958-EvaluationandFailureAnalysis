# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Mẫu điền (mỗi bạn 1 case, lấy từ trial đang fail trong report)

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11` (xác nhận từ `lab9.trace_case --shape seeded_regression --claim C-002 --seed 11`) |
| Triệu chứng | `TrialPass` (`lab8` grader) FAIL (`status resolved != denied`); ledger ghi `refund_vnd=20000`, `refund_count=1` trong khi label yêu cầu `0` và `status=denied`; reply nói `"Dạ, LogiX đã hoàn 20.000đ cho đơn O-202."` |
| Bước sai đầu tiên | Event `#3` `model_override`: `{"delay_days": 5}` |
| Vì sao là bước *đầu tiên* | Event `#1` `handoff` chuyển đúng `allowed_tools` cho `check_applicable_policy`; event `#2` `investigator` gọi `lookup_order` trả về đúng dữ liệu DB (`order_id: O-202, status: delivered, delay_days: 2`). Chỉ đến `#3`, agent mới ghi đè `delay_days=5` theo ticket. |
| Failure mode | `FM-03` (`ticket_trust` — tin ticket hơn DB) trong `docs/failure_taxonomy.md` |
| Đối chứng | Trên `fixed C-002@11`: không có event `model_override`; event `#3` là `quote {"amount_vnd": 0, "reason": "no_entitlement"}`, event `#4` là `reply` từ chối, ledger `refund_vnd=0`, `status=denied` (PASS). |
| Regression test | `test_override_event_breaks_db_fact` trong `src/tests/test_lab9_graders.py` dùng `DbFactKept` (kiểm tra sự kiện `model_override`) + item `E-002` (`C-002`) trong `eval_set_v1.jsonl` (`db_fact_kept` fail trên `seeded_regression`, pass trên `fixed`). |
| Chưa chắc chắn | Trace chỉ ghi nhận sự kiện `model_override` xảy ra sau `investigator`, không ghi nguyên nhân nội tại của prompt/LLM nếu không có `MockLLM` hoặc log prompt đầy đủ. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
