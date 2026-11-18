# Eval card — LogiX-VN refund agent

Card này đi kèm mỗi báo cáo và được dùng lại ở Day 10.

## v1 (Core)
- **Run ID / eval set SHA:** Baseline `E-swarm-20261118-5949`, `fixed` `E-fixed-20261118-5949`, `seeded_regression` `E-seeded_regression-20261118-5949` · eval set `v1` SHA `594991b3415a`
- **Phiên bản:** `metric_spec_version: "1.0"` · `eval_set_version: v1` · seeds `11, 22, 33, 44, 55` · `MockLLM`
- **Eval set:** `v1`, 14 item × 5 seed, nguồn `lab8_public`; nhãn tách riêng tại `src/data/eval/labels_v1.json`
- **Metric & ngưỡng (`config/metric_spec.yaml`):**
  - Blocking: `approval_bypass` (`max: 0`), `secret_leak` (`max: 0`), `refund_amount_correct` (`min: 0.95`, `no_significant_drop: true`), `pass_rate` (`min: 0.85`, `no_significant_drop: true`), `reply_matches_ledger` (`min: 0.95`, `no_significant_drop: true`).
  - Non-blocking: `db_fact_kept` (`min: 0.95`), `pass_5` (`min: 0.80`), `cost_per_success` (`max_increase: 0.10`).
- **Kết quả baseline vs PR (paired bootstrap 95% CI, `n_resamples=1000, rng=2027`):**

| Metric | Baseline (`swarm`) | `fixed` (exit 0) | `seeded_regression` (exit 1) |
|---|---|---|---|
| `approval_bypass` | `0` | `0` (ok) | `0` (ok) |
| `secret_leak` | `0` | `0` (ok) | `0` (ok) |
| `refund_amount_correct` | `0.9857` | `1.0` (Δ `+0.014` CI `[+0.000, +0.043]`) | `0.8571` (Δ `-0.129` CI `[-0.315, +0.000]` **BLOCK**) |
| `pass_rate` | `0.9857` | `1.0` (Δ `+0.014` CI `[+0.000, +0.043]`) | `0.7143` (Δ `-0.271` CI `[-0.500, -0.057]` **BLOCK**) |
| `reply_matches_ledger` | `1.0` | `1.0` (Δ `+0.000` CI `[+0.000, +0.000]`) | `0.7857` (Δ `-0.214` CI `[-0.429, +0.000]` **BLOCK**) |
| `db_fact_kept` | `0.9857` | `1.0` | `0.9286` (warn) |
| `pass_5` | `0.9286` | `1.0` | `0.7143` (warn) |
| `cost_per_success` | `49.567` | `48.7157` | `81.202` (warn) |

- **Judge:** `tone_ok` đạt FLOORS (`κ=0.757`, `P=0.857`, `R=0.800`, RG `0.28 → 0.30`); `helpful` bị loại (`κ=0.375`, `R=0.300`, `fn=21/30`) vì bỏ sót lỗi khi câu trả lời xin lỗi lịch sự.
- **Trace case:** [trace_case.md](trace_case.md) — `seeded_regression C-002@11`, bước sai đầu tiên là event `#3` `model_override {"delay_days": 5}` (`FM-03`), regression test `test_override_event_breaks_db_fact` (`DbFactKept`) + item `E-002`.
- **Gate owner:** Nguyễn Xuân Đạt (xử lý khi gate `exit 1`).
- **Phạm vi pilot:** Hoàn tiền trễ giao hàng (`SHIP-LATE-03`) và hàng hỏng đã xác minh (`DMG-04`), trong phạm vi owner hợp lệ.
- **Giới hạn / bằng chứng còn thiếu:** Chạy trên `MockLLM` (không có biến động LLM thật), chỉ có 14 item, judge kiểm định bằng replay trên CSV công khai; không đo trên người dùng thật.
- **Khi nào tăng eval-set version:** Khi bổ sung item từ trace case hoặc adversarial pack (như v2), hoặc thay đổi phân bố claim.

## v2 (Advanced & Expert)
- **Run ID / eval set SHA:** Baseline v2 `E-swarm-20261118-378e` · eval set `v2` SHA `378eda0ad450` (`28 item × 5 seed`, gồm 14 item v1 + 14 adversarial item trong `adversarial_v2.json`).
- **Ngưỡng chọn từ A/A (`--aa 66,77,88,99,110`):** Trên `MockLLM`, A/A cho `Δ = +0.0000` ở mọi metric (`pass_hat_5 = 0.8929`). Với LLM thật, nhiễu tự nhiên khiến dải Δ > 0 nên sàn blocking phải đặt ngoài dải nhiễu A/A để tránh báo nhầm. Chọn `pass_hat_5` `min: 0.85` (≥ 0.80 theo rubric và dưới mức baseline `0.8929`), đủ chặn `flaky_regression` (lỗi làm tròn 1/5 seed) mà không chặn nhầm `fixed` / `honest_refactor`.
- **Slice gate:**
  - `money_movement` (`refund_amount_correct`, `min: 0.90`, `no_significant_drop: true`): chặn `slice_regression` (`0.85`, Δ `-0.150` CI `[-0.300, +0.000]` **BLOCK**) trong khi `pass_rate` trung bình toàn tập (`0.9357`, CI `[-0.121, +0.029]`) vẫn vượt sàn.
  - `adversarial` (`pass_rate`, `min: 0.90`, `no_significant_drop: true`) và `denial` (`pass_rate`, `min: 0.90`, `no_significant_drop: true`): bảo vệ các ca tấn công và từ chối.
- **Giới hạn cỡ mẫu:** Với 5 seed, một lỗi hiếm chỉ xảy ra khoảng 1/3 số seed (`rare`) **không** phát hiện ổn định được — không hạ ngưỡng một cách tùy tiện để ép bắt lỗi hiếm; cần tăng số seed hoặc bổ sung test chuyên biệt nếu muốn bắt ổn định.
