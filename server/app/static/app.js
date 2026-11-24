/**
 * COAL MINE SUBSIDENCE MONITORING - CLIENT ENGINE
 * 
 * Features:
 * - Interactive Leaflet India Map with OpenStreetMap tiles.
 * - Custom Neo-Brutalist markers colored by danger level (Normal, Watch, Warning, Critical).
 * - Low-battery indicators (🪫) rendered above nodes when battery <= 20%.
 * - Unplaced Nodes interactive placement workflow with crosshair positioning.
 * - Real-time telemetry inspector with last 2-3 minutes Chart.js graphs for Tilt X/Y & Vibration RMS.
 * - Excel sheet export (.xlsx) per node and full mine workbook.
 * - Deduplicated in-app alert banner with optional Web Audio synthesizer chime.
 * - Multi-tenant auth, user addition, and notification channel configurations.
 */

// Global State
let map = null;
let markers = {}; // key: "gateway_mac:node_id" -> L.marker
let placedNodesMap = {};
let unplacedNodesList = [];
let gatewaysList = [];
let selectedNode = null;
let placementMode = { active: false, node: null };
let chartTilt = null;
let chartVib = null;
let pollTimer = null;
let audioChimeEnabled = true;
let audioCtx = null;
let lastKnownWorstStatus = "normal";

// Auth State
let currentUser = null;
let allowedMines = [];
let pendingPlacementNode = null;
let pendingPlacementTunnelId = null;
let onboardingMineId = null;

// ==========================================
// INITIALIZATION
// ==========================================

document.addEventListener("DOMContentLoaded", async () => {
  initMap();
  initCharts();
  await checkAuth();
  startPolling();
});

// ==========================================
// AUDIO ALERT SYNTHESIZER (WEB AUDIO API)
// ==========================================

function playAlertChime(level = "critical") {
  if (!audioChimeEnabled) return;
  try {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === "suspended") {
      audioCtx.resume();
    }
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain);
    gain.connect(audioCtx.destination);

    if (level === "critical") {
      osc.type = "sawtooth";
      osc.frequency.setValueAtTime(880, audioCtx.currentTime); // A5
      osc.frequency.exponentialRampToValueAtTime(440, audioCtx.currentTime + 0.3);
      gain.gain.setValueAtTime(0.3, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.3);
      osc.start(audioCtx.currentTime);
      osc.stop(audioCtx.currentTime + 0.3);
    } else if (level === "warning") {
      osc.type = "sine";
      osc.frequency.setValueAtTime(659, audioCtx.currentTime); // E5
      osc.frequency.exponentialRampToValueAtTime(523, audioCtx.currentTime + 0.25);
      gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.25);
      osc.start(audioCtx.currentTime);
      osc.stop(audioCtx.currentTime + 0.25);
    }
  } catch (e) {
    console.warn("Audio chime failed:", e);
  }
}

function toggleAudioChime() {
  audioChimeEnabled = !audioChimeEnabled;
  const btn = document.getElementById("btnAudioToggle");
  btn.textContent = audioChimeEnabled ? "🔊 Sound: ON" : "🔇 Sound: OFF";
  btn.className = audioChimeEnabled ? "neo-btn btn-small" : "neo-btn btn-small btn-danger";
}

// ==========================================
// MAP & LEAFLET ENGINE
// ==========================================

function initMap() {
  // Center roughly over Central/Eastern India's major coal belts (Jharkhand / WB / MP)
  map = L.map("map", {
    center: [23.5, 84.5],
    zoom: 6,
    zoomControl: true,
  });

  // OpenStreetMap Tile Layer
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | DGMS Coal Mine Monitor'
  }).addTo(map);

  // Map Click Listener for Node Placement Mode
  map.on("click", (e) => {
    if (placementMode.active && placementMode.node) {
      handleMapClickPlacement(e.latlng.lat, e.latlng.lng);
    }
  });
}

function panMapTo(lat, lng, zoom = 14) {
  if (map) {
    map.flyTo([lat, lng], zoom, { duration: 1.2 });
  }
}

function resetIndiaMap() {
  if (map) {
    map.flyTo([23.5, 84.5], 6, { duration: 1.2 });
  }
}

function createNeoMarkerHtml(node) {
  const isOffline = node.online === false;
  const level = isOffline ? "offline" : (node.status || "normal").toLowerCase();
  const isLowBatt = !isOffline && (node.low_battery || (node.battery_percentage <= 20.0));
  const battBadge = isOffline
    ? `<div class="marker-battery-low" style="background:#4b5563; color:#fff; border-color:#000;">OFFLINE</div>`
    : (isLowBatt ? `<div class="marker-battery-low">🪫 ${node.battery_percentage.toFixed(0)}%</div>` : "");
  const containerStyle = isOffline ? 'filter: grayscale(100%); opacity: 0.65;' : '';

  return `
    <div class="neo-marker-container" style="${containerStyle}">
      ${battBadge}
      <div class="neo-marker-pin ${level}">
        <span>N${node.node_id}</span>
      </div>
    </div>
  `;
}

function updateMapMarkers(nodes) {
  const activeKeys = new Set();
  let placedCount = 0;

  nodes.forEach(node => {
    if (!node.placed || node.latitude == null || node.longitude == null) return;
    placedCount++;

    const key = `${node.gateway_mac_address}:${node.node_id}`;
    activeKeys.add(key);
    placedNodesMap[key] = node;

    const icon = L.divIcon({
      className: "neo-custom-marker",
      html: createNeoMarkerHtml(node),
      iconSize: [46, 36],
      iconAnchor: [23, 30]
    });

    if (markers[key]) {
      // Update position and icon
      markers[key].setLatLng([node.latitude, node.longitude]);
      markers[key].setIcon(icon);
    } else {
      // Create new marker
      const marker = L.marker([node.latitude, node.longitude], { icon })
        .addTo(map)
        .on("click", () => {
          openInspectorDrawer(node);
        });

      // Neo-brutalist tooltip
      marker.bindTooltip(`
        <strong>${node.location_name || `Node ${node.node_id}`}</strong><br/>
        GW: ${node.gateway_mac_address}<br/>
        Status: <strong>${(node.status || 'NORMAL').toUpperCase()}</strong><br/>
        Tilt: (${node.tilt_x.toFixed(2)}°, ${node.tilt_y.toFixed(2)}°)<br/>
        Vib RMS: ${node.vibration_magnitude.toFixed(3)}g
      `, { offset: [0, -25] });

      markers[key] = marker;
    }
  });

  // Remove stale markers
  Object.keys(markers).forEach(k => {
    if (!activeKeys.has(k)) {
      map.removeLayer(markers[k]);
      delete markers[k];
      delete placedNodesMap[k];
    }
  });

  document.getElementById("placedCountLabel").textContent = `Placed Nodes: ${placedCount}`;
}

// ==========================================
// INTERACTIVE PLACEMENT MODE WORKFLOW
// ==========================================

async function startPlacementWorkflow(node) {
  if (!node) return;
  pendingPlacementNode = node;

  try {
    let mineId = node.mine_id;
    if (!mineId && allowedMines.length === 1) {
      mineId = allowedMines[0];
    }
    if (!mineId) {
      const mRes = await apiFetch("/api/mines");
      if (mRes.mines && mRes.mines.length > 0) {
        mineId = mRes.mines[0].id;
      }
    }
    if (!mineId) {
      alert("No mine associated with this node. Please configure a mine first.");
      return;
    }

    const tRes = await apiFetch(`/api/mines/${mineId}/tunnels`);
    const tunnels = tRes.tunnels || [];
    if (!tunnels.length) {
      alert("No underground tunnels found in this mine. Please add a tunnel first under 'Mines & Gateways'.");
      return;
    }

    const sel = document.getElementById("placementTunnelSelect");
    sel.innerHTML = tunnels.map(t =>
      `<option value="${t.id}">${t.name} (${t.code}) - Depth: ${t.depth_m || 0}m</option>`
    ).join("");

    document.getElementById("tunnelPickerModal").classList.add("open");
  } catch (err) {
    alert("Could not load tunnels for placement: " + err.message);
  }
}

function closeTunnelPickerModal() {
  document.getElementById("tunnelPickerModal").classList.remove("open");
}

function confirmTunnelSelectionAndStartMapMode() {
  const sel = document.getElementById("placementTunnelSelect");
  if (!sel.value) {
    alert("Please select a tunnel.");
    return;
  }
  pendingPlacementTunnelId = parseInt(sel.value, 10);
  closeTunnelPickerModal();
  startPlacementMode(pendingPlacementNode);
}

function startPlacementMode(node) {
  placementMode.active = true;
  placementMode.node = node;

  document.body.classList.add("placement-cursor");
  const banner = document.getElementById("placementModeBanner");
  const bannerText = document.getElementById("placementBannerText");
  bannerText.textContent = `🎯 PLACEMENT MODE ACTIVE: Click anywhere on the map to place Node #${node.node_id} (Tunnel ID: ${pendingPlacementTunnelId})`;
  banner.style.display = "block";

  // Auto pan to known coal belt if zoomed out
  if (map.getZoom() < 10) {
    panMapTo(23.7438, 86.4116, 12);
  }
}

function cancelPlacementMode() {
  placementMode.active = false;
  placementMode.node = null;
  pendingPlacementNode = null;
  pendingPlacementTunnelId = null;
  document.body.classList.remove("placement-cursor");
  document.getElementById("placementModeBanner").style.display = "none";
}

