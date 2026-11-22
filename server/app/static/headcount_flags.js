/**
 * Badge Management, Live Underground Headcount, and Check Flags UI.
 *
 * Owns badge issuance modal, SVG rendering, headcount panel polling, and flag resolution.
 * Must NOT store unhashed tokens in localStorage or rewrite existing app.js logic.
 */

(function () {
  let headcountPollTimer = null;
  let activeClearFlagId = null;

  function safeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Hook into selectWorker to append badge management controls
  const origSelectWorker = window.selectWorker;
  window.selectWorker = async function (workerId) {
    if (typeof origSelectWorker === "function") {
      await origSelectWorker(workerId);
    }
    await enhanceWorkerDetailWithBadge(workerId);
  };

  async function enhanceWorkerDetailWithBadge(workerId) {
    const pane = document.getElementById("workerDetailPane");
    if (!pane || !window.currentUser) return;

    try {
      const worker = await window.apiFetch(`/api/workers/${workerId}`);
      const isManager = window.currentUser.role === "manager";
      const isForeman = window.currentUser.role === "foreman";
      if (!isManager && !isForeman) return;

      const badgeBox = document.createElement("div");
      badgeBox.className = "zine-box";
      badgeBox.style.cssText = "padding:10px; margin-top:10px; background:#fff; border:2px solid var(--ink);";

      const badgeStatusText = worker.has_badge
        ? `<span style="color:var(--green-1); font-weight:700;">🟢 Active (Issued: ${safeHtml(worker.badge_issued_at ? new Date(worker.badge_issued_at).toLocaleString() : "Yes")})</span>`
        : `<span style="color:var(--ink-3); font-weight:700;">⚪ No active badge</span>`;

      badgeBox.innerHTML = `
        <h5 style="font-family:var(--condensed); margin:0 0 6px 0;">🛡️ QR SECURITY BADGE</h5>
        <div style="font-size:12px; margin-bottom:8px;">Status: ${badgeStatusText}</div>
        <div style="display:flex; gap:8px;">
          ${isManager ? `
            <button type="button" class="neo-btn btn-small btn-primary" onclick="window.issueWorkerBadge(${worker.id})">
              ${worker.has_badge ? "Re-Issue Badge" : "Issue Badge"}
            </button>
          ` : ""}
          ${worker.has_badge ? `
            <button type="button" class="neo-btn btn-small btn-danger" onclick="window.revokeWorkerBadge(${worker.id})">
              Revoke Badge
            </button>
          ` : ""}
        </div>
      `;
      pane.appendChild(badgeBox);
    } catch (err) {
      console.error("Failed to load badge info:", err);
    }
  }

  window.issueWorkerBadge = async function (workerId) {
    try {
      const res = await window.apiFetch(`/api/workers/${workerId}/badge`, { method: "POST" });
      const modal = document.getElementById("badgeModal");
      const container = document.getElementById("badgeSvgContainer");
      const info = document.getElementById("badgeWorkerInfo");
      if (!modal || !container || !info) return;

      container.innerHTML = res.badge_svg;
      info.innerHTML = `
        <div style="font-weight:700; font-size:14px;">${safeHtml(res.worker.full_name)}</div>
        <div style="font-size:12px; color:var(--ink-2);">Employee ID: <code>${safeHtml(res.worker.employee_no || "N/A")}</code></div>
        <div style="font-size:11px; color:var(--ink-3); margin-top:4px;">Issued: ${new Date(res.issued_at).toLocaleString()}</div>
      `;
      modal.classList.add("open");
    } catch (err) {
      alert("Failed to issue badge: " + err.message);
    }
  };

  window.revokeWorkerBadge = async function (workerId) {
    if (!confirm("Are you sure you want to revoke this worker's QR badge? This action cannot be undone.")) return;
    try {
      const res = await window.apiFetch(`/api/workers/${workerId}/badge`, { method: "DELETE" });
      alert(`Badge revoked successfully (${res.revoked} badge(s) deactivated).`);
      if (typeof window.selectWorker === "function") {
        await window.selectWorker(workerId);
      }
    } catch (err) {
      alert("Failed to revoke badge: " + err.message);
    }
  };

  window.closeBadgeModal = function () {
    const modal = document.getElementById("badgeModal");
    const container = document.getElementById("badgeSvgContainer");
    const info = document.getElementById("badgeWorkerInfo");
    if (modal) modal.classList.remove("open");
    if (container) container.innerHTML = "";
    if (info) info.innerHTML = "";
  };

  window.printBadge = function () {
    window.print();
  };

  // Headcount & Flags Console
  window.openHeadcountModal = async function () {
    const modal = document.getElementById("headcountModal");
    if (!modal) return;
    modal.classList.add("open");
    await refreshHeadcountData();
    await refreshCheckFlagsData();
    if (headcountPollTimer) clearInterval(headcountPollTimer);
    headcountPollTimer = setInterval(async () => {
      await refreshHeadcountData();
      await refreshCheckFlagsData();
    }, 10000);
  };

  window.closeHeadcountModal = function () {
    const modal = document.getElementById("headcountModal");
    if (modal) modal.classList.remove("open");
    if (headcountPollTimer) {
      clearInterval(headcountPollTimer);
      headcountPollTimer = null;
    }
  };

  async function refreshHeadcountData() {
    const container = document.getElementById("headcountTunnelsContainer");
    if (!container) return;
    try {
      const res = await window.apiFetch("/api/headcount");
      const tunnelIds = Object.keys(res || {});
      if (tunnelIds.length === 0) {
        container.innerHTML = `<div style="font-size:12px; color:var(--ink-3); padding:10px;">No tunnel headcount available.</div>`;
        return;
      }

      container.innerHTML = tunnelIds.map(tid => {
        const item = res[tid];
        const workers = item.workers || [];
        const workerList = workers.length > 0
          ? workers.map(w => `<div style="font-size:11px; padding:2px 0;">• ${safeHtml(w.full_name)} (<code>${safeHtml(w.employee_no || "N/A")}</code>) - In at ${new Date(w.scanned_at).toLocaleTimeString()}</div>`).join("")
          : `<div style="font-size:11px; color:var(--ink-3);">No personnel currently inside.</div>`;

        return `
          <div class="zine-box" style="padding:10px; margin-bottom:10px; background:#fff;">
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #ddd; padding-bottom:4px; margin-bottom:6px;">
              <h5 style="margin:0; font-family:var(--condensed);">TUNNEL #${item.tunnel_id}</h5>
              <div style="font-size:12px;">
                Inside: <strong style="color:var(--green-1); font-size:14px;">${item.inside}</strong> | Scheduled: <strong>${item.scheduled_now}</strong>
              </div>
            </div>
            <div style="max-height:120px; overflow-y:auto;">${workerList}</div>
          </div>
        `;
      }).join("");
    } catch (err) {
      console.error("Failed to load headcount:", err);
    }
  }

  async function refreshCheckFlagsData() {
    const container = document.getElementById("checkFlagsListContainer");
    if (!container) return;
    try {
      const res = await window.apiFetch("/api/check-flags?status=open");
      const flags = res.flags || [];
      if (flags.length === 0) {
        container.innerHTML = `<div style="font-size:12px; color:var(--green-1); padding:10px;">🟢 No open check flags. All finished shifts checked out.</div>`;
        return;
      }

      container.innerHTML = flags.map(fl => {
        const overdueStyle = fl.overdue
          ? "background:#ffebee; border:2px solid var(--red-1);"
          : "background:#fff; border:1px solid var(--ink);";
        const overdueBadge = fl.overdue
          ? `<span class="role-tag" style="background:var(--red-1); color:#fff;">⚠️ OVERDUE</span>`
          : "";

        return `
          <div class="zine-box" style="padding:8px; margin-bottom:8px; ${overdueStyle}">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <div>
                <strong>${safeHtml(fl.worker.full_name)}</strong> (Emp: <code>${safeHtml(fl.worker.employee_no || "N/A")}</code>) ${overdueBadge}
                <div style="font-size:11px; color:var(--ink-2);">
                  Tunnel: <strong>${safeHtml(fl.assignment.tunnel.name)}</strong> | Shift ended: ${new Date(fl.assignment.ends_at).toLocaleString()}
                </div>
                <div style="font-size:11px; color:var(--ink-3);">Flag raised: ${new Date(fl.raised_at).toLocaleString()}</div>
              </div>
              <button class="neo-btn btn-small btn-primary" onclick="window.openClearFlagModal(${fl.id}, '${safeHtml(fl.worker.full_name)}')">Clear Flag</button>
            </div>
          </div>
        `;
      }).join("");
    } catch (err) {
      console.error("Failed to load check flags:", err);
    }
  }

  window.openClearFlagModal = function (flagId, workerName) {
    activeClearFlagId = flagId;
    const modal = document.getElementById("clearFlagModal");
    const targetEl = document.getElementById("clearFlagWorkerName");
    const noteEl = document.getElementById("clearFlagNote");
    if (!modal) return;
    if (targetEl) targetEl.textContent = workerName;
    if (noteEl) noteEl.value = "";
    modal.classList.add("open");
    noteEl?.focus();
  };

  window.closeClearFlagModal = function () {
    const modal = document.getElementById("clearFlagModal");
    if (modal) modal.classList.remove("open");
    activeClearFlagId = null;
  };

  window.handleClearFlagSubmit = async function (e) {
    if (e) e.preventDefault();
    if (!activeClearFlagId) return;
    const note = document.getElementById("clearFlagNote")?.value.trim();
    if (!note || note.length < 3) {
      alert("Note must be between 3 and 500 characters.");
      return;
    }

    try {
      await window.apiFetch(`/api/check-flags/${activeClearFlagId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: note }),
      });
      window.closeClearFlagModal();
      await refreshHeadcountData();
      await refreshCheckFlagsData();
    } catch (err) {
      alert("Failed to clear flag: " + err.message);
    }
  };
})();
