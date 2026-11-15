"use strict";
let S = null;
function setSt(id, cls, text) { $(id).className = "status " + cls; $(id).textContent = text; }

function render(s) {
  S = s;
  $("lan-url").value = s.lan_url;
  s.lan_ready ? setSt("lan-st", "ok", "Sẵn sàng")
              : setSt("lan-st", "bad", "Web đang chỉ mở trên máy này. Đổi \"host\" trong config.json thành 0.0.0.0 rồi khởi động lại chương trình.");

  $("ts-url").value = s.ts_url || "";
  $("ts-check").disabled = s.ts_checking;
  if (s.ts_checking) setSt("ts-st", "", "Đang kiểm tra...");
  else if (s.ts_url) setSt("ts-st", "ok", "Tailscale đã kết nối");
  else setSt("ts-st", "warn", "Chưa thấy Tailscale trên máy này. Cài Tailscale, đăng nhập rồi bấm Kiểm tra lại.");

  $("cf-url").value = s.tunnel_url || "";
  const on = s.tunnel_state === "on" || s.tunnel_state === "starting";
  $("cf-toggle").textContent = on ? "Tắt tunnel" : "Bật tunnel";
  if (s.tunnel_state === "starting") setSt("cf-st", "", "Đang tạo link...");
  else if (s.tunnel_state === "on") setSt("cf-st", "ok", "Đang bật");
  else if (s.tunnel_state === "error") setSt("cf-st", "bad", s.tunnel_msg);
  else setSt("cf-st", "", "Đang tắt");
  if (document.activeElement !== $("cf-auto")) $("cf-auto").checked = s.tunnel_autostart;
}
async function refresh() { const r = await api("/api/remote"); if (r.ok) render(r.data); }
async function post(path, body) {
  $("msg").textContent = "";
  const r = await api(path, body);
  if (!r.ok) $("msg").textContent = r.data.error || "Lỗi";
  await refresh();
}
async function copy(id, btn) {
  const ok = await copyInput($(id));
  const t = btn.textContent; btn.textContent = ok ? "Đã chép ✓" : "Chưa có link";
  setTimeout(() => btn.textContent = t, 1500);
}
$("lan-copy").onclick = e => copy("lan-url", e.target);
$("ts-copy").onclick = e => copy("ts-url", e.target);
$("cf-copy").onclick = e => copy("cf-url", e.target);
$("ts-check").onclick = () => post("/api/remote/tailscale", {});
$("cf-toggle").onclick = () => post("/api/remote/tunnel", {on: !(S && (S.tunnel_state === "on" || S.tunnel_state === "starting"))});
$("cf-auto").onchange = () => post("/api/remote/autostart", {on: $("cf-auto").checked});

refresh();
setInterval(refresh, 2000);
