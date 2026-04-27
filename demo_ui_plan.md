# Kế hoạch Demo UI cho luồng nghiệp vụ Ride Hailing

## 1. Mục tiêu
- Trình diễn rõ ràng luồng nghiệp vụ: tạo yêu cầu chuyến đi -> nhận chuyến -> hoàn thành chuyến.
- Giao diện dễ hiểu cho người xem demo, có điều khiển từng bước và chế độ chạy tự động.
- Trực quan hóa được bản đồ, vị trí tài xế, tiến trình chuyến đi và luồng sự kiện giữa các service.

## 2. Phạm vi nghiệp vụ bám theo backend hiện có

### 2.1 Endpoint chính
- Ride Service
  - `POST /v1/trips` (tạo yêu cầu chuyến đi)
  - `GET /v1/trips/:id` (lấy trạng thái/chuyến đi)
  - `POST /v1/trips/:id/accept` (nhận chuyến)
  - `POST /v1/trips/:id/complete` (hoàn thành chuyến)
- Dispatch Service
  - `PUT /v1/drivers/:id/location` (cập nhật vị trí tài xế)
  - `GET /v1/dispatch/ws/location` (WebSocket vị trí)
- Notification Service
  - `GET /v1/history` (lịch sử sự kiện/thông báo)

### 2.2 Event flow chính để hiển thị
- `Trip.Requested`
- `Trip.Matched`
- `Trip.Accepted`
- `Trip.Completed`

## 3. Đề xuất giao diện demo

### 3.1 Bố cục tổng quan (một màn hình)
- Cột trái: Passenger App Mock (kiểu Grab) để nhập điểm đón/điểm đến và đặt chuyến.
- Cột giữa: Bản đồ + tiến trình chuyến đi.
- Cột phải: Driver App Mock + Notification + Event Timeline.

### 3.2 Bản đồ (bắt buộc)
- Marker: Passenger, điểm đến, các tài xế online, tài xế được gán.
- Tuyến đường:
  - Tài xế -> điểm đón.
  - Điểm đón -> điểm đến.
- Animation:
  - Marker tài xế di chuyển mượt theo tick thời gian.
  - Hiệu ứng pulse khi đang tìm tài xế.

### 3.3 Trạng thái vòng đời chuyến đi
- `Idle`
- `Searching Driver`
- `Driver Assigned`
- `Driver Coming`
- `On Trip`
- `Completed`

Mỗi trạng thái có:
- Badge màu rõ ràng.
- Mô tả ngắn 1 câu để người xem không chuyên kỹ thuật vẫn hiểu.

### 3.4 Event Timeline trực quan
- Dạng timeline dọc, có animation khi sự kiện mới xuất hiện.
- Hiển thị: thời gian, loại event, trip id rút gọn, service liên quan.
- Bộ lọc nhanh: All | Ride | Dispatch | Notification.

### 3.5 Cửa sổ thông báo (2 phía)
- Passenger notifications:
  - Đang tìm tài xế
  - Đã tìm thấy tài xế
  - Tài xế đang đến
  - Chuyến đi hoàn thành
- Driver notifications:
  - Có chuyến mới
  - Di chuyển đến điểm đón
  - Hoàn thành chuyến
- Hiệu ứng: slide-in đơn giản, tự ẩn sau vài giây và lưu lịch sử.

### 3.6 Thanh điều khiển demo
- Nút chính:
  - Seed Drivers
  - Request Ride
  - Simulate Driver Movement
  - Complete Trip
  - Reset Demo
- Nút trình diễn:
  - Auto Play
  - Step Mode
  - Speed x1/x2/x4
- Khối hướng dẫn ngắn 3 bước luôn hiển thị để hỗ trợ người xem.

### 3.7 API Inspector (phục vụ demo kỹ thuật)
- Hiển thị request/response gần nhất theo bước.
- Hiển thị method, URL, payload, status code.
- Hiển thị trip id hiện hành để đối chiếu với timeline.

## 4. Trải nghiệm thị giác
- Phong cách gần với ứng dụng gọi xe phổ biến: map lớn, card nổi, CTA rõ ràng.
- Tông sáng, tương phản tốt khi trình chiếu.
- Responsive cho desktop và mobile.

## 5. Kịch bản demo chuẩn
1. Seed tài xế và cập nhật vị trí ban đầu.
2. Passenger tạo chuyến (`POST /v1/trips`).
3. Timeline hiển thị `Trip.Requested` -> `Trip.Matched` -> `Trip.Accepted`.
4. Driver marker di chuyển đến điểm đón (Driver Coming).
5. Chuyển sang On Trip và di chuyển đến điểm đến.
6. Hoàn thành chuyến (`POST /v1/trips/:id/complete`) và hiển thị trạng thái Completed.

## 6. Kịch bản ngoại lệ (dành cho bản mở rộng)
- Không tìm thấy tài xế.
- Timeout service.
- Hủy chuyến.

## 7. Kế hoạch triển khai sau khi duyệt
1. Dựng layout + bản đồ + control bar.
2. Nối API create/accept/complete và đồng bộ state UI.
3. Làm event timeline + notification windows + auto play.
4. Tinh chỉnh hiệu ứng và hoàn thiện trải nghiệm trình diễn.

## 8. Ghi chú kỹ thuật
- Frontend sẽ được khởi tạo mới trong thư mục `vroom-services/frontend` (hiện chưa có sẵn trong bản source đính kèm).
- URL service local theo docker-compose:
  - User: `http://localhost:8081`
  - Ride: `http://localhost:8082`
  - Dispatch: `http://localhost:8083`
  - Notification: `http://localhost:8084`
