"use strict";
const ME = parseInt($("meid").dataset.id, 10);
const ROLES = ["viewer", "operator", "admin"];
const fmtTime = ts => ts ? new Date(ts * 1000).toLocaleString("vi-VN") : "—";

function cell(text) { const td = document.createElement("td"); td.textContent = text; return td; }
function btn(label, cls, fn) {
  const b = document.createElement("button");
  b.textContent = label; b.className = "small " + (cls || ""); b.onclick = fn; return b;
}

async function load() {
  const r = await api("/api/users");
  if (!r.ok) return;
  const body = $("ulist"); body.textContent = "";
  r.data.users.forEach(u => {
    const tr = document.createElement("tr");
    tr.appendChild(cell(u.username + (u.id === ME ? " (bạn)" : "")));
    const tdRole = document.createElement("td");
    const sel = document.createElement("select");
    ROLES.forEach(x => { const o = document.createElement("option"); o.value = o.textContent = x; sel.appendChild(o); });
    sel.value = u.role;
    sel.onchange = async () => { await act("/api/users/" + u.id, {role: sel.value}); };
    tdRole.appendChild(sel); tr.appendChild(tdRole);
    tr.appendChild(cell(u.active ? "Hoạt động" : "Đã khóa"));
    tr.appendChild(cell(fmtTime(u.last_login)));
    const tdAct = document.createElement("td");
    tdAct.className = "row";
    tdAct.appendChild(btn(u.active ? "Khóa" : "Mở khóa", "", () => act("/api/users/" + u.id, {active: !u.active})));
    tdAct.appendChild(btn("Đặt lại MK", "", async () => {
      const pw = prompt("Mật khẩu mới cho " + u.username + " (≥ 8 ký tự):");
      if (pw) await act("/api/users/" + u.id, {password: pw});
    }));
    if (u.id !== ME) tdAct.appendChild(btn("Xóa", "danger", async () => {
      if (confirm("Xóa tài khoản " + u.username + "?")) await act("/api/users/" + u.id, undefined, "DELETE");
    }));
    tr.appendChild(tdAct);
    body.appendChild(tr);
  });
  const ab = $("alist"); ab.textContent = "";
  r.data.audit.forEach(a => {
    const tr = document.createElement("tr");
    [fmtTime(a.ts), a.username || "—", a.ip || "", a.action, a.detail || ""].forEach(t => tr.appendChild(cell(t)));
    ab.appendChild(tr);
  });
}

async function act(path, body, method) {
  const r = await api(path, body === undefined ? {} : body, method);
  if (!r.ok) alert(r.data.error || "Lỗi");
  await load();
}

$("add").onclick = async () => {
  const msg = $("addmsg"); msg.textContent = "";
  const r = await api("/api/users", {username: $("nu").value, password: $("np").value, role: $("nr").value});
  if (!r.ok) { msg.textContent = r.data.error || "Lỗi"; return; }
  $("nu").value = $("np").value = ""; load();
};
load();