async function handleMapClickPlacement(lat, lng) {
  const node = placementMode.node;
  if (!node) return;

  try {
    const res = await apiFetch(`/api/nodes/${node.id}/placement`, {
      method: "PATCH",
      body: JSON.stringify({
        tunnel_id: pendingPlacementTunnelId,
        lat: lat,
        lon: lng,
      }),
    });

    cancelPlacementMode();
    await fetchDashboardData();

    // Open inspector for placed node
    const updatedNode = { ...node, latitude: lat, longitude: lng, tunnel_id: pendingPlacementTunnelId, placed: true };
    openInspectorDrawer(updatedNode);
  } catch (err) {
    alert("Placement failed: " + err.message);
  }
}

function repositionCurrentNode() {
  if (!selectedNode) return;
  closeInspectorDrawer();
  startPlacementWorkflow(selectedNode);
}

async function unplaceCurrentNode() {
  if (!selectedNode) return;
  if (!confirm(`Remove Node #${selectedNode.node_id} from map? It will return to the Unplaced Nodes list.`)) return;

  try {
    await apiFetch(`/api/nodes/${selectedNode.id}/placement`, {
      method: "DELETE",
    });
    closeInspectorDrawer();
    await fetchDashboardData();
  } catch (err) {
    alert("Error unplacing node: " + err.message);
  }
}

// ==========================================
// TELEMETRY INSPECTOR DRAWER & CHARTS
// ==========================================

function initCharts() {
  const ctxTilt = document.getElementById("chartTiltCanvas").getContext("2d");
  const ctxVib = document.getElementById("chartVibCanvas").getContext("2d");

  // Chart 1: Tilt Deviation (Pitch X & Roll Y)
  chartTilt = new Chart(ctxTilt, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Tilt X (Pitch)",
          data: [],
          borderColor: "#e0651e",
          backgroundColor: "#e0651e",
          borderWidth: 2,
          pointRadius: 2,
          tension: 0.1
        },
        {
          label: "Tilt Y (Roll)",
          data: [],
          borderColor: "#2d7a45",
          backgroundColor: "#2d7a45",
          borderWidth: 2,
          pointRadius: 2,
          tension: 0.1
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { intersect: false, mode: "index" },
      plugins: {
        legend: {
          position: "top",
          labels: { boxWidth: 10, font: { family: "JetBrains Mono", size: 9 }, padding: 6 }
        }
      },
      scales: {
        x: {
          title: {
            display: true,
            text: "Time",
            font: { family: "JetBrains Mono", size: 10, weight: "bold" },
            color: "#1a1108",
            padding: { top: 2 }
          },
          ticks: { font: { family: "JetBrains Mono", size: 8 }, maxTicksLimit: 5, color: "#3a2a1c" },
          grid: { color: "#eadfc4" }
        },
        y: {
          title: {
            display: true,
            text: "Degrees (°)",
            font: { family: "JetBrains Mono", size: 10, weight: "bold" },
            color: "#1a1108",
            padding: { bottom: 2 }
          },
          ticks: { font: { family: "JetBrains Mono", size: 8 }, color: "#3a2a1c" },
          grid: { color: "#eadfc4" }
        }
      }
    }
  });

  // Chart 2: Vibration Magnitude
  chartVib = new Chart(ctxVib, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Vibration RMS",
          data: [],
          borderColor: "#8b1a1a",
          backgroundColor: "rgba(139, 26, 26, 0.15)",
          fill: true,
          borderWidth: 2,
          pointRadius: 2,
          tension: 0.1
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { intersect: false, mode: "index" },
      plugins: {
        legend: {
          position: "top",
          labels: { boxWidth: 10, font: { family: "JetBrains Mono", size: 9 }, padding: 6 }
        }
      },
      scales: {
        x: {
          title: {
            display: true,
            text: "Time",
            font: { family: "JetBrains Mono", size: 10, weight: "bold" },
            color: "#1a1108",
            padding: { top: 2 }
          },
          ticks: { font: { family: "JetBrains Mono", size: 8 }, maxTicksLimit: 5, color: "#3a2a1c" },
          grid: { color: "#eadfc4" }
        },
        y: {
          title: {
            display: true,
            text: "Magnitude (g)",
            font: { family: "JetBrains Mono", size: 10, weight: "bold" },
            color: "#1a1108",
            padding: { bottom: 2 }
          },
          ticks: { font: { family: "JetBrains Mono", size: 8 }, color: "#3a2a1c" },
          grid: { color: "#eadfc4" }
        }
      }
    }
  });
}

async function openInspectorDrawer(node) {
  selectedNode = node;
  const drawer = document.getElementById("inspectorDrawer");
  const backdrop = document.getElementById("drawerBackdrop");

  drawer.classList.add("open");
  backdrop.style.display = "block";

  // Fill in Header & Status Stamp
  const titleText = node.location_name ? `${node.location_name.toUpperCase()} (#${node.node_id})` : `NODE #${node.node_id}`;
  document.getElementById("drawerNodeTitle").textContent = titleText;
  document.getElementById("drawerGatewayMac").textContent = `GW MAC: ${node.gateway_mac_address}`;

  const isOffline = node.online === false;
  const status = isOffline ? "offline" : (node.status || "normal").toLowerCase();
  const stamp = document.getElementById("drawerStatusStamp");
  stamp.textContent = isOffline ? "STATUS: OFFLINE" : `STATUS: ${status.toUpperCase()}`;
  stamp.className = `status-stamp ${status}`;

  // Battery gauge
  const battV = node.battery_voltage ? node.battery_voltage.toFixed(2) : "4.00";
  const battPct = node.battery_percentage ? node.battery_percentage.toFixed(0) : "100";
  const battBadge = document.getElementById("drawerBatteryBadge");
  const isLowBatt = node.low_battery || (node.battery_percentage <= 20.0);
  battBadge.innerHTML = `${isLowBatt ? "🪫" : "🔋"} ${battV} V · ${battPct}%`;
  battBadge.style.color = isLowBatt ? "var(--blood)" : "var(--ink)";

  document.getElementById("drawerLowBattAlert").style.display = isLowBatt ? "flex" : "none";

  // Quick metrics
  document.getElementById("drawerTiltX").textContent = `${node.tilt_x >= 0 ? "+" : ""}${node.tilt_x.toFixed(2)}°`;
  document.getElementById("drawerTiltY").textContent = `${node.tilt_y >= 0 ? "+" : ""}${node.tilt_y.toFixed(2)}°`;
  document.getElementById("drawerVibRMS").textContent = `${node.vibration_magnitude.toFixed(3)} g`;
  
  const shockEl = document.getElementById("drawerShockSensor");
  shockEl.textContent = node.vibration_detected ? "SHOCK TRIGGERED" : "Normal";
  shockEl.style.color = node.vibration_detected ? "var(--blood)" : "var(--ink)";

  // Reasons & Meta
  const reasons = (node.reasons && node.reasons.length) ? node.reasons.join(" • ") : "All metrics within baseline";
  document.getElementById("drawerReasons").textContent = reasons;
  document.getElementById("drawerCounter").textContent = node.counter || "0";
  document.getElementById("drawerAge").textContent = `${node.age_s != null ? node.age_s.toFixed(0) : "0"}s ago`;

  // Placement info
  const coordsEl = document.getElementById("drawerLocationCoords");
  if (node.placed && node.latitude != null) {
    coordsEl.textContent = `Lat: ${node.latitude.toFixed(5)}, Lng: ${node.longitude.toFixed(5)}`;
  } else {
    coordsEl.textContent = "Unplaced on surface map";
  }

  // Update Excel Export button
  const dlBtn = document.getElementById("btnDownloadNodeExcel");
  dlBtn.href = `/api/export.xlsx?gateway=${encodeURIComponent(node.gateway_mac_address)}&node_id=${node.node_id}`;

  // Fetch telemetry history (last 2-3 mins = 36 points)
  await updateInspectorCharts(node.gateway_mac_address, node.node_id);
}

function closeInspectorDrawer() {
  document.getElementById("inspectorDrawer").classList.remove("open");
  document.getElementById("drawerBackdrop").style.display = "none";
  selectedNode = null;
}

