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
let authToken = localStorage.getItem("subsidence_auth_token") || "";
let currentUser = null;

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
  const level = (node.status || "normal").toLowerCase();
  const isLowBatt = node.low_battery || (node.battery_percentage <= 20.0);
  const battBadge = isLowBatt ? `<div class="marker-battery-low">🪫 ${node.battery_percentage.toFixed(0)}%</div>` : "";

  return `
    <div class="neo-marker-container">
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

function startPlacementMode(node) {
  placementMode.active = true;
  placementMode.node = node;

  document.body.classList.add("placement-cursor");
  const banner = document.getElementById("placementModeBanner");
  const bannerText = document.getElementById("placementBannerText");
  bannerText.textContent = `🎯 PLACEMENT MODE ACTIVE: Click anywhere on the map to place Node #${node.node_id} (Gateway: ${node.gateway_mac_address})`;
  banner.style.display = "block";

  // Auto pan to known coal belt if zoomed out
  if (map.getZoom() < 10) {
    panMapTo(23.7438, 86.4116, 12);
  }
}

function cancelPlacementMode() {
  placementMode.active = false;
  placementMode.node = null;
  document.body.classList.remove("placement-cursor");
  document.getElementById("placementModeBanner").style.display = "none";
}

async function handleMapClickPlacement(lat, lng) {
  const node = placementMode.node;
  if (!node) return;

  const defaultName = node.location_name && !node.location_name.includes("Unplaced") 
    ? node.location_name 
    : `Node ${node.node_id} Surface Station`;

  const locName = prompt(`Enter surface location name for Node #${node.node_id}:`, defaultName) || defaultName;

  try {
    const res = await apiFetch("/api/placements", {
      method: "POST",
      body: JSON.stringify({
        gateway_mac_address: node.gateway_mac_address,
        node_id: node.node_id,
        latitude: lat,
        longitude: lng,
        location_name: locName
      })
    });

    cancelPlacementMode();
    await fetchDashboardData();
    
    // Open inspector for placed node
    const updatedNode = { ...node, latitude: lat, longitude: lng, location_name: locName, placed: true };
    openInspectorDrawer(updatedNode);
  } catch (err) {
    alert("Placement failed: " + err.message);
  }
}

function repositionCurrentNode() {
  if (!selectedNode) return;
  closeInspectorDrawer();
  startPlacementMode(selectedNode);
}

async function unplaceCurrentNode() {
  if (!selectedNode) return;
  if (!confirm(`Remove Node #${selectedNode.node_id} from map? It will return to the Unplaced Nodes list.`)) return;

  try {
    await apiFetch(`/api/placements/${encodeURIComponent(selectedNode.gateway_mac_address)}/${selectedNode.node_id}`, {
      method: "DELETE"
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

  const status = (node.status || "normal").toLowerCase();
  const stamp = document.getElementById("drawerStatusStamp");
  stamp.textContent = `STATUS: ${status.toUpperCase()}`;
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
        <button class="neo-btn btn-small btn-primary" onclick="startPlacementMode(${JSON.stringify(n).replace(/"/g, '&quot;')})">
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
            const nLevel = (n.status || "normal").toLowerCase();
            const isSel = selectedNode && selectedNode.gateway_mac_address === n.gateway_mac_address && selectedNode.node_id === n.node_id;
            return `
              <div class="node-chip ${isSel ? 'selected' : ''}" onclick="openInspectorDrawer(${JSON.stringify(n).replace(/"/g, '&quot;')})">
                <div class="node-chip-header">
                  <span class="node-chip-id">N#${n.node_id}</span>
                  <span class="node-chip-status" style="background:${n.color};"></span>
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

async function fetchAlerts() {
  try {
    const data = await apiFetch("/api/alerts?limit=20");
    const alerts = data.alerts || [];
    renderAlertsTable(alerts);
  } catch (err) {
    console.error("Alerts fetch error:", err);
  }
}

function renderAlertsTable(alerts) {
  const tbody = document.getElementById("alertsTableBody");
  if (!alerts.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--ink-3);">No active hazard alerts recorded.</td></tr>`;
    return;
  }

  tbody.innerHTML = alerts.map(a => {
    const level = (a.danger_level || "normal").toLowerCase();
    const ackBtn = a.acknowledged 
      ? `<span style="color:var(--green); font-weight:700;">✓ Acked</span>` 
      : `<button class="neo-btn btn-small" onclick="acknowledgeAlert(${a.id})">Ack</button>`;

    return `
      <tr>
        <td style="white-space:nowrap;">${a.time_str}</td>
        <td><strong>Node #${a.node_id}</strong></td>
        <td><code>${a.gateway_mac_address}</code></td>
        <td><span class="badge-danger-level badge-${level}">${level}</span></td>
        <td>${a.trigger_reading}</td>
        <td>${ackBtn}</td>
      </tr>
    `;
  }).join("");
}

