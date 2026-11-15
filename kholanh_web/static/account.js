"use strict";
$("save").onclick = async () => {
  const msg = $("msg"); msg.className = "err";
  if ($("new1").value !== $("new2").value) { msg.textContent = "Hai mật khẩu mới không khớp"; return; }
  const r = await api("/api/password", {current: $("cur").value, new: $("new1").value});
  if (!r.ok) { msg.textContent = r.data.error || "Lỗi"; return; }
  msg.className = "okmsg"; msg.textContent = "Đã đổi. Đang chuyển về trang đăng nhập...";
  setTimeout(() => location.href = "/login", 1200);
};