async function updateInspectorCharts(gatewayMac, nodeId) {
  try {
    const data = await apiFetch(`/api/history?gateway=${encodeURIComponent(gatewayMac)}&node_id=${nodeId}&limit=15`);
    const readings = data.readings || [];

    const labels = readings.map(r => {
      const d = new Date(r.ts * 1000);
      return `${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
    });

    const tiltXData = readings.map(r => r.tilt_x);
    const tiltYData = readings.map(r => r.tilt_y);
    const vibData = readings.map(r => r.vibration_magnitude);

    if (chartTilt) {
      chartTilt.data.labels = labels;
      chartTilt.data.datasets[0].data = tiltXData;
      chartTilt.data.datasets[1].data = tiltYData;
      chartTilt.update();
    }

    if (chartVib) {
      chartVib.data.labels = labels;
      chartVib.data.datasets[0].data = vibData;
      chartVib.update();
    }
  } catch (err) {
    console.error("Failed to load telemetry history:", err);
  }
}

// ==========================================
// UNPLACED NODES LIST & GATEWAY INVENTORY
// ==========================================

function renderUnplacedNodes(unplaced) {
  const container = document.getElementById("unplacedList");
  const badge = document.getElementById("unplacedBadge");
  badge.textContent = unplaced.length;

  if (!unplaced.length) {
    container.innerHTML = `<div style="font-family:var(--mono); font-size:12px; color:var(--green-2); font-weight:700;">✓ All active nodes are placed on the map.</div>`;
    return;
  }

  container.innerHTML = unplaced.map(n => `
    <div class="unplaced-item">
      <div class="unplaced-meta">
        <span class="unplaced-title">Node #${n.node_id}</span>
        <span class="unplaced-sub">GW: ${n.gateway_mac_address}</span>
        <span style="font-size:10px; font-family:var(--mono);">
          Tilt: (${n.tilt_x.toFixed(2)}°, ${n.tilt_y.toFixed(2)}°) · Vib: ${n.vibration_magnitude.toFixed(3)}g
        </span>
      </div>
      <div>
        <button class="neo-btn btn-small btn-primary" onclick="startPlacementWorkflow(${JSON.stringify(n).replace(/"/g, '&quot;')})">
          📍 Place on Map
        </button>
      </div>
    </div>
  `).join("");
}

function renderGatewaysTree(gateways) {
  const container = document.getElementById("gatewaysTree");
  if (!gateways.length) {
    container.innerHTML = `<div style="font-family:var(--mono); font-size:12px; color:var(--ink-3);">No active gateways reporting.</div>`;
    return;
  }

  container.innerHTML = gateways.map(gw => {
    const isOnline = gw.online;
    const worst = (gw.worst_status || "normal").toLowerCase();

    return `
      <div class="gateway-branch-box">
        <div class="gateway-branch-header">
          <div>
            <div class="gateway-title">${gw.gateway_name || "Gateway"}</div>
            <div class="gateway-mac-tag">MAC: ${gw.gateway_mac_address}</div>
          </div>
          <div>
            <span class="badge-danger-level badge-${worst}">${worst}</span>
          </div>
        </div>
        <div class="nodes-mini-grid">
          ${gw.nodes.map(n => {
            const isOffline = n.online === false;
            const nLevel = isOffline ? "offline" : (n.status || "normal").toLowerCase();
            const isSel = selectedNode && selectedNode.gateway_mac_address === n.gateway_mac_address && selectedNode.node_id === n.node_id;
            const chipStyle = isOffline ? 'opacity: 0.6; filter: grayscale(100%); background: #e5e7eb;' : '';
            return `
              <div class="node-chip ${isSel ? 'selected' : ''} ${isOffline ? 'offline' : ''}" style="${chipStyle}" onclick="openInspectorDrawer(${JSON.stringify(n).replace(/"/g, '&quot;')})">
                <div class="node-chip-header">
                  <span class="node-chip-id">N#${n.node_id}${isOffline ? ' (OFFLINE)' : ''}</span>
                  <span class="node-chip-status" style="background:${isOffline ? '#6b7280' : n.color};"></span>
                </div>
                <div class="node-chip-val">Tilt: ${Math.max(Math.abs(n.tilt_x), Math.abs(n.tilt_y)).toFixed(2)}°</div>
                <div class="node-chip-val">Vib: ${n.vibration_magnitude.toFixed(3)}g</div>
                ${n.low_battery ? '<div style="font-size:9px; color:var(--blood); font-weight:700;">🪫 Low Batt</div>' : ''}
              </div>
            `;
          }).join("")}
        </div>
      </div>
    `;
  }).join("");
}

// ==========================================
// ALERTS SYSTEM & DEDUPLICATION DISPLAY
// ==========================================

let alertsPollTimer = null;

async function fetchAlerts() {
  const isSuperior = currentUser && ["manager", "safety_officer", "engineer"].includes(currentUser.role);
  const banner = document.getElementById("criticalAlertBanner");
  const alertsBox = document.getElementById("alertsTableBody")?.closest(".zine-box");

  if (!isSuperior) {
    if (banner) banner.style.display = "none";
    if (alertsBox) alertsBox.style.display = "none";
    return;
  }

  if (alertsBox) alertsBox.style.display = "block";

  try {
    const data = await apiFetch("/api/alerts?status=open");
    const alerts = data.alerts || [];
    renderAlertsTable(alerts);

    const unacked = alerts.filter(a => !a.acknowledged_at && !a.resolved_at);
    if (banner) {
      if (unacked.length > 0) {
        banner.style.display = "block";
        const bannerText = document.getElementById("alertBannerText");
        if (bannerText) {
          bannerText.innerHTML = `⚠️ ${unacked.length} active alert${unacked.length > 1 ? 's' : ''} <button class="neo-btn btn-small" style="background:#ffffff; color:#000; margin-left:8px;" onclick="scrollToAlerts()">View</button>`;
        }
      } else {
        banner.style.display = "none";
      }
    }
  } catch (err) {
    console.warn("Alerts fetch error:", err);
  }
}

function scrollToAlerts() {
  const alertsBox = document.getElementById("alertsTableBody")?.closest(".zine-box");
  if (alertsBox) {
    alertsBox.scrollIntoView({ behavior: "smooth" });
  }
}

function renderAlertsTable(alerts) {
  const tbody = document.getElementById("alertsTableBody");
  if (!tbody) return;
  if (!alerts.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--ink-3);">No active hazard or connectivity alerts.</td></tr>`;
    return;
  }

  tbody.innerHTML = alerts.map(a => {
    const ackBtn = a.acknowledged_at 
      ? `<span style="color:var(--green); font-weight:700;">✓ Acked by ${a.acknowledged_by_name || 'Staff'}</span>` 
      : `<button class="neo-btn btn-small" onclick="acknowledgeAlert(${a.id})">Acknowledge</button>`;

    const timeStr = a.opened_at ? new Date(a.opened_at).toLocaleTimeString() : "N/A";
    const entityStr = a.node_id ? `Node #${a.node_id}` : `Gateway #${a.gateway_id}`;

    return `
      <tr>
        <td style="white-space:nowrap;">${timeStr}</td>
        <td><strong>${entityStr}</strong></td>
        <td><code>GW #${a.gateway_id}</code></td>
        <td><span class="badge-danger-level badge-critical">${a.kind}</span></td>
        <td>${a.title}</td>
        <td>${ackBtn}</td>
      </tr>
    `;
  }).join("");
}

async function acknowledgeAlert(alertId) {
  try {
    await apiFetch(`/api/alerts/${alertId}/ack`, { method: "POST" });
    await fetchAlerts();
  } catch (err) {
    alert("Ack error: " + err.message);
  }
}

function evaluateWorstDangerAndNotify(nodes) {
  let worst = "normal";
  let worstNode = null;
  const ranks = { "normal": 0, "watch": 1, "warning": 2, "critical": 3 };

  nodes.forEach(n => {
    const s = (n.status || "normal").toLowerCase();
    if (ranks[s] > ranks[worst]) {
      worst = s;
      worstNode = n;
    }
  });

  const banner = document.getElementById("criticalAlertBanner");
  const bannerText = document.getElementById("alertBannerText");

  if (ranks[worst] >= ranks["warning"]) {
    banner.style.display = "block";
    bannerText.innerHTML = `⚠️ <strong>SUBSIDENCE HAZARD [${worst.toUpperCase()}]:</strong> Node #${worstNode.node_id} (${worstNode.location_name || worstNode.gateway_mac_address}) triggered: Tilt (${worstNode.tilt_x.toFixed(2)}°, ${worstNode.tilt_y.toFixed(2)}°), Vib ${worstNode.vibration_magnitude.toFixed(3)}g!`;
    
    // Play sound chime if status elevated
    if (ranks[worst] > ranks[lastKnownWorstStatus]) {
      playAlertChime(worst);
    }
  } else {
    banner.style.display = "none";
  }

  lastKnownWorstStatus = worst;
}

function dismissAlertBanner() {
  document.getElementById("criticalAlertBanner").style.display = "none";
}

// ==========================================
// POLLING & REAL-TIME DASHBOARD DATA
// ==========================================

async function fetchDashboardData() {
  try {
    // 1. Fetch nodes (with placement info)
    const nodesRes = await apiFetch("/api/nodes");
    const nodes = nodesRes.nodes || [];

    // 2. Update Map Markers
    updateMapMarkers(nodes);

    // 3. Unplaced nodes
    const unplaced = nodes.filter(n => !n.placed);
    renderUnplacedNodes(unplaced);

    // 4. Gateways tree
    const gwRes = await apiFetch("/api/gateways");
    renderGatewaysTree(gwRes.gateways || []);

    // 5. Check danger level alerts & banner
    evaluateWorstDangerAndNotify(nodes);

    // 6. If inspector drawer is currently open for a node, refresh its charts live
    if (selectedNode) {
      const currentFresh = nodes.find(n => n.gateway_mac_address === selectedNode.gateway_mac_address && n.node_id === selectedNode.node_id);
      if (currentFresh) {
        // Re-read telemetry without resetting user selection
        document.getElementById("drawerTiltX").textContent = `${currentFresh.tilt_x >= 0 ? "+" : ""}${currentFresh.tilt_x.toFixed(2)}°`;
        document.getElementById("drawerTiltY").textContent = `${currentFresh.tilt_y >= 0 ? "+" : ""}${currentFresh.tilt_y.toFixed(2)}°`;
        document.getElementById("drawerVibRMS").textContent = `${currentFresh.vibration_magnitude.toFixed(3)} g`;
        document.getElementById("drawerCounter").textContent = currentFresh.counter || "0";
        document.getElementById("drawerAge").textContent = `${currentFresh.age_s != null ? currentFresh.age_s.toFixed(0) : "0"}s ago`;
      }
      await updateInspectorCharts(selectedNode.gateway_mac_address, selectedNode.node_id);
    }
  } catch (err) {
    console.warn("Dashboard sync error:", err);
  }
}