async function acknowledgeAlert(alertId) {
  try {
    await apiFetch(`/api/alerts/${alertId}/ack`, { method: "POST" });
    fetchAlerts();
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
    fetchAlerts();
  }, 4000); // 4-second polling to capture 5-second ESP-NOW intervals
}

// ==========================================
// AUTHENTICATION & MULTI-TENANT ACCESS
// ==========================================

async function apiFetch(url, options = {}) {
  const headers = options.headers || {};
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }
  if (!headers["Content-Type"] && options.body && typeof options.body === "string") {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) {
    // Show auth modal if not authenticated
    openAuthModal();
    throw new Error("Authentication required");
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
    updateUserBadge(currentUser);
  } catch (e) {
    // If not authenticated, try login with default operator
    try {
      const loginRes = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: "mine_operator", password: "operator123" })
      });
      if (loginRes.ok) {
        const d = await loginRes.json();
        authToken = d.token;
        localStorage.setItem("subsidence_auth_token", authToken);
        currentUser = d.user;
        updateUserBadge(currentUser);
      } else {
        openAuthModal();
      }
    } catch (err) {
      openAuthModal();
    }
  }
}

function updateUserBadge(user) {
  if (!user) return;
  document.getElementById("currentUsername").textContent = user.username.toUpperCase();
  const roleEl = document.getElementById("currentUserRole");
  roleEl.textContent = user.role.toUpperCase();
  roleEl.style.background = user.role === "admin" ? "var(--saffron)" : "var(--ink)";
  document.getElementById("btnOpenAuth").style.display = "none";
  document.getElementById("btnLogout").style.display = "inline-flex";
}

function openAuthModal() {
  document.getElementById("authModal").classList.add("open");
}

function closeAuthModal() {
  document.getElementById("authModal").classList.remove("open");
}

let currentAuthTab = "login";
function switchAuthTab(tab) {
  currentAuthTab = tab;
  document.getElementById("tabLogin").className = tab === "login" ? "modal-tab active" : "modal-tab";
  document.getElementById("tabSignup").className = tab === "signup" ? "modal-tab active" : "modal-tab";
  document.getElementById("authEmailGroup").style.display = tab === "signup" ? "block" : "none";
  document.getElementById("authRoleGroup").style.display = tab === "signup" ? "block" : "none";
  document.getElementById("authModalTitle").textContent = tab === "login" ? "SIGN IN" : "CREATE OPERATOR ACCOUNT";
}

async function handleAuthSubmit(e) {
  e.preventDefault();
  const u = document.getElementById("authUsername").value.trim();
  const p = document.getElementById("authPassword").value;
  const em = document.getElementById("authEmail").value.trim();
  const role = document.getElementById("authRole").value;

  try {
    const endpoint = currentAuthTab === "login" ? "/api/auth/login" : "/api/auth/signup";
    const body = currentAuthTab === "login" ? { username: u, password: p } : { username: u, password: p, email: em, role };
    
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Authentication failed");
    }

    const data = await res.json();
    authToken = data.token;
    localStorage.setItem("subsidence_auth_token", authToken);
    currentUser = data.user;
    updateUserBadge(currentUser);
    closeAuthModal();
    fetchDashboardData();
  } catch (err) {
    alert("Auth error: " + err.message);
  }
}

