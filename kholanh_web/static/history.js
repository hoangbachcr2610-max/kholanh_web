"use strict";
async function load() {
  const h = $("range").value;
  $("csv").href = "/history.csv?hours=" + h;
  const r = await api("/api/history?hours=" + h);
  if (!r.ok) { $("info").textContent = r.data.error || "Lỗi tải dữ liệu"; return; }
  const rows = r.data.rows;
  $("info").textContent = rows.length + " điểm dữ liệu";
  drawChart($("c1"), rows.map(x => x[1]), "#0b5ed7", rows.map(x => x[0]));
  drawChart($("c2"), rows.map(x => x[2]), "#198754", rows.map(x => x[0]));
}
$("range").onchange = load;
window.addEventListener("resize", load);
load();