function startPolling() {
  fetchDashboardData();
  fetchAlerts();

  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    fetchDashboardData();
  }, 4000); // 4-second polling to capture 5-second ESP-NOW intervals

  if (alertsPollTimer) clearInterval(alertsPollTimer);
  alertsPollTimer = setInterval(() => {
    fetchAlerts();
  }, 10000); // 10-second polling for alerts
}

// ==========================================
// AUTHENTICATION & MULTI-TENANT ACCESS
// ==========================================

async function apiFetch(url, options = {}) {
  const headers = options.headers || {};
  if (!headers["Content-Type"] && options.body && typeof options.body === "string") {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(url, { ...options, headers, credentials: "same-origin" });
  if (res.status === 401) {
    openAuthModal();
    throw new Error("Authentication required");
  }
  if (res.status === 403) {
    const errData = await res.json().catch(() => ({}));
    if (
      errData.code === "password_change_required" ||
      (errData.detail && errData.detail.code === "password_change_required")
    ) {
      openChangePasswordModal();
      throw new Error("Password change required");
    }
    throw new Error(errData.detail || "Forbidden: insufficient permissions");
  }
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.detail || errData.message || `Request failed (${res.status})`);
  }
  return res.json();
}

async function checkAuth() {
  try {
    const data = await apiFetch("/api/auth/me");
    currentUser = data.user;
    allowedMines = data.allowed_mine_ids || [];
    updateUserBadge(currentUser);
    if (currentUser.force_pw_change) {
      openChangePasswordModal();
    } else if (allowedMines.length === 0) {
      if (currentUser.role === "manager") {
        openOnboardingModal();
      } else {
        openNoMinesModal();
      }
    }
  } catch (e) {
    openAuthModal();
  }
}

function updateUserBadge(user) {
  if (!user) return;
  document.getElementById("currentUsername").textContent = user.username.toUpperCase();
  const roleEl = document.getElementById("currentUserRole");
  roleEl.textContent = user.role.toUpperCase();
  roleEl.style.background = user.role === "manager" ? "var(--saffron)" : "var(--ink)";
  document.getElementById("btnOpenAuth").style.display = "none";
  document.getElementById("btnLogout").style.display = "inline-flex";

  // Role-based cosmetic visibility
  const isManager = user.role === "manager";
  const isSuperior = ["manager", "safety_officer", "engineer"].includes(user.role);
  const isWorker = ["foreman", "miner"].includes(user.role);

  const btnWorkers = document.getElementById("btnWorkers");
  if (btnWorkers) btnWorkers.style.display = isSuperior ? "inline-flex" : "none";

  const btnMyHours = document.getElementById("btnMyHours");
  if (btnMyHours) btnMyHours.style.display = isWorker ? "inline-flex" : "none";

  const btnAddUser = document.getElementById("btnAddUser");
  if (btnAddUser) btnAddUser.style.display = isManager ? "inline-flex" : "none";

  const btnManageMines = document.getElementById("btnManageMines");
  if (btnManageMines) btnManageMines.style.display = isManager ? "inline-flex" : "none";

  const btnNotif = document.getElementById("btnNotifConfig");
  if (btnNotif) btnNotif.style.display = isSuperior ? "inline-flex" : "none";

  const demoBar = document.querySelector(".demo-bar");
  if (demoBar) demoBar.style.display = isManager ? "block" : "none";

  // Hide placement controls for workers
  const unplacedCard = document.getElementById("unplacedList")?.closest(".zine-box");
  if (unplacedCard) unplacedCard.style.display = isWorker ? "none" : "block";

  const gatewaysCard = document.getElementById("gatewaysTree")?.closest(".zine-box");
  if (gatewaysCard) gatewaysCard.style.display = isWorker ? "none" : "block";

  const btnReposition = document.getElementById("btnReposition");
  if (btnReposition) btnReposition.style.display = isWorker ? "none" : "inline-block";

  const btnUnplace = document.querySelector("button[onclick='unplaceCurrentNode()']");
  if (btnUnplace) btnUnplace.style.display = isWorker ? "none" : "inline-block";

  // Alerts visibility: workers do not see alert banner or list
  const alertsCard = document.getElementById("alertsTableBody")?.closest(".zine-box");
  if (alertsCard) alertsCard.style.display = isWorker ? "none" : "block";
  const banner = document.getElementById("criticalAlertBanner");
  if (isWorker && banner) banner.style.display = "none";
  if (isSuperior) fetchAlerts();
}

function openAuthModal() {
  document.getElementById("authModal").classList.add("open");
}

function closeAuthModal() {
  document.getElementById("authModal").classList.remove("open");
}

function openChangePasswordModal() {
  document.getElementById("changePasswordModal").classList.add("open");
}

function closeChangePasswordModal() {
  document.getElementById("changePasswordModal").classList.remove("open");
}

async function handleAuthSubmit(e) {
  e.preventDefault();
  const u = document.getElementById("authUsername").value.trim();
  const p = document.getElementById("authPassword").value;

  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: u, password: p }),
      credentials: "same-origin",
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Authentication failed");
    }

    const data = await res.json();
    currentUser = data.user;
    updateUserBadge(currentUser);
    closeAuthModal();

    if (data.force_pw_change) {
      openChangePasswordModal();
    } else {
      fetchDashboardData();
    }
  } catch (err) {
    alert("Auth error: " + err.message);
  }
}

async function handleChangePasswordSubmit(e) {
  e.preventDefault();
  const cur = document.getElementById("cpCurrentPassword").value;
  const newP = document.getElementById("cpNewPassword").value;
  const confP = document.getElementById("cpConfirmPassword").value;

  if (newP !== confP) {
    alert("New password confirmation does not match.");
    return;
  }
  if (newP.length < 10) {
    alert("New password must be at least 10 characters long.");
    return;
  }

  try {
    await apiFetch("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password: cur, new_password: newP }),
    });
    alert("Password updated successfully.");
    closeChangePasswordModal();
    await checkAuth();
    fetchDashboardData();
  } catch (err) {
    alert("Password change failed: " + err.message);
  }
}

async function handleLogout() {
  try {
    await apiFetch("/api/auth/logout", { method: "POST" });
  } catch (e) {}
  currentUser = null;
  location.reload();
}

// Add User Modal (Manager Only)
function openAddUserModal() {
  document.getElementById("addUserModal").classList.add("open");
}
function closeAddUserModal() {
  document.getElementById("addUserModal").classList.remove("open");
}

async function handleCreateUserSubmit(e) {
  e.preventDefault();
  const u = document.getElementById("newUsername").value.trim();
  const fn = document.getElementById("newFullName").value.trim();
  const r = document.getElementById("newRole").value;
  const p = document.getElementById("newPassword").value;

  try {
    await apiFetch("/api/users", {
      method: "POST",
      body: JSON.stringify({
        username: u,
        full_name: fn,
        role: r,
        initial_password: p,
        mine_ids: [],
      }),
    });
    alert(`User '${u}' created successfully with role '${r}'.`);
    closeAddUserModal();
  } catch (err) {
    alert("Failed to create user: " + err.message);
  }
}

// ==========================================
// NOTIFICATION CHANNELS & AUDIT LOG (HANDLED IN static/notifications.js)
// ==========================================

// ==========================================
// DEMO & SIMULATOR CONTROLS
// ==========================================

async function triggerSeedData() {
  try {
    const res = await apiFetch("/api/demo/seed", { method: "POST" });
    alert("Coal Mine Datasets Seeded! Jharia & Raniganj fields initialized with placed and unplaced nodes.");
    panMapTo(23.7438, 86.4116, 13); // Pan to Jharia
    fetchDashboardData();
  } catch (err) {
    alert("Seed error: " + err.message);
  }
}

let liveSimRunning = false;
async function toggleLiveSimulator() {
  try {
    const res = await apiFetch("/api/demo/toggle-live", { method: "POST" });
    liveSimRunning = res.running;
    const btn = document.getElementById("btnLiveToggle");
    btn.textContent = liveSimRunning ? "⏸️ Live 5s Feed: ON" : "▶️ Live 5s Feed: OFF";
    btn.className = liveSimRunning ? "neo-btn btn-small btn-primary" : "neo-btn btn-small btn-success";
  } catch (err) {
    alert("Toggle error: " + err.message);
  }
}

function openHazardModal() {
  const modal = document.getElementById("hazardModal");
  const select = document.getElementById("hazardNodeSelect");
  modal.classList.add("open");

  // Populate node selector with placed nodes
  const options = Object.values(placedNodesMap).map(n => 
    `<option value="${n.gateway_mac_address}:${n.node_id}">Node #${n.node_id} (${n.location_name}) - GW: ${n.gateway_mac_address}</option>`
  );
  select.innerHTML = options.length ? options.join("") : `<option value="A4:C1:38:90:12:01:3">Node #3 (Jharia Goaf Caved Zone)</option>`;
}

function closeHazardModal() {
  document.getElementById("hazardModal").classList.remove("open");
}

