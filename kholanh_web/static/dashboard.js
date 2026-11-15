"use strict";
let state = null, dirty = false, fsHold = 0;

function render(s) {
  state = s;
  const role = RANK[s.me.role], canOp = role >= 2, canAdmin = role >= 3;
  const fresh = s.connected && s.age !== null && s.age < 15;
  $("conn").textContent = !s.connected ? "Chưa kết nối thiết bị" : (fresh ? "Đang trực tuyến" : "Dữ liệu cũ");
  $("conn").className = "pill " + (fresh ? "good" : "warn");
  $("simbar").hidden = !s.sim;

  const bad = s.alarms.length > 0;
  $("banner").className = "banner" + (bad ? " bad" : "");
  $("banner").textContent = bad ? "⚠ CẢNH BÁO: " + s.alarms.join(" | ") : "● BÌNH THƯỜNG";

  $("temp").textContent = fmt(s.temp, 1) + " °C";
  $("hum").textContent = fmt(s.hum, 1) + " %RH";
  $("temp").classList.toggle("alarm", s.alarms.some(a => a.startsWith("Nhiệt")));
  $("hum").classList.toggle("alarm", s.alarms.some(a => a.startsWith("Độ ẩm")));
  $("upd").textContent = s.age === null
    ? (s.connected ? "Đang chờ dữ liệu..." : (s.want_conn ? "Mất kết nối thiết bị: " + (s.conn_error || "đang tự thử lại")
         : "Chưa kết nối thiết bị." + (canAdmin ? " Vào mục Thiết bị để kết nối." : " Liên hệ quản trị viên.")))
    : "Cập nhật " + Math.round(s.age) + " giây trước" + (s.conn_desc ? " · " + s.conn_desc : "");

  $("m-auto").classList.toggle("active", s.mode === "auto");
  $("m-manual").classList.toggle("active", s.mode === "manual");
  $("m-auto").disabled = $("m-manual").disabled = !canOp;
  $("permhint").textContent = canOp ? "" : "Tài khoản chỉ có quyền xem.";
  [0, 1].forEach(i => {
    const on = s.fans[i];
    $("f" + i + "s").textContent = on ? "ĐANG CHẠY" : "TẮT";
    $("f" + i + "s").className = "state" + (on ? " on" : "");
    const b = $("f" + i + "b");
    b.textContent = on ? "Tắt quạt" : "Bật quạt";
    b.disabled = !(canOp && s.mode === "manual" && s.connected);
  });
  $("fanhint").textContent = s.mode === "auto" ? "Đang ở chế độ Tự động: chuyển sang Thủ công để bật/tắt quạt bằng tay." : "";

  $("failsafe").disabled = !canAdmin;
  if (Date.now() > fsHold) $("failsafe").value = s.failsafe;
  document.querySelectorAll(".thr input").forEach(i => i.disabled = !canOp);
  $("savethr").hidden = !canOp;
  if (!dirty) for (const k in s.thr) $(k).value = s.thr[k];
  drawChart($("c1"), s.hist.temp, "#0b5ed7");
  drawChart($("c2"), s.hist.hum, "#198754");

  const ev = $("events"); ev.textContent = "";
  s.events.slice().reverse().forEach(e => {            // textContent: không chèn HTML từ dữ liệu
    const d = document.createElement("div");
    d.className = e[2]; d.textContent = "[" + e[0] + "] " + e[1];
    ev.appendChild(d);
  });
}

async function refresh() {
  const r = await api("/api/state");
  if (!r.ok) { $("conn").textContent = "Mất kết nối tới máy chủ"; $("conn").className = "pill warn"; return; }
  render(r.data);
}

async function act(path, body) {
  const r = await api(path, body);
  if (!r.ok) alert(r.data.error || "Lệnh bị từ chối");
  await refresh();
  return r.ok;
}

$("m-auto").onclick = () => act("/api/mode", {mode: "auto"});
$("m-manual").onclick = () => act("/api/mode", {mode: "manual"});
$("f0b").onclick = () => state && act("/api/fan", {ch: 0, on: !state.fans[0]});
$("f1b").onclick = () => state && act("/api/fan", {ch: 1, on: !state.fans[1]});
$("failsafe").onchange = async () => {
  fsHold = Date.now() + 4000;
  const ok = await act("/api/failsafe", {value: $("failsafe").value});
  $("fsmsg").textContent = ok ? "✓ Đã lưu" : "";
  if (!ok) fsHold = 0;
  setTimeout(() => $("fsmsg").textContent = "", 3000);
};
document.querySelectorAll(".thr input").forEach(i => i.oninput = () => dirty = true);
$("savethr").onclick = async () => {
  const body = {};
  for (const k of ["t_high", "t_low", "h_high", "h_low", "t_hyst", "h_hyst"]) body[k] = parseFloat($(k).value);
  if (await act("/api/thresholds", body)) {
    dirty = false; $("thrmsg").textContent = "✓ Đã lưu";
    setTimeout(() => $("thrmsg").textContent = "", 3000);
  }
};

refresh();
setInterval(refresh, 1500);
