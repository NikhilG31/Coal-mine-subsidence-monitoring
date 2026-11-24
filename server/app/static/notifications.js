/**
 * static/notifications.js
 * Owns Notification Settings, Destinations, Test Dispatches, and Delivery Audit Log UI.
 * Must NOT bypass server authorization, embed secrets, or leak worker identities.
 */

let notifPollInterval = null;
let currentNotifMineId = null;

function escapeNotifHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function getStatusBadge(status) {
  const s = String(status || "").toLowerCase();
  let bg = "#6b7280"; // pending grey
  let fg = "#ffffff";
  if (s === "sent") {
    bg = "#10b981"; // green
  } else if (s === "failed") {
    bg = "#ef4444"; // red
  } else if (s === "skipped") {
    bg = "#f59e0b"; // amber
  }
  return `<span style="background:${bg}; color:${fg}; padding:2px 6px; border-radius:3px; font-size:10px; font-weight:700; text-transform:uppercase;">${escapeNotifHtml(s)}</span>`;
}

async function openNotifModal() {
  const modal = document.getElementById("notifModal");
  if (!modal) return;
  modal.classList.add("open");

  // Populate mines dropdown from available superior mines
  await populateNotifMinesDropdown();
  await loadCurrentMineSettings();
  await loadDeliveryLog();

  // Auto-refresh delivery log every 10 s while open
  if (notifPollInterval) clearInterval(notifPollInterval);
  notifPollInterval = setInterval(loadDeliveryLog, 10000);
}

function closeNotifModal() {
  const modal = document.getElementById("notifModal");
  if (modal) modal.classList.remove("open");
  if (notifPollInterval) {
    clearInterval(notifPollInterval);
    notifPollInterval = null;
  }
}

async function populateNotifMinesDropdown() {
  const select = document.getElementById("notifMineSelect");
  if (!select) return;
  select.innerHTML = "";

  try {
    const res = await apiFetch("/api/mines");
    const mines = Array.isArray(res) ? res : (res.mines || []);
    if (mines.length === 0) {
      select.innerHTML = '<option value="">No mines available</option>';
      return;
    }
    mines.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = `${m.name} (#${m.id})`;
      select.appendChild(opt);
    });

    if (!currentNotifMineId && mines.length > 0) {
      currentNotifMineId = mines[0].id;
    }
    select.value = currentNotifMineId || (mines[0] ? mines[0].id : "");
  } catch (err) {
    console.error("Error fetching mines for notification modal:", err);
  }
}

async function handleNotifMineChange() {
  const select = document.getElementById("notifMineSelect");
  if (select && select.value) {
    currentNotifMineId = parseInt(select.value, 10);
    await loadCurrentMineSettings();
    await loadDeliveryLog();
  }
}

async function loadCurrentMineSettings() {
  if (!currentNotifMineId) return;

  const roleEl = document.getElementById("currentUserRole");
  const role = roleEl ? roleEl.textContent.trim().toLowerCase() : "";
  const isManager = role === "manager";

  // Hide or show Manager controls
  const mgrActions = document.getElementById("notifManagerActions");
  const readOnlyBanner = document.getElementById("notifReadOnlyBanner");
  if (mgrActions) mgrActions.style.display = isManager ? "flex" : "none";
  if (readOnlyBanner) readOnlyBanner.style.display = isManager ? "none" : "block";

  // Disable form inputs for non-managers
  const inputs = document.querySelectorAll("#notifSettingsForm input");
  inputs.forEach((inp) => {
    inp.disabled = !isManager;
  });

  try {
    const settings = await apiFetch(`/api/mines/${currentNotifMineId}/notification-settings`);

    // Channel toggles & destinations
    document.getElementById("cfgTelegramEnabled").checked = !!settings.telegram_enabled;
    document.getElementById("cfgTelegramChatId").value = settings.telegram_chat_id || "";
    document.getElementById("cfgWhatsappEnabled").checked = !!settings.whatsapp_enabled;
    document.getElementById("cfgWhatsappTo").value = settings.whatsapp_to || "";
    document.getElementById("cfgWebhookEnabled").checked = !!settings.webhook_enabled;
    document.getElementById("cfgWebhookUrl").value = settings.webhook_url || "";

    // Event toggles
    document.getElementById("cfgOnIncidentOpen").checked = !!settings.on_incident_open;
    document.getElementById("cfgOnIncidentClose").checked = !!settings.on_incident_close;
    document.getElementById("cfgOnSos").checked = !!settings.on_sos;
    document.getElementById("cfgOnOffline").checked = !!settings.on_offline;

    // Server-unconfigured channels banner
    const conf = settings.channels_configured || {};
    const unconfigured = [];
    if (!conf.telegram) unconfigured.push("Telegram (TELEGRAM_BOT_TOKEN)");
    if (!conf.whatsapp) unconfigured.push("WhatsApp (TWILIO credentials)");
    if (!conf.webhook) unconfigured.push("Webhook (WEBHOOK_SIGNING_SECRET)");

    const banner = document.getElementById("notifUnconfiguredBanner");
    const bannerList = document.getElementById("notifUnconfiguredChannelsList");
    if (banner && bannerList) {
      if (unconfigured.length > 0) {
        bannerList.textContent = unconfigured.join(", ");
        banner.style.display = "block";
      } else {
        banner.style.display = "none";
      }
    }
  } catch (err) {
    console.error("Error loading notification settings:", err);
  }
}