async function handleTriggerHazardSubmit(e) {
  e.preventDefault();
  const val = document.getElementById("hazardNodeSelect").value;
  const idx = val.lastIndexOf(":");
  const mac = val.slice(0, idx);
  const nodeId = val.slice(idx + 1);
  const level = document.getElementById("hazardLevelSelect").value;

  try {
    await apiFetch("/api/demo/trigger-hazard", {
      method: "POST",
      body: JSON.stringify({
        gateway_mac_address: mac,
        node_id: parseInt(nodeId, 10),
        level: level
      })
    });
    closeHazardModal();
    await fetchDashboardData();
    const key = `${mac}:${nodeId}`;
    if (placedNodesMap[key]) {
      openInspectorDrawer(placedNodesMap[key]);
    }
  } catch (err) {
    alert("Hazard simulation error: " + err.message);
  }
}

// ==========================================
// GATEWAY KEY DISPLAY MODAL
// ==========================================

function showGatewayKey(apiKey) {
  document.getElementById("displayedGatewayKey").value = apiKey;
  document.getElementById("gatewayKeyModal").classList.add("open");
}

function copyGatewayKey() {
  const input = document.getElementById("displayedGatewayKey");
  input.select();
  navigator.clipboard.writeText(input.value);
  alert("Gateway API key copied to clipboard! Save it in your gateway firmware / environment.");
}

function closeGatewayKeyModal() {
  document.getElementById("gatewayKeyModal").classList.remove("open");
}

// ==========================================
// MANAGER ONBOARDING WIZARD & RESTRICTED VIEWS
// ==========================================

function openOnboardingModal() {
  document.getElementById("onboardingModal").classList.add("open");
  document.getElementById("obStep1").style.display = "block";
  document.getElementById("obStep2").style.display = "none";
  document.getElementById("obStep3").style.display = "none";
  document.getElementById("obStep4").style.display = "none";
}

function closeOnboardingModal() {
  document.getElementById("onboardingModal").classList.remove("open");
}

function openNoMinesModal() {
  document.getElementById("noMinesModal").classList.add("open");
}

async function handleOnboardingMineSubmit(e) {
  e.preventDefault();
  const name = document.getElementById("obMineName").value.trim();
  const sub = document.getElementById("obMineSubsidiary").value.trim();
  const lat = parseFloat(document.getElementById("obMineLat").value) || null;
  const lon = parseFloat(document.getElementById("obMineLon").value) || null;

  try {
    const res = await apiFetch("/api/mines", {
      method: "POST",
      body: JSON.stringify({
        name: name,
        subsidiary: sub || null,
        lat: lat,
        lon: lon,
      }),
    });
    onboardingMineId = res.id;
    document.getElementById("obStep1").style.display = "none";
    document.getElementById("obStep2").style.display = "block";
  } catch (err) {
    alert("Failed to create mine: " + err.message);
  }
}

async function handleOnboardingTunnelSubmit(e) {
  e.preventDefault();
  const name = document.getElementById("obTunnelName").value.trim();
  const code = document.getElementById("obTunnelCode").value.trim();
  const depth = parseFloat(document.getElementById("obTunnelDepth").value) || null;

  try {
    await apiFetch(`/api/mines/${onboardingMineId}/tunnels`, {
      method: "POST",
      body: JSON.stringify({
        name: name,
        code: code,
        depth_m: depth,
      }),
    });
    document.getElementById("obStep2").style.display = "none";
    document.getElementById("obStep3").style.display = "block";
  } catch (err) {
    alert("Failed to create tunnel: " + err.message);
  }
}

async function handleOnboardingGatewaySubmit(e) {
  e.preventDefault();
  const name = document.getElementById("obGwName").value.trim();
  const mac = document.getElementById("obGwMac").value.trim();

  try {
    const res = await apiFetch(`/api/mines/${onboardingMineId}/gateways`, {
      method: "POST",
      body: JSON.stringify({ name: name, mac: mac }),
    });
    showGatewayKey(res.api_key);
    document.getElementById("obStep3").style.display = "none";
    document.getElementById("obStep4").style.display = "block";
  } catch (err) {
    alert("Failed to register gateway: " + err.message);
  }
}

async function finishOnboarding() {
  closeOnboardingModal();
  await checkAuth();
  await fetchDashboardData();
}

// ==========================================
// MINES, TUNNELS & GATEWAYS MANAGEMENT
// ==========================================

async function openMinesModal() {
  document.getElementById("minesModal").classList.add("open");
  await loadMinesList();
}

function closeMinesModal() {
  document.getElementById("minesModal").classList.remove("open");
}

async function loadMinesList() {
  const container = document.getElementById("minesListContainer");
  container.innerHTML = `<div style="font-family:var(--mono); font-size:12px; color:var(--ink-3);">Loading mine sites and hardware topology...</div>`;

  try {
    const minesRes = await apiFetch("/api/mines");
    const mines = minesRes.mines || [];

    if (!mines.length) {
      container.innerHTML = `
        <div style="font-family:var(--mono); font-size:12px; color:var(--ink-3); padding:16px; border:2px dashed var(--ink-3);">
          No mine sites created yet. Click "+ Add New Mine" to create one.
        </div>
      `;
      return;
    }

    let html = "";
    for (const m of mines) {
      const tunnelsRes = await apiFetch(`/api/mines/${m.id}/tunnels`).catch(() => ({ tunnels: [] }));
      const gatewaysRes = await apiFetch(`/api/mines/${m.id}/gateways`).catch(() => ({ gateways: [] }));
      const tunnels = tunnelsRes.tunnels || [];
      const gateways = gatewaysRes.gateways || [];

      html += `
        <div class="zine-box" style="padding:14px; background:var(--paper);">
          <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:2px solid var(--ink); padding-bottom:8px; margin-bottom:10px;">
            <div>
              <strong style="font-size:16px;">${m.name}</strong>
              <span style="font-size:11px; color:var(--ink-3); margin-left:8px;">${m.subsidiary ? "(" + m.subsidiary + ")" : ""} • ${m.method} • Status: ${m.status}</span>
            </div>
            <div style="display:flex; gap:6px;">
              <button class="neo-btn btn-small" onclick="openAddTunnelModal(${m.id})">+ Add Tunnel</button>
              <button class="neo-btn btn-small btn-primary" onclick="openAddGatewayModal(${m.id})">+ Add Gateway</button>
            </div>
          </div>

          <!-- Tunnels sub-list -->
          <div style="margin-bottom:10px;">
            <div style="font-family:var(--condensed); font-size:12px; font-weight:700; color:var(--ink-2); margin-bottom:4px;">UNDERGROUND TUNNELS (${tunnels.length}):</div>
            ${tunnels.length ? `
              <div style="display:flex; flex-wrap:wrap; gap:8px;">
                ${tunnels.map(t => `
                  <span style="background:var(--paper-3); border:1px solid var(--ink); padding:3px 8px; font-family:var(--mono); font-size:11px;">
                    ${t.name} [<code>${t.code}</code>] · ${t.depth_m != null ? t.depth_m + 'm' : 'Surface'}
                  </span>
                `).join("")}
              </div>
            ` : `<div style="font-size:11px; color:var(--ink-3); font-style:italic;">No tunnels assigned. Add a tunnel to place sensor nodes.</div>`}
          </div>

          <!-- Gateways sub-list -->
          <div>
            <div style="font-family:var(--condensed); font-size:12px; font-weight:700; color:var(--ink-2); margin-bottom:4px;">HARDWARE GATEWAYS (${gateways.length}):</div>
            ${gateways.length ? `
              <div style="display:flex; flex-direction:column; gap:6px;">
                ${gateways.map(g => `
                  <div style="display:flex; justify-content:space-between; align-items:center; background:#fff; border:1px solid var(--ink); padding:6px 10px;">
                    <div>
                      <strong>${g.name}</strong>
                      <code style="margin-left:8px;">${g.mac}</code>
                      <span class="badge-danger-level badge-${g.online ? 'normal' : 'critical'}" style="margin-left:8px; font-size:9px;">
                        ${g.online ? 'ONLINE' : 'OFFLINE'}
                      </span>
                      <span style="font-size:10px; color:var(--ink-3); margin-left:6px;">
                        ${g.last_seen_at ? 'Seen: ' + new Date(g.last_seen_at).toLocaleTimeString() : 'Never seen'}
                      </span>
                      ${!g.active ? '<span style="color:var(--blood); font-weight:700; font-size:10px; margin-left:6px;">[DISABLED]</span>' : ''}
                    </div>
                    <div style="display:flex; gap:6px;">
                      <button class="neo-btn btn-small" onclick="rotateGatewayKey(${g.id})">Rotate Key</button>
                      <button class="neo-btn btn-small" onclick="toggleGatewayActive(${g.id}, ${g.active})">
                        ${g.active ? 'Disable' : 'Enable'}
                      </button>
                      ${g.node_count === 0 ? `<button class="neo-btn btn-small btn-danger" onclick="deleteGateway(${g.id})">Delete</button>` : ''}
                    </div>
                  </div>
                `).join("")}
              </div>
            ` : `<div style="font-size:11px; color:var(--ink-3); font-style:italic;">No hardware gateways registered. Register a gateway to connect nodes.</div>`}
          </div>
        </div>
      `;
    }
    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = `<div style="color:var(--blood); font-weight:700;">Error loading mines: ${err.message}</div>`;
  }
}