async function handleLogout() {
  try {
    await apiFetch("/api/auth/logout", { method: "POST" });
  } catch (e) {}
  authToken = "";
  localStorage.removeItem("subsidence_auth_token");
  currentUser = null;
  location.reload();
}

// Add User Modal
function openAddUserModal() {
  document.getElementById("addUserModal").classList.add("open");
}
function closeAddUserModal() {
  document.getElementById("addUserModal").classList.remove("open");
}

async function handleCreateUserSubmit(e) {
  e.preventDefault();
  const u = document.getElementById("newUsername").value.trim();
  const em = document.getElementById("newEmail").value.trim();
  const p = document.getElementById("newPassword").value;
  const role = document.getElementById("newRole").value;

  try {
    await apiFetch("/api/users", {
      method: "POST",
      body: JSON.stringify({ username: u, email: em, password: p, role })
    });
    alert(`User '${u}' created successfully with role '${role}'.`);
    closeAddUserModal();
  } catch (err) {
    alert("Failed to create user: " + err.message);
  }
}

// ==========================================
// NOTIFICATION CHANNELS CONFIG MODAL
// ==========================================

async function openNotifModal() {
  document.getElementById("notifModal").classList.add("open");
  try {
    const data = await apiFetch("/api/notifications/config");
    const cfg = data.config || {};
    document.getElementById("cfgRepeatInterval").value = cfg.repeat_interval_s || 900;
    document.getElementById("cfgWebhookUrl").value = cfg.webhook_url || "";
    document.getElementById("cfgTelegramToken").value = cfg.telegram_bot_token || "";
    document.getElementById("cfgTelegramChatId").value = cfg.telegram_chat_id || "";
    document.getElementById("cfgTwilioSid").value = cfg.twilio_sid || "";
    document.getElementById("cfgTwilioToken").value = cfg.twilio_token || "";
    document.getElementById("cfgTwilioFrom").value = cfg.twilio_from || "";
    document.getElementById("cfgTwilioTo").value = cfg.twilio_to || "";
    document.getElementById("cfgTwilioIsWhatsApp").checked = !!cfg.twilio_is_whatsapp;
  } catch (err) {
    console.error("Config fetch error:", err);
  }
}

function closeNotifModal() {
  document.getElementById("notifModal").classList.remove("open");
}

async function handleSaveNotifConfig(e) {
  e.preventDefault();
  const payload = {
    repeat_interval_s: parseInt(document.getElementById("cfgRepeatInterval").value, 10) || 900,
    webhook_url: document.getElementById("cfgWebhookUrl").value.trim(),
    telegram_bot_token: document.getElementById("cfgTelegramToken").value.trim(),
    telegram_chat_id: document.getElementById("cfgTelegramChatId").value.trim(),
    twilio_sid: document.getElementById("cfgTwilioSid").value.trim(),
    twilio_token: document.getElementById("cfgTwilioToken").value.trim(),
    twilio_from: document.getElementById("cfgTwilioFrom").value.trim(),
    twilio_to: document.getElementById("cfgTwilioTo").value.trim(),
    twilio_is_whatsapp: document.getElementById("cfgTwilioIsWhatsApp").checked
  };

  try {
    await apiFetch("/api/notifications/config", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    alert("Notification configurations saved successfully.");
    closeNotifModal();
  } catch (err) {
    alert("Error saving settings: " + err.message);
  }
}

async function testNotificationChannel(channel) {
  try {
    const res = await apiFetch(`/api/notifications/test?channel=${channel}`, { method: "POST" });
    alert(`Success: ${res.message}`);
  } catch (err) {
    alert(`Test failed: ${err.message}`);
  }
}

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