async function handleSaveNotifSettings(event) {
  event.preventDefault();
  if (!currentNotifMineId) return;

  const payload = {
    telegram_enabled: document.getElementById("cfgTelegramEnabled").checked,
    telegram_chat_id: document.getElementById("cfgTelegramChatId").value.trim() || null,
    whatsapp_enabled: document.getElementById("cfgWhatsappEnabled").checked,
    whatsapp_to: document.getElementById("cfgWhatsappTo").value.trim() || null,
    webhook_enabled: document.getElementById("cfgWebhookEnabled").checked,
    webhook_url: document.getElementById("cfgWebhookUrl").value.trim() || null,
    on_incident_open: document.getElementById("cfgOnIncidentOpen").checked,
    on_incident_close: document.getElementById("cfgOnIncidentClose").checked,
    on_sos: document.getElementById("cfgOnSos").checked,
    on_offline: document.getElementById("cfgOnOffline").checked,
  };

  try {
    const updated = await apiFetch(`/api/mines/${currentNotifMineId}/notification-settings`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    alert("Notification settings updated successfully.");
    await loadCurrentMineSettings();
  } catch (err) {
    alert("Error updating settings: " + err.message);
  }
}

async function handleSendTestNotification() {
  if (!currentNotifMineId) return;
  if (!confirm("Dispatch test alert to all enabled channels for this mine?")) return;

  try {
    const res = await apiFetch(`/api/mines/${currentNotifMineId}/notification-settings/test`, {
      method: "POST",
    });
    alert(`Success: ${res.message} (IDs: ${res.ids.join(", ") || "None enqueued"})`);
    await loadDeliveryLog();
  } catch (err) {
    alert("Test notification failed: " + err.message);
  }
}

async function loadDeliveryLog() {
  const container = document.getElementById("notifLogTableContainer");
  if (!container || !currentNotifMineId) return;

  try {
    const logs = await apiFetch(`/api/notifications?mine_id=${currentNotifMineId}&limit=50`);
    if (!logs || logs.length === 0) {
      container.innerHTML = '<div style="font-size:12px; color:var(--ink-3); padding:8px;">No notification logs recorded for this mine.</div>';
      return;
    }

    let html = `
      <table class="neo-table" style="width:100%; font-size:11px; text-align:left;">
        <thead>
          <tr>
            <th style="padding:4px 6px;">ID</th>
            <th style="padding:4px 6px;">Event</th>
            <th style="padding:4px 6px;">Channel</th>
            <th style="padding:4px 6px;">Status</th>
            <th style="padding:4px 6px;">Attempts</th>
            <th style="padding:4px 6px;">Time (UTC)</th>
            <th style="padding:4px 6px;">Result</th>
            <th style="padding:4px 6px;">Message</th>
          </tr>
        </thead>
        <tbody>
    `;

    logs.forEach((r) => {
      const timeStr = r.created_at ? new Date(r.created_at).toISOString().replace("T", " ").substring(0, 19) : "-";
      html += `
        <tr>
          <td style="padding:4px 6px; font-weight:700;">#${r.id}</td>
          <td style="padding:4px 6px;"><span class="badge" style="font-size:10px;">${escapeNotifHtml(r.event)}</span></td>
          <td style="padding:4px 6px; font-weight:600;">${escapeNotifHtml(r.channel)}</td>
          <td style="padding:4px 6px;">${getStatusBadge(r.status)}</td>
          <td style="padding:4px 6px;">${r.attempts}</td>
          <td style="padding:4px 6px; white-space:nowrap;">${escapeNotifHtml(timeStr)}</td>
          <td style="padding:4px 6px; max-width:140px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeNotifHtml(r.result || "")}">${escapeNotifHtml(r.result || "-")}</td>
          <td style="padding:4px 6px; max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeNotifHtml(r.message)}">${escapeNotifHtml(r.message)}</td>
        </tr>
      `;
    });

    html += `
        </tbody>
      </table>
    `;
    container.innerHTML = html;
  } catch (err) {
    console.error("Error loading notification delivery log:", err);
    container.innerHTML = `<div style="color:var(--danger); font-size:12px; padding:8px;">Failed to load delivery log: ${escapeNotifHtml(err.message)}</div>`;
  }
}