// Add Mine Modal
function openAddMineModal() {
  document.getElementById("addMineModal").classList.add("open");
}
function closeAddMineModal() {
  document.getElementById("addMineModal").classList.remove("open");
}
async function handleCreateMineSubmit(e) {
  e.preventDefault();
  const name = document.getElementById("newMineName").value.trim();
  const sub = document.getElementById("newMineSubsidiary").value.trim();
  const lat = parseFloat(document.getElementById("newMineLat").value) || null;
  const lon = parseFloat(document.getElementById("newMineLon").value) || null;

  try {
    await apiFetch("/api/mines", {
      method: "POST",
      body: JSON.stringify({ name: name, subsidiary: sub || null, lat: lat, lon: lon }),
    });
    closeAddMineModal();
    await loadMinesList();
    await checkAuth();
    await fetchDashboardData();
  } catch (err) {
    alert("Create mine failed: " + err.message);
  }
}

// Add Tunnel Modal
function openAddTunnelModal(mineId) {
  document.getElementById("tunnelMineId").value = mineId;
  document.getElementById("addTunnelModal").classList.add("open");
}
function closeAddTunnelModal() {
  document.getElementById("addTunnelModal").classList.remove("open");
}
async function handleCreateTunnelSubmit(e) {
  e.preventDefault();
  const mineId = document.getElementById("tunnelMineId").value;
  const name = document.getElementById("newTunnelName").value.trim();
  const code = document.getElementById("newTunnelCode").value.trim();
  const depth = parseFloat(document.getElementById("newTunnelDepth").value) || null;

  try {
    await apiFetch(`/api/mines/${mineId}/tunnels`, {
      method: "POST",
      body: JSON.stringify({ name: name, code: code, depth_m: depth }),
    });
    closeAddTunnelModal();
    await loadMinesList();
  } catch (err) {
    alert("Create tunnel failed: " + err.message);
  }
}

// Add Gateway Modal
function openAddGatewayModal(mineId) {
  document.getElementById("gatewayMineId").value = mineId;
  document.getElementById("addGatewayModal").classList.add("open");
}
function closeAddGatewayModal() {
  document.getElementById("addGatewayModal").classList.remove("open");
}
async function handleCreateGatewaySubmit(e) {
  e.preventDefault();
  const mineId = document.getElementById("gatewayMineId").value;
  const name = document.getElementById("newGatewayName").value.trim();
  const mac = document.getElementById("newGatewayMac").value.trim();

  try {
    const res = await apiFetch(`/api/mines/${mineId}/gateways`, {
      method: "POST",
      body: JSON.stringify({ name: name, mac: mac }),
    });
    closeAddGatewayModal();
    showGatewayKey(res.api_key);
    await loadMinesList();
    await fetchDashboardData();
  } catch (err) {
    alert("Register gateway failed: " + err.message);
  }
}

async function rotateGatewayKey(gwId) {
  if (!confirm("Rotate this hardware gateway's API key? The existing key will be immediately invalidated and rejected by /api/ingest.")) {
    return;
  }
  try {
    const res = await apiFetch(`/api/gateways/${gwId}/rotate-key`, { method: "POST" });
    showGatewayKey(res.api_key);
    await loadMinesList();
  } catch (err) {
    alert("Failed to rotate key: " + err.message);
  }
}

async function toggleGatewayActive(gwId, currentActive) {
  try {
    await apiFetch(`/api/gateways/${gwId}`, {
      method: "PATCH",
      body: JSON.stringify({ active: !currentActive }),
    });
    await loadMinesList();
    await fetchDashboardData();
  } catch (err) {
    alert("Toggle active status failed: " + err.message);
  }
}

async function deleteGateway(gwId) {
  if (!confirm("Disable this hardware gateway?")) {
    return;
  }
  try {
    await apiFetch(`/api/gateways/${gwId}`, { method: "DELETE" });
    await loadMinesList();
    await fetchDashboardData();
  } catch (err) {
    alert("Disable gateway failed: " + err.message);
  }
}

// =============================================================================
// STEP 5: WORKERS, SHIFTS & ASSIGNMENTS FRONTEND CONSOLE
// =============================================================================

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

let allAvailableTunnels = [];

async function openWorkersModal() {
  const modal = document.getElementById("workersModal");
  if (modal) modal.classList.add("open");

  // Load mines and tunnels for filters
  try {
    const mRes = await apiFetch("/api/mines");
    const mineSelect = document.getElementById("workerMineFilter");
    if (mineSelect && mRes.mines) {
      mineSelect.innerHTML = `<option value="">All Permitted Mines</option>` +
        mRes.mines.map(m => `<option value="${m.id}">${escapeHtml(m.name)}</option>`).join("");
    }

    // Pre-cache all tunnels for assignment
    allAvailableTunnels = [];
    if (mRes.mines) {
      for (const m of mRes.mines) {
        try {
          const tRes = await apiFetch(`/api/mines/${m.id}/tunnels`);
          if (tRes.tunnels) {
            tRes.tunnels.forEach(t => allAvailableTunnels.push({ ...t, mine_name: m.name }));
          }
        } catch (_) {}
      }
    }
  } catch (err) {
    console.error("Failed to load mines for worker filter:", err);
  }

  await searchWorkers();
}

function closeWorkersModal() {
  const modal = document.getElementById("workersModal");
  if (modal) modal.classList.remove("open");
}

async function onWorkerMineFilterChange() {
  const mineId = document.getElementById("workerMineFilter").value;
  const tunnelSelect = document.getElementById("workerTunnelFilter");
  if (!tunnelSelect) return;

  tunnelSelect.innerHTML = `<option value="">All Tunnels</option>`;
  if (mineId) {
    try {
      const res = await apiFetch(`/api/mines/${mineId}/tunnels`);
      if (res.tunnels) {
        tunnelSelect.innerHTML += res.tunnels.map(t =>
          `<option value="${t.id}">${escapeHtml(t.name)} (${escapeHtml(t.code)})</option>`
        ).join("");
      }
    } catch (err) {
      console.error("Failed to load tunnels:", err);
    }
  }
}

function resetWorkerFilters() {
  document.getElementById("workerSearchInput").value = "";
  document.getElementById("workerMineFilter").value = "";
  const tunnelSelect = document.getElementById("workerTunnelFilter");
  if (tunnelSelect) tunnelSelect.innerHTML = `<option value="">All Tunnels</option>`;
  searchWorkers();
}

async function searchWorkers() {
  const container = document.getElementById("workersListContainer");
  if (!container) return;
  container.innerHTML = `<div style="font-size:12px; color:var(--ink-3);">Searching workers...</div>`;

  const q = document.getElementById("workerSearchInput")?.value.trim() || "";
  const mineId = document.getElementById("workerMineFilter")?.value || "";
  const tunnelId = document.getElementById("workerTunnelFilter")?.value || "";

  let url = `/api/workers?`;
  const params = [];
  if (q) params.push(`q=${encodeURIComponent(q)}`);
  if (mineId) params.push(`mine_id=${encodeURIComponent(mineId)}`);
  if (tunnelId) params.push(`tunnel_id=${encodeURIComponent(tunnelId)}`);
  url += params.join("&");

  try {
    const res = await apiFetch(url);
    const workers = res.workers || [];
    if (workers.length === 0) {
      container.innerHTML = `<div style="font-size:12px; color:var(--ink-3); padding:10px;">No workers found.</div>`;
      return;
    }

    container.innerHTML = workers.map(w => {
      const currentTunnelText = w.current_tunnel
        ? `📍 Tunnel: <strong>${escapeHtml(w.current_tunnel.name)}</strong>`
        : `<span style="color:var(--ink-3);">📍 No active shift</span>`;
      const empNoText = w.employee_no ? escapeHtml(w.employee_no) : "ID pending";

      return `
        <div class="zine-box" style="padding:8px; display:flex; justify-content:space-between; align-items:center; background:#fff;">
          <div>
            <div style="font-weight:700; font-size:13px;">${escapeHtml(w.full_name)} <span class="role-tag">${escapeHtml(w.role.toUpperCase())}</span></div>
            <div style="font-size:11px; color:var(--ink-2); margin-top:2px;">Employee: <code>${empNoText}</code> | User: <em>@${escapeHtml(w.username)}</em></div>
            <div style="font-size:11px; margin-top:3px;">${currentTunnelText}</div>
          </div>
          <button class="neo-btn btn-small btn-primary" onclick="selectWorker(${w.id})">Inspect</button>
        </div>
      `;
    }).join("");
  } catch (err) {
    container.innerHTML = `<div style="font-size:12px; color:var(--red-1);">Failed to load workers: ${escapeHtml(err.message)}</div>`;
  }
}

