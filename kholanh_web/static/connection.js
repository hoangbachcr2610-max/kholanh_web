"use strict";
let cur = null, formLoaded = false;

function readForm() {
  return {
    port: $("port").value.trim(), baud: parseInt($("baud").value, 10), parity: $("parity").value,
    interval: parseFloat($("interval").value), sensor_id: parseInt($("sensor_id").value, 10),
    relay_id: parseInt($("relay_id").value, 10), function: parseInt($("function").value, 10),
    hum_first: $("hum_first").value === "1", simulate: $("simulate").checked
  };
}
function descOf(f) {
  return f.simulate ? "Chế độ mô phỏng" : (f.port.split(" - ")[0] + " @ " + f.baud + "-8-" + f.parity + "-1");
}
function fillForm(c) {
  $("port").value = c.port; $("baud").value = c.baud; $("parity").value = c.parity;
  $("interval").value = c.interval; $("sensor_id").value = c.sensor_id; $("relay_id").value = c.relay_id;
  $("function").value = c.function; $("hum_first").value = c.hum_first ? "1" : "0";
  $("simulate").checked = c.simulate; syncSim();
}
function syncSim() { $("port").disabled = $("portsel").disabled = $("simulate").checked; }

function fillPorts(d) {
  const sel = $("portsel"); sel.textContent = "";
  const first = document.createElement("option");
  first.value = ""; first.textContent = d.ports.length ? "-- Chọn cổng COM (" + d.ports.length + " cổng) --" : "-- Không thấy cổng COM nào --";
  sel.appendChild(first);
  d.ports.forEach(p => { const o = document.createElement("option"); o.value = p.device; o.textContent = p.device + " - " + p.description; sel.appendChild(o); });
  syncSel();
  $("portnote").textContent = !d.pyserial
    ? "Chưa cài pyserial nên không liệt kê được cổng. Chạy: python -m pip install pyserial (vẫn có thể gõ tay tên cổng)."
    : (d.ports.length ? "Tìm thấy " + d.ports.length + " cổng: " + d.ports.map(p => p.device).join(", ")
                      : "Không thấy cổng COM nào. Kiểm tra cáp USB, driver (CH340/CP2102/FTDI) trong Device Manager, đóng phần mềm khác đang giữ cổng rồi bấm ↻. Có thể gõ tay, ví dụ COM9.");
}
// đồng bộ ô chọn theo nội dung ô gõ tay
function syncSel() {
  const v = $("port").value.split(" - ")[0].trim(), sel = $("portsel");
  sel.value = Array.from(sel.options).some(o => o.value === v) ? v : "";
}

function renderStatus(d) {
  cur = d;
  const st = $("status"), f = readForm();
  const running = d.connected;
  $("toggle").textContent = (running || d.want) ? "NGẮT KẾT NỐI" : "KẾT NỐI";
  $("apply").disabled = !(running || d.want);
  if (running && descOf(f) !== d.desc) {
    st.className = "status warn";
    st.textContent = "⚠ Chưa áp dụng: đang chạy ở " + d.desc + ". Bấm ÁP DỤNG.";
  } else if (running) {
    st.className = "status ok"; st.textContent = "Đã kết nối: " + d.desc;
  } else if (d.want) {
    st.className = "status bad"; st.textContent = "Mất kết nối, đang tự thử lại" + (d.error ? ": " + d.error : "");
  } else {
    st.className = "status"; st.textContent = "Chưa kết nối";
  }
}

async function load(refillForm) {
  const r = await api("/api/connection");
  if (!r.ok) return;
  fillPorts(r.data);
  const bs = $("baud");
  if (!bs.options.length) r.data.bauds.forEach(b => { const o = document.createElement("option"); o.value = o.textContent = b; bs.appendChild(o); });
  if (refillForm || !formLoaded) { fillForm(r.data.conn); formLoaded = true; }
  renderStatus(r.data);
}
async function poll() {
  const r = await api("/api/connection");
  if (r.ok) renderStatus(r.data);
}
async function run(path, body) {
  $("msg").textContent = "";
  $("toggle").disabled = $("apply").disabled = true;
  $("status").className = "status"; $("status").textContent = "Đang xử lý...";
  const r = await api(path, body);
  $("toggle").disabled = false;
  if (!r.ok) $("msg").textContent = r.data.error || "Lỗi";
  await poll();
}

$("toggle").onclick = () => (cur && (cur.connected || cur.want))
  ? run("/api/connection/disconnect", {}) : run("/api/connection/connect", readForm());
$("apply").onclick = () => run("/api/connection/apply", readForm());
$("refresh").onclick = () => load(false);
$("portsel").onchange = () => { if ($("portsel").value) { $("port").value = $("portsel").value; if (cur) renderStatus(cur); } };
$("port").addEventListener("input", syncSel);
$("simulate").onchange = () => { syncSim(); if (cur) renderStatus(cur); };
document.querySelectorAll("#port,#baud,#parity,#interval,#sensor_id,#relay_id,#function,#hum_first").forEach(
  e => e.addEventListener("input", () => cur && renderStatus(cur)));

load(true);
setInterval(poll, 2000);
