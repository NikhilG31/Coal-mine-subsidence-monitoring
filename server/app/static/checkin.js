/**
 * Gate Check-In and Check-Out Scanner UI.
 *
 * Owns scanner camera/USB handling, in/out toggle, recent scans table, and scanner controls.
 * Must NOT store unhashed tokens in localStorage or rewrite existing app.js logic.
 */

(function () {
  let scanDirection = "in";
  let videoStream = null;

  function safeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Gate Scanner Functions
  window.openGateScannerModal = async function () {
    const modal = document.getElementById("gateScannerModal");
    if (!modal) return;
    modal.classList.add("open");
    await populateScannerTunnels();
    await window.loadRecentScans();
    document.getElementById("scanTokenInput")?.focus();
  };

  window.closeGateScannerModal = function () {
    const modal = document.getElementById("gateScannerModal");
    if (modal) modal.classList.remove("open");
    stopScannerCamera();
  };

  window.setScanDirection = function (dir) {
    scanDirection = dir;
    const btnIn = document.getElementById("btnScanIn");
    const btnOut = document.getElementById("btnScanOut");
    if (btnIn && btnOut) {
      btnIn.className = dir === "in" ? "neo-btn btn-small btn-success active-dir" : "neo-btn btn-small";
      btnOut.className = dir === "out" ? "neo-btn btn-small btn-danger active-dir" : "neo-btn btn-small";
    }
  };

  async function populateScannerTunnels() {
    const select = document.getElementById("scannerTunnelSelect");
    if (!select || !window.currentUser) return;

    select.innerHTML = "";
    if (window.currentUser.role === "foreman") {
      select.innerHTML = `<option value="">(Own Active Tunnel)</option>`;
      select.disabled = true;
      return;
    }

    select.disabled = false;
    try {
      const mRes = await window.apiFetch("/api/mines");
      for (const m of mRes.mines || []) {
        try {
          const tRes = await window.apiFetch(`/api/mines/${m.id}/tunnels`);
          for (const t of tRes.tunnels || []) {
            select.innerHTML += `<option value="${t.id}">${safeHtml(m.name)} - ${safeHtml(t.name)}</option>`;
          }
        } catch (_) {}
      }
    } catch (err) {
      console.error("Failed to load tunnels for scanner:", err);
    }
  }

  window.handleScanSubmit = async function (e) {
    if (e) e.preventDefault();
    const tokenInput = document.getElementById("scanTokenInput");
    const alertBox = document.getElementById("scanResultAlert");
    if (!tokenInput || !alertBox) return;

    const token = tokenInput.value.trim();
    if (!token) return;

    const tunnelVal = document.getElementById("scannerTunnelSelect")?.value;
    const payload = {
      token: token,
      direction: scanDirection,
    };
    if (tunnelVal) payload.tunnel_id = parseInt(tunnelVal, 10);

    tokenInput.value = "";
    try {
      const res = await window.apiFetch("/api/checkins", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (res.duplicate) {
        alertBox.style.display = "block";
        alertBox.style.background = "#fff3cd";
        alertBox.style.color = "#856404";
        alertBox.innerHTML = `⚠️ <strong>Duplicate scan ignored:</strong> ${safeHtml(res.worker.full_name)} already checked ${safeHtml(res.direction.toUpperCase())}`;
      } else {
        alertBox.style.display = "block";
        alertBox.style.background = "#d4edda";
        alertBox.style.color = "#155724";
        let warnText = "";
        if (res.warnings && res.warnings.length > 0) {
          warnText = `<div style="margin-top:4px; font-size:11px; color:#856404;">⚠️ ${safeHtml(res.warnings[0].message)}</div>`;
        }
        alertBox.innerHTML = `✅ <strong>CHECK-${safeHtml(res.direction.toUpperCase())} SUCCESS:</strong> ${safeHtml(res.worker.full_name)} (Emp: ${safeHtml(res.worker.employee_no || "N/A")}) ➔ ${safeHtml(res.tunnel.name)}${warnText}`;
      }
      await window.loadRecentScans();
    } catch (err) {
      alertBox.style.display = "block";
      alertBox.style.background = "#f8d7da";
      alertBox.style.color = "#721c24";
      alertBox.innerHTML = `❌ <strong>Scan Rejected:</strong> ${safeHtml(err.message)}`;
    }
    tokenInput.focus();
  };

  window.loadRecentScans = async function () {
    const listEl = document.getElementById("recentScansList");
    if (!listEl) return;
    try {
      const res = await window.apiFetch("/api/checkins?limit=20");
      const events = res.events || [];
      if (events.length === 0) {
        listEl.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--ink-3);">No scans recorded yet.</td></tr>`;
        return;
      }
      listEl.innerHTML = events.map(ev => `
        <tr>
          <td><strong>${safeHtml(ev.worker.full_name)}</strong> <code>${safeHtml(ev.worker.employee_no || "")}</code></td>
          <td><span class="role-tag" style="background:${ev.direction === 'in' ? 'var(--green-1)' : 'var(--red-1)'}; color:#fff;">${safeHtml(ev.direction.toUpperCase())}</span></td>
          <td>${safeHtml(ev.tunnel.name)}</td>
          <td>${new Date(ev.scanned_at).toLocaleTimeString()}</td>
          <td>${safeHtml(ev.scanner ? ev.scanner.full_name : "System")}</td>
        </tr>
      `).join("");
    } catch (err) {
      console.error("Failed to load scans:", err);
    }
  };

  function stopScannerCamera() {
    if (videoStream) {
      videoStream.getTracks().forEach(track => track.stop());
      videoStream = null;
    }
  }

  // UI initialization after DOM ready
  document.addEventListener("DOMContentLoaded", () => {
    const checkUserInterval = setInterval(() => {
      if (window.currentUser) {
        clearInterval(checkUserInterval);
        const isScanner = ["manager", "safety_officer", "engineer", "foreman"].includes(window.currentUser.role);
        const btnScanner = document.getElementById("btnGateScanner");
        const btnHeadcount = document.getElementById("btnHeadcount");
        if (btnScanner) btnScanner.style.display = isScanner ? "inline-flex" : "none";
        if (btnHeadcount) btnHeadcount.style.display = isScanner ? "inline-flex" : "none";
      }
    }, 200);
  });
})();