async function selectWorker(workerId) {
  const pane = document.getElementById("workerDetailPane");
  if (!pane) return;
  pane.innerHTML = `<div style="font-size:12px; color:var(--ink-3);">Loading worker profile and history...</div>`;

  try {
    const worker = await apiFetch(`/api/workers/${workerId}`);
    const isManager = currentUser && currentUser.role === "manager";
    const canSchedule = currentUser && ["manager", "safety_officer"].includes(currentUser.role);

    const empNo = escapeHtml(worker.employee_no || "");
    const phone = escapeHtml(worker.phone || "");
    const emName = escapeHtml(worker.emergency_contact_name || "");
    const emPhone = escapeHtml(worker.emergency_contact_phone || "");
    const joinedOn = escapeHtml(worker.joined_on || "");
    const tunnelName = worker.current_tunnel ? escapeHtml(worker.current_tunnel.name) : "None (Unassigned)";

    // Default datetimes for assignment form (next hour to +8 hours)
    const now = new Date();
    now.setMinutes(0, 0, 0);
    now.setHours(now.getHours() + 1);
    const defaultStart = now.toISOString().slice(0, 16);
    const end = new Date(now.getTime() + 8 * 3600 * 1000);
    const defaultEnd = end.toISOString().slice(0, 16);

    const tunnelOptions = allAvailableTunnels.map(t =>
      `<option value="${t.id}">${escapeHtml(t.mine_name)} ➔ ${escapeHtml(t.name)} (${escapeHtml(t.code)})</option>`
    ).join("");

    pane.innerHTML = `
      <!-- Header -->
      <div style="border-bottom:2px solid var(--ink); padding-bottom:8px; margin-bottom:10px;">
        <h4 style="font-family:var(--condensed); font-size:16px; margin:0;">
          ${escapeHtml(worker.full_name)} <span class="role-tag">${escapeHtml(worker.role.toUpperCase())}</span>
        </h4>
        <div style="font-size:11px; color:var(--ink-3); margin-top:3px;">
          Username: <strong>@${escapeHtml(worker.username)}</strong> | Status: ${worker.active ? "🟢 Active" : "🔴 Inactive"}
        </div>
      </div>

      <!-- Current Tunnel Badge -->
      <div style="background:var(--paper); border:1px solid var(--ink); padding:6px 10px; margin-bottom:10px; font-size:12px;">
        CURRENT TUNNEL: <strong>${tunnelName}</strong>
      </div>

      <!-- Profile Edit Form (Manager) or Readonly Cards (Other Superiors) -->
      ${isManager ? `
        <details style="margin-bottom:12px;">
          <summary style="font-size:12px; font-weight:700; cursor:pointer; color:var(--saffron);">✏️ EDIT WORKER PROFILE &amp; CONTACTS</summary>
          <form onsubmit="handleSaveWorkerProfile(event, ${worker.id})" style="margin-top:8px; padding:10px; background:#fff; border:1px solid var(--ink);">
            <div class="form-group" style="margin-bottom:6px;">
              <label style="font-size:11px;">Employee ID (Required 1-64 chars)</label>
              <input type="text" id="profEmpNo" class="form-input" required value="${empNo}" placeholder="e.g. EMP-1044">
            </div>
            <div class="form-group" style="margin-bottom:6px;">
              <label style="font-size:11px;">Phone (5-32 digits)</label>
              <input type="text" id="profPhone" class="form-input" value="${phone}" placeholder="+91 9876543210">
            </div>
            <div class="form-group" style="margin-bottom:6px;">
              <label style="font-size:11px;">Emergency Contact Name</label>
              <input type="text" id="profEmName" class="form-input" value="${emName}" placeholder="Emergency Contact Name">
            </div>
            <div class="form-group" style="margin-bottom:6px;">
              <label style="font-size:11px;">Emergency Contact Phone</label>
              <input type="text" id="profEmPhone" class="form-input" value="${emPhone}" placeholder="+91 9123456789">
            </div>
            <div class="form-group" style="margin-bottom:8px;">
              <label style="font-size:11px;">Joined On (Date)</label>
              <input type="date" id="profJoinedOn" class="form-input" value="${joinedOn}">
            </div>
            <button type="submit" class="neo-btn btn-small btn-primary" style="width:100%;">Save Profile Changes</button>
          </form>
        </details>
      ` : `
        <div style="font-size:11px; margin-bottom:10px; background:#fff; padding:6px; border:1px solid #ddd;">
          <div>Employee ID: <strong>${empNo || "None"}</strong> | Joined: <strong>${joinedOn || "None"}</strong></div>
          <div>Phone: <strong>${phone || "None"}</strong> | Emergency: <strong>${emName || "None"} (${emPhone || "None"})</strong></div>
        </div>
      `}

      <!-- Shift Assignment Form (Manager & Safety Officer) -->
      ${canSchedule ? `
        <div style="background:#fff; border:2px solid var(--ink); padding:10px; margin-bottom:12px;">
          <h5 style="font-family:var(--condensed); margin:0 0 8px 0;">⚡ ASSIGN TO UNDERGROUND TUNNEL</h5>
          
          <div id="assignWeeklyWarning" class="zine-box" style="display:none; background:#fff3cd; color:#856404; font-weight:700; border:2px solid #ffeeba; margin-bottom:8px; padding:6px 10px; font-size:11px;"></div>
          <div id="assignClashError" class="zine-box" style="display:none; background:#f8d7da; color:#721c24; font-weight:700; border:2px solid #f5c6cb; margin-bottom:8px; padding:6px 10px; font-size:11px;"></div>

          <form onsubmit="handleCreateAssignmentSubmit(event, ${worker.id})">
            <div class="form-group" style="margin-bottom:6px;">
              <label style="font-size:11px;">Target Tunnel</label>
              <select id="assignTunnelId" class="form-select" required>
                ${tunnelOptions}
              </select>
            </div>
            <div style="display:flex; gap:8px; margin-bottom:8px;">
              <div class="form-group" style="flex:1;">
                <label style="font-size:11px;">Shift Start</label>
                <input type="datetime-local" id="assignStartsAt" class="form-input" required value="${defaultStart}">
              </div>
              <div class="form-group" style="flex:1;">
                <label style="font-size:11px;">Shift End</label>
                <input type="datetime-local" id="assignEndsAt" class="form-input" required value="${defaultEnd}">
              </div>
            </div>
            <button type="submit" class="neo-btn btn-small btn-primary" style="width:100%;">Create Shift Assignment</button>
          </form>
        </div>
      ` : ""}

      <!-- Upcoming Shifts -->
      <h5 style="font-family:var(--condensed); margin:10px 0 4px 0;">UPCOMING SHIFTS</h5>
      <div style="display:flex; flex-direction:column; gap:6px; max-height:140px; overflow-y:auto; margin-bottom:10px;">
        ${worker.upcoming_shifts && worker.upcoming_shifts.length > 0 ? worker.upcoming_shifts.map(s => `
          <div style="font-size:11px; padding:6px; background:#fff; border:1px solid #ddd; display:flex; justify-content:space-between; align-items:center;">
            <div>
              <strong>${escapeHtml(s.tunnel_name)}</strong> (${escapeHtml(s.mine_name)})<br>
              <span style="color:var(--ink-2);">${new Date(s.starts_at).toLocaleString()} ➔ ${new Date(s.ends_at).toLocaleString()}</span>
            </div>
            ${canSchedule ? `
              <div style="display:flex; gap:4px;">
                <button type="button" class="neo-btn btn-small" onclick="handleShortenShift(${s.id}, '${s.starts_at}', '${s.ends_at}', ${worker.id})">Shorten</button>
                <button type="button" class="neo-btn btn-small btn-danger" onclick="handleDeleteShift(${s.id}, ${worker.id})">✖</button>
              </div>
            ` : ""}
          </div>
        `).join("") : `<div style="font-size:11px; color:var(--ink-3);">No upcoming shifts.</div>`}
      </div>

      <!-- Past Shifts (Last 30 Days) -->
      <h5 style="font-family:var(--condensed); margin:10px 0 4px 0;">PAST SHIFTS (LAST 30 DAYS)</h5>
      <div style="display:flex; flex-direction:column; gap:6px; max-height:120px; overflow-y:auto;">
        ${worker.past_shifts && worker.past_shifts.length > 0 ? worker.past_shifts.map(s => `
          <div style="font-size:11px; padding:6px; background:#f9f9f9; border:1px solid #ddd;">
            <strong>${escapeHtml(s.tunnel_name)}</strong> (${escapeHtml(s.mine_name)})<br>
            <span style="color:var(--ink-3);">${new Date(s.starts_at).toLocaleString()} ➔ ${new Date(s.ends_at).toLocaleString()}</span>
          </div>
        `).join("") : `<div style="font-size:11px; color:var(--ink-3);">No past shifts in the last 30 days.</div>`}
      </div>
    `;
  } catch (err) {
    pane.innerHTML = `<div style="font-size:12px; color:var(--red-1);">Failed to load worker details: ${escapeHtml(err.message)}</div>`;
  }
}

async function handleSaveWorkerProfile(event, workerId) {
  event.preventDefault();
  const empNo = document.getElementById("profEmpNo").value.trim();
  const phone = document.getElementById("profPhone").value.trim() || null;
  const emName = document.getElementById("profEmName").value.trim() || null;
  const emPhone = document.getElementById("profEmPhone").value.trim() || null;
  const joinedOn = document.getElementById("profJoinedOn").value || null;

  try {
    await apiFetch(`/api/workers/${workerId}/profile`, {
      method: "PUT",
      body: JSON.stringify({
        employee_no: empNo,
        phone: phone,
        emergency_contact_name: emName,
        emergency_contact_phone: emPhone,
        joined_on: joinedOn,
      }),
    });
    alert("Worker profile updated successfully!");
    await selectWorker(workerId);
    await searchWorkers();
  } catch (err) {
    alert("Profile update failed: " + err.message);
  }
}

async function handleCreateAssignmentSubmit(event, workerId) {
  event.preventDefault();
  const tunnelId = parseInt(document.getElementById("assignTunnelId").value, 10);
  const startLocal = document.getElementById("assignStartsAt").value;
  const endLocal = document.getElementById("assignEndsAt").value;

  const warnEl = document.getElementById("assignWeeklyWarning");
  const errEl = document.getElementById("assignClashError");
  if (warnEl) warnEl.style.display = "none";
  if (errEl) errEl.style.display = "none";

  // Require timezone offset by converting local dates to ISO-8601 UTC string
  const startsAtIso = new Date(startLocal).toISOString();
  const endsAtIso = new Date(endLocal).toISOString();

  try {
    const res = await fetch("/api/assignments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: workerId,
        tunnel_id: tunnelId,
        starts_at: startsAtIso,
        ends_at: endsAtIso,
      }),
    });

    const data = await res.json();

    if (res.status === 201) {
      if (data.warnings && data.warnings.length > 0) {
        const w = data.warnings[0];
        if (warnEl) {
          warnEl.innerHTML = `⚠️ WEEKLY HOURS WARNING: Shift scheduled, but total weekly hours (${w.total_hours}h) exceeds limit (${w.limit_hours}h)!`;
          warnEl.style.display = "block";
        }
      } else {
        alert("Shift assigned successfully!");
      }
      await selectWorker(workerId);
      await searchWorkers();
    } else if (res.status === 409) {
      if (errEl) {
        const clash = data.clashing_shift;
        const clashText = clash
          ? `Overlaps with Shift #${clash.id} in ${escapeHtml(clash.tunnel_name)} (${new Date(clash.starts_at).toLocaleTimeString()} to ${new Date(clash.ends_at).toLocaleTimeString()})`
          : data.detail || "Shift overlap detected";
        errEl.innerHTML = `⚠️ CLASHING SHIFT REJECTED: ${clashText}`;
        errEl.style.display = "block";
      } else {
        alert("Shift clash: " + (data.detail || "Overlapping shift"));
      }
    } else {
      alert("Assignment failed: " + (data.detail || JSON.stringify(data)));
    }
  } catch (err) {
    alert("Assignment error: " + err.message);
  }
}

async function handleShortenShift(shiftId, startsAt, endsAt, workerId) {
  const currentEnd = new Date(endsAt);
  const promptEnd = prompt(
    `Enter shortened end time in local ISO format (YYYY-MM-DDTHH:MM):\nCurrent end: ${currentEnd.toLocaleString()}`
  );
  if (!promptEnd) return;

  const newEndIso = new Date(promptEnd).toISOString();
  try {
    const res = await fetch(`/api/assignments/${shiftId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ends_at: newEndIso }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert("Failed to shorten shift: " + (data.detail || "Unknown error"));
      return;
    }
    alert("Shift shortened successfully!");
    await selectWorker(workerId);
  } catch (err) {
    alert("Shorten shift failed: " + err.message);
  }
}

async function handleDeleteShift(shiftId, workerId) {
  if (!confirm("Are you sure you want to delete this upcoming shift?")) return;
  try {
    const res = await fetch(`/api/assignments/${shiftId}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) {
      alert("Failed to delete shift: " + (data.detail || data.message || "Unknown error"));
      return;
    }
    alert("Shift deleted successfully!");
    await selectWorker(workerId);
  } catch (err) {
    alert("Delete shift failed: " + err.message);
  }
}

// =============================================================================
// STEP 5: WORKER MY HOURS SELF-SERVICE CONSOLE
// =============================================================================

async function openMyHoursModal() {
  const modal = document.getElementById("myHoursModal");
  if (modal) modal.classList.add("open");

  const profCard = document.getElementById("myProfileCard");
  const hoursCard = document.getElementById("myHoursProgressBar");
  const shiftsCard = document.getElementById("myShiftsContainer");

  if (profCard) profCard.innerHTML = `<div style="font-size:12px; color:var(--ink-3);">Loading worker profile...</div>`;
  if (hoursCard) hoursCard.innerHTML = `<div style="font-size:12px; color:var(--ink-3);">Loading weekly hours...</div>`;
  if (shiftsCard) shiftsCard.innerHTML = `<div style="font-size:12px; color:var(--ink-3);">Loading shifts...</div>`;

  try {
    const [profile, shiftsData] = await Promise.all([
      apiFetch("/api/me/profile"),
      apiFetch("/api/me/shifts"),
    ]);

    // 1. Profile rendering
    if (profCard) {
      const empNo = escapeHtml(profile.employee_no || "Unassigned");
      const phone = escapeHtml(profile.phone || "Not on file");
      const emName = escapeHtml(profile.emergency_contact_name || "None");
      const emPhone = escapeHtml(profile.emergency_contact_phone || "None");
      const joinedOn = escapeHtml(profile.joined_on || "N/A");
      const currTunnel = profile.current_tunnel ? escapeHtml(profile.current_tunnel.name) : "No active assignment";

      profCard.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #ddd; padding-bottom:6px; margin-bottom:8px;">
          <div>
            <span style="font-size:14px; font-weight:700;">${escapeHtml(currentUser?.full_name || currentUser?.username)}</span>
            <span class="role-tag">${escapeHtml(currentUser?.role?.toUpperCase() || "WORKER")}</span>
          </div>
          <div style="font-family:var(--mono); font-size:12px;">ID: <strong>${empNo}</strong></div>
        </div>
        <div style="font-size:12px; display:grid; grid-template-columns:1fr 1fr; gap:6px;">
          <div>📍 Current Tunnel: <strong>${currTunnel}</strong></div>
          <div>📅 Joined: <strong>${joinedOn}</strong></div>
          <div>📞 Phone: <strong>${phone}</strong></div>
          <div>🚨 Emergency: <strong>${emName} (${emPhone})</strong></div>
        </div>
      `;
    }

    // 2. Weekly hours calculation (Monday 00:00 to Sunday 23:59 local)
    const shifts = shiftsData.shifts || [];
    const now = new Date();
    const currentDay = now.getDay();
    const diffToMonday = (currentDay === 0 ? -6 : 1) - currentDay;
    const monday = new Date(now);
    monday.setDate(now.getDate() + diffToMonday);
    monday.setHours(0, 0, 0, 0);

    const nextMonday = new Date(monday);
    nextMonday.setDate(monday.getDate() + 7);

    let totalSecondsThisWeek = 0;
    shifts.forEach(s => {
      const sStart = new Date(s.starts_at);
      const sEnd = new Date(s.ends_at);
      const overlapStart = Math.max(sStart.getTime(), monday.getTime());
      const overlapEnd = Math.min(sEnd.getTime(), nextMonday.getTime());
      if (overlapEnd > overlapStart) {
        totalSecondsThisWeek += (overlapEnd - overlapStart) / 1000;
      }
    });

    const totalHours = (totalSecondsThisWeek / 3600).toFixed(1);
    const limit = 48.0;
    const isExceeded = parseFloat(totalHours) > limit;

    if (hoursCard) {
      hoursCard.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <span>This Week's Logged Shifts (Mon-Sun):</span>
          <span style="font-family:var(--mono); font-size:15px; color:${isExceeded ? 'var(--red-1)' : 'var(--green-2)'};">
            ${totalHours} / ${limit} hrs
          </span>
        </div>
        <div style="height:10px; background:#eee; border:1px solid #ccc; width:100%;">
          <div style="height:100%; width:${Math.min(100, (totalHours / limit) * 100)}%; background:${isExceeded ? 'var(--red-1)' : 'var(--green-2)'};"></div>
        </div>
        ${isExceeded ? `<div style="font-size:11px; color:var(--red-1); margin-top:4px;">⚠️ Weekly hours limit (${limit}h) exceeded! Notify your shift supervisor.</div>` : ""}
      `;
    }

    // 3. Shifts list
    if (shiftsCard) {
      if (shifts.length === 0) {
        shiftsCard.innerHTML = `<div style="font-size:12px; color:var(--ink-3); padding:10px;">No shifts assigned.</div>`;
        return;
      }

      const nowTime = now.getTime();
      shiftsCard.innerHTML = shifts.map(s => {
        const sStart = new Date(s.starts_at);
        const sEnd = new Date(s.ends_at);
        const isActive = sStart.getTime() <= nowTime && nowTime < sEnd.getTime();
        const isUpcoming = sStart.getTime() > nowTime;
        const statusBadge = isActive
          ? `<span style="background:var(--green-2); color:#fff; padding:2px 6px; font-size:10px; font-weight:700;">🟢 ON SHIFT NOW</span>`
          : isUpcoming
          ? `<span style="background:var(--saffron); color:#000; padding:2px 6px; font-size:10px; font-weight:700;">UPCOMING</span>`
          : `<span style="background:#ddd; color:#555; padding:2px 6px; font-size:10px;">COMPLETED</span>`;

        return `
          <div style="padding:8px; border:1px solid #ccc; background:${isActive ? '#e8f5e9' : '#fff'}; display:flex; justify-content:space-between; align-items:center;">
            <div>
              <div style="font-weight:700; font-size:13px;">${escapeHtml(s.tunnel_name)} <span style="font-size:11px; font-weight:normal; color:#666;">(${escapeHtml(s.mine_name)})</span></div>
              <div style="font-size:11px; color:#555; margin-top:2px;">
                ${sStart.toLocaleString()} ➔ ${sEnd.toLocaleString()}
              </div>
            </div>
            <div>${statusBadge}</div>
          </div>
        `;
      }).join("");
    }
  } catch (err) {
    if (shiftsCard) shiftsCard.innerHTML = `<div style="font-size:12px; color:var(--red-1);">Failed to load shift data: ${escapeHtml(err.message)}</div>`;
  }
}

function closeMyHoursModal() {
  const modal = document.getElementById("myHoursModal");
  if (modal) modal.classList.remove("open");
}

