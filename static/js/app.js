let buses = Array.isArray(window.liveBuses) ? window.liveBuses : [];
let commuterData = window.commuterData && typeof window.commuterData === 'object' ? window.commuterData : { routes: [], stopDirectory: [], stopNames: [] };
let userLocation = null;
let userMarkerLayer = null;
let applyCurrentLocationToPlanner = false;
let hasSetInitialMapView = false;
let shouldFocusUserLocation = false;
const LIVE_TRACKING_REFRESH_MS = 3000;
const COMMUTER_DATA_REFRESH_MS = 10000;
const FALLBACK_BUS_SPEED_KPH = 24;

const mapElement = document.getElementById('map');
const map = mapElement ? L.map('map').setView([15.37, 120.94], 10) : null;
if (map) {
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);
}

const busList = document.getElementById('busList');
const activeBusCount = document.getElementById('activeBusCount');
const avgCrowd = document.getElementById('avgCrowd');
const highCrowdCount = document.getElementById('highCrowdCount');
const crowdLevels = document.getElementById('crowdLevels');
const userLocationText = document.getElementById('userLocationText');
const sortSelect = document.getElementById('sortSelect');
const locateMeBtn = document.getElementById('locateMeBtn');
const menuToggle = document.getElementById('menuToggle');
const menuOverlay = document.getElementById('menuOverlay');
const menuClose = document.getElementById('menuClose');

const plannerOrigin = document.getElementById('plannerOrigin');
const plannerDestination = document.getElementById('plannerDestination');
const plannerSearchBtn = document.getElementById('plannerSearchBtn');
const plannerSwapBtn = document.getElementById('plannerSwapBtn');
const useCurrentLocationBtn = document.getElementById('useCurrentLocationBtn');
const plannerResults = document.getElementById('plannerResults');
const plannerDestinationHint = document.getElementById('plannerDestinationHint');
const stopDirectoryList = document.getElementById('stopDirectoryList');
const plannerRouteList = document.getElementById('plannerRouteList');

let mapLayers = [];

function hasValidCoordinates(lat, lng) {
  return (
    lat !== null &&
    lat !== undefined &&
    lng !== null &&
    lng !== undefined &&
    Number.isFinite(Number(lat)) &&
    Number.isFinite(Number(lng)) &&
    Math.abs(Number(lat)) <= 90 &&
    Math.abs(Number(lng)) <= 180
  );
}

function markerColor(level) {
  if (level === 'High') return '#dc2626';
  if (level === 'Medium') return '#ea580c';
  return '#16a34a';
}

function createBusIcon(level) {
  const fill = markerColor(level);
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="42" height="42" viewBox="0 0 42 42">
      <g fill="none" fill-rule="evenodd">
        <path d="M21 3C11.6 3 6 6.8 6 13v12.5c0 2.2 1.8 4 4 4H11v4.2c0 1.2 1 2.3 2.3 2.3h1.4c1.2 0 2.3-1 2.3-2.3v-4.2h8v4.2c0 1.2 1 2.3 2.3 2.3h1.4c1.2 0 2.3-1 2.3-2.3v-4.2H32c2.2 0 4-1.8 4-4V13c0-6.2-5.6-10-15-10Z" fill="${fill}" stroke="#0f172a" stroke-width="1.5"/>
        <rect x="10" y="10" width="22" height="8" rx="2.5" fill="#e0f2fe" stroke="#0f172a" stroke-width="1.2"/>
        <path d="M10 22h22" stroke="#0f172a" stroke-width="1.4"/>
        <circle cx="14" cy="28" r="3.2" fill="#111827"/>
        <circle cx="28" cy="28" r="3.2" fill="#111827"/>
      </g>
    </svg>
  `;

  return L.icon({
    iconUrl: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
    iconSize: [42, 42],
    iconAnchor: [21, 21],
    popupAnchor: [0, -18]
  });
}

function createUserIcon() {
  const svg = `
    <div class="user-marker-wrap">
      <svg class="user-marker-svg" xmlns="http://www.w3.org/2000/svg" width="44" height="44" viewBox="0 0 44 44">
        <circle cx="22" cy="22" r="18" fill="#1d4ed8" opacity="0.14"/>
        <g transform="translate(7 5)">
          <path d="M15 0C11.6 0 8.8 2.8 8.8 6.2S11.6 12.4 15 12.4s6.2-2.8 6.2-6.2S18.4 0 15 0Z" fill="#2563eb" stroke="#ffffff" stroke-width="1.6"/>
          <path d="M15 14.8c-6 0-10.8 4.7-10.8 10.6v3.1c0 1 0.8 1.9 1.9 1.9h17.8c1 0 1.9-0.8 1.9-1.9v-3.1c0-5.9-4.9-10.6-10.8-10.6Z" fill="#60a5fa" stroke="#ffffff" stroke-width="1.6"/>
          <circle cx="15" cy="22" r="1.8" fill="#ffffff"/>
          <path d="M15 24.4v4.8M10.8 30.4l4.2-3.4 4.2 3.4" stroke="#ffffff" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
        </g>
      </svg>
    </div>
  `;

  return L.divIcon({
    html: svg,
    className: 'user-marker-icon',
    iconSize: [44, 44],
    iconAnchor: [22, 22],
    popupAnchor: [0, -18]
  });
}

function focusUserMarker() {
  if (!userMarkerLayer || !userMarkerLayer._icon) {
    return;
  }

  const inner = userMarkerLayer._icon.querySelector('.user-marker-wrap');
  if (!inner) {
    return;
  }
  inner.classList.remove('user-marker-pop');
  void inner.offsetWidth;
  inner.classList.add('user-marker-pop');

  setTimeout(() => {
    if (inner) {
      inner.classList.remove('user-marker-pop');
    }
  }, 1400);
}

function clearMapLayers() {
  if (!map) {
    return;
  }
  mapLayers.forEach((layer) => map.removeLayer(layer));
  mapLayers = [];
}

function addLayer(layer) {
  if (!map) {
    return layer;
  }
  layer.addTo(map);
  mapLayers.push(layer);
  return layer;
}

function summarizeBuses(items) {
  const onlineBuses = items.filter((bus) => bus.status === 'online');
  const liveTrackedBuses = onlineBuses.filter((bus) => bus.tripStatus === 'active' && bus.isLiveTracked);
  const counts = { Low: 0, Medium: 0, High: 0 };
  let totalLoad = 0;

  liveTrackedBuses.forEach((bus) => {
    const level = bus.crowdLevel === 'High' || bus.crowdLevel === 'Medium' ? bus.crowdLevel : 'Low';
    counts[level] += 1;
    totalLoad += Number(bus.capacityRatio) > 0
      ? Number(bus.capacityRatio) * 100
      : (Number(bus.capacity) > 0 ? (Number(bus.passengers) / Number(bus.capacity)) * 100 : 0);
  });

  return {
    active_bus_count: liveTrackedBuses.length,
    avg_crowd: liveTrackedBuses.length ? Math.round(totalLoad / liveTrackedBuses.length) : 0,
    low_count: counts.Low,
    medium_count: counts.Medium,
    high_count: counts.High
  };
}

function distanceKm(a, b) {
  if (!a || !b) return Number.POSITIVE_INFINITY;
  const toRad = (value) => (value * Math.PI) / 180;
  const earth = 6371;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);

  const hav =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.sin(dLng / 2) * Math.sin(dLng / 2) * Math.cos(lat1) * Math.cos(lat2);

  return earth * 2 * Math.atan2(Math.sqrt(hav), Math.sqrt(1 - hav));
}

function formatArrivalMinutes(minutes) {
  if (!Number.isFinite(Number(minutes))) {
    return 'ETA unavailable';
  }

  const rounded = Math.max(Math.round(Number(minutes)), 0);
  if (rounded <= 0) {
    return 'Arriving now';
  }
  if (rounded === 1) {
    return 'About 1 min';
  }
  return `About ${rounded} min`;
}

function estimateMinutesFromDistance(distanceKmValue, route = null) {
  const distance = Number(distanceKmValue);
  if (!Number.isFinite(distance)) {
    return null;
  }

  const routeDistance = Number(route?.distanceKm);
  const routeDuration = Number(route?.expectedDurationMinutes);
  const routeSpeed = routeDistance > 0 && routeDuration > 0
    ? routeDistance / (routeDuration / 60)
    : FALLBACK_BUS_SPEED_KPH;
  const speedKph = Math.min(Math.max(routeSpeed || FALLBACK_BUS_SPEED_KPH, 10), 45);
  return Math.max(Math.ceil((distance / speedKph) * 60), distance > 0 ? 1 : 0);
}

function getRouteByName(routeName) {
  return getRoutes().find((route) => stopNameKey(route.routeName) === stopNameKey(routeName)) || null;
}

function offsetIfOverlapping(lat, lng) {
  if (!userLocation) {
    return [lat, lng];
  }

  const overlapDistance = distanceKm(userLocation, { lat, lng });
  if (overlapDistance > 0.03) {
    return [lat, lng];
  }

  return [lat + 0.00035, lng + 0.00035];
}

function getSortedBuses() {
  const sorted = buses.filter((bus) => bus.status === 'online');
  const sortMode = sortSelect ? sortSelect.value : 'nearest';

  sorted.forEach((bus) => {
    bus.distanceKm = userLocation && hasValidCoordinates(bus.lat, bus.lng)
      ? distanceKm(userLocation, { lat: Number(bus.lat), lng: Number(bus.lng) })
      : Number.POSITIVE_INFINITY;
    bus.capacityRatio = Number(bus.capacity) > 0 ? Number(bus.passengers) / Number(bus.capacity) : 0;
    bus.arrivalEstimate = estimateBusArrivalForCommuter(bus);
  });

  if (sortMode === 'capacity_low') {
    sorted.sort((a, b) => a.capacityRatio - b.capacityRatio);
  } else if (sortMode === 'capacity_high') {
    sorted.sort((a, b) => b.capacityRatio - a.capacityRatio);
  } else {
    sorted.sort((a, b) => a.distanceKm - b.distanceKm);
  }

  return sorted;
}

function estimateBusArrivalForCommuter(bus) {
  if (!userLocation || bus.tripStatus !== 'active' || !bus.isLiveTracked || !hasValidCoordinates(bus.lat, bus.lng)) {
    return null;
  }

  const route = getRouteByName(bus.direction);
  const nearestStop = findNearestStopForCurrentLocation();
  if (route && nearestStop && findRouteStopIndex(route, nearestStop.name) >= 0) {
    const stopMinutes = estimateMinutesToStop(route, bus, nearestStop.name);
    if (stopMinutes !== null) {
      return {
        minutes: stopMinutes,
        label: `${formatArrivalMinutes(stopMinutes)} to ${nearestStop.name}`,
        detail: 'Nearest stop from your location'
      };
    }
  }

  const directDistance = distanceKm(userLocation, { lat: Number(bus.lat), lng: Number(bus.lng) });
  const directMinutes = estimateMinutesFromDistance(directDistance, route);
  if (directMinutes === null) {
    return null;
  }

  return {
    minutes: directMinutes,
    label: `${formatArrivalMinutes(directMinutes)} to your area`,
    detail: `${directDistance.toFixed(2)} km from you`
  };
}

function renderBusArrivalEstimate(bus) {
  if (!userLocation) {
    return 'Allow location to show ETA';
  }
  if (bus.tripStatus !== 'active' || !bus.isLiveTracked) {
    return 'ETA unavailable';
  }
  return bus.arrivalEstimate ? bus.arrivalEstimate.label : 'ETA unavailable';
}

function renderBusTripStatus(bus) {
  return bus.tripStatus === 'active' ? 'Active trip' : 'No active trip';
}

function renderBusList() {
  if (!busList) return;

  const sortedBuses = getSortedBuses();

  if (!sortedBuses.length) {
    busList.innerHTML = '<div class="bus-card"><h3>No buses found</h3><p>The system will show fleet units here once they are registered.</p></div>';
    return;
  }

  busList.innerHTML = sortedBuses.map((bus, index) => `
    <article class="bus-card ${index === 0 && sortSelect && sortSelect.value === 'nearest' ? 'nearest' : ''}" style="border-left-color:${bus.routeColor || '#1d4ed8'}">
      <h3>${bus.id}</h3>
      <p>${bus.direction}</p>
      <div class="row">
        <span>${bus.passengers}/${bus.capacity} passengers</span>
        <span class="crowd-pill ${bus.crowdLevel}">${bus.crowdLevel}</span>
      </div>
      <div class="row">
        <span>Status: ${renderBusTripStatus(bus)}</span>
        <span>${bus.driver}</span>
      </div>
      <div class="row">
        <span>${bus.nextStop}</span>
        <span>${bus.tripStatus === 'active' && Number.isFinite(bus.distanceKm) ? `${bus.distanceKm.toFixed(2)} km away` : 'Distance unavailable'}</span>
      </div>
      <div class="row eta-row">
        <span>Arrival</span>
        <strong>${renderBusArrivalEstimate(bus)}</strong>
      </div>
      <div class="row">
        <span>${Math.round(bus.capacityRatio * 100)}% load</span>
        <span>${bus.isLiveTracked ? 'Live tracking active' : 'Awaiting GPS'}</span>
      </div>
    </article>
  `).join('');
}

function renderSummary(summary) {
  if (activeBusCount) activeBusCount.textContent = summary.active_bus_count ?? buses.length;
  if (avgCrowd) avgCrowd.textContent = summary.avg_crowd ?? 0;
  if (highCrowdCount) highCrowdCount.textContent = summary.high_count ?? 0;
  if (crowdLevels) {
    crowdLevels.innerHTML = `
      <div><span class="dot low"></span>Low ${summary.low_count ?? 0}</div>
      <div><span class="dot medium"></span>Medium ${summary.medium_count ?? 0}</div>
      <div><span class="dot high"></span>High ${summary.high_count ?? 0}</div>
    `;
  }
}

function renderMap() {
  if (!map) {
    return;
  }
  clearMapLayers();

  const sortedBuses = getSortedBuses();
  const bounds = [];

  if (userLocation) {
    userMarkerLayer = addLayer(L.marker([userLocation.lat, userLocation.lng], {
      icon: createUserIcon()
    }));
    userMarkerLayer.bindPopup('<strong>You are here</strong>');
    bounds.push([userLocation.lat, userLocation.lng]);
  }

  sortedBuses.forEach((bus) => {
    const hasLivePosition = hasValidCoordinates(bus.lat, bus.lng);
    const coords = Array.isArray(bus.coords) ? bus.coords : [];
    const history = Array.isArray(bus.history) ? bus.history : [];

    if (history.length > 1) {
      const trail = addLayer(L.polyline(history, {
        color: bus.routeColor || '#1d4ed8',
        weight: 4,
        opacity: 0.95,
        dashArray: '8, 8'
      }));
      trail.bindPopup(`${bus.id} live travel history`);
      bounds.push(...history);
    }

    if (!hasLivePosition) {
      return;
    }

    const [markerLat, markerLng] = offsetIfOverlapping(Number(bus.lat), Number(bus.lng));
    const marker = addLayer(L.marker([markerLat, markerLng], {
      icon: createBusIcon(bus.crowdLevel)
    }));
    marker.bindPopup(`
      <strong>${bus.id}</strong><br>
      ${bus.direction}<br>
      Status: ${bus.status}<br>
      ${bus.passengers}/${bus.capacity} passengers<br>
      ${bus.nextStop}<br>
      Arrival: ${renderBusArrivalEstimate(bus)}
    `);
    bounds.push([markerLat, markerLng]);
  });

  if (bounds.length && !hasSetInitialMapView) {
    map.fitBounds(bounds, { padding: [24, 24] });
    hasSetInitialMapView = true;
  } else if (userLocation && !hasSetInitialMapView) {
    map.setView([userLocation.lat, userLocation.lng], 14);
    hasSetInitialMapView = true;
  }
}

function updateUserLocationText(text) {
  if (userLocationText) {
    userLocationText.textContent = text;
  }
}

async function resolveLocationName(lat, lng) {
  try {
    const response = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lng}`, {
      headers: {
        'Accept-Language': 'en'
      }
    });
    if (!response.ok) {
      return null;
    }

    const data = await response.json();
    const address = data.address || {};
    return (
      address.city ||
      address.town ||
      address.municipality ||
      address.village ||
      address.county ||
      address.state ||
      null
    );
  } catch (error) {
    return null;
  }
}

function detectUserLocation() {
  if (!navigator.geolocation) {
    updateUserLocationText('Location is not available on this device');
    renderBusList();
    renderMap();
    return;
  }

  navigator.geolocation.getCurrentPosition(
    async (position) => {
      userLocation = {
        lat: position.coords.latitude,
        lng: position.coords.longitude
      };
      updateUserLocationText('Detecting your city...');
      const locationName = await resolveLocationName(userLocation.lat, userLocation.lng);
      updateUserLocationText(locationName || `${userLocation.lat.toFixed(5)}, ${userLocation.lng.toFixed(5)}`);
      const nearestStop = findNearestStopForCurrentLocation();
      if (applyCurrentLocationToPlanner && plannerOrigin && nearestStop) {
        plannerOrigin.value = nearestStop.name;
        applyCurrentLocationToPlanner = false;
      }
      populateStopOptions();
      renderBusList();
      renderMap();
      renderPlanner();
      if (map && shouldFocusUserLocation) {
        map.setView([userLocation.lat, userLocation.lng], 15);
        shouldFocusUserLocation = false;
      }
      setTimeout(focusUserMarker, 120);
    },
    () => {
      updateUserLocationText('Location permission denied');
      renderBusList();
      renderMap();
    },
    {
      enableHighAccuracy: true,
      maximumAge: 10000,
      timeout: 10000
    }
  );
}

function stopNameKey(value) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function getRoutes() {
  return Array.isArray(commuterData.routes) ? commuterData.routes : [];
}

function getStopDirectory() {
  return Array.isArray(commuterData.stopDirectory) ? commuterData.stopDirectory : [];
}

function findNearestStopForCurrentLocation() {
  if (!userLocation) {
    return null;
  }

  let nearestStop = null;
  let nearestDistance = Number.POSITIVE_INFINITY;
  getStopDirectory().forEach((stop) => {
    if (!hasValidCoordinates(stop.lat, stop.lng)) {
      return;
    }
    const distance = distanceKm(userLocation, { lat: Number(stop.lat), lng: Number(stop.lng) });
    if (distance < nearestDistance) {
      nearestDistance = distance;
      nearestStop = stop;
    }
  });

  return nearestStop;
}

function getRouteLiveBuses(routeName) {
  return buses.filter((bus) => bus.direction === routeName && bus.tripStatus === 'active' && bus.isLiveTracked);
}

function getRoutesServingStop(stopName) {
  return getRoutes().filter((route) => findRouteStopIndex(route, stopName) >= 0);
}

function findRouteStopIndex(route, stopName) {
  const stops = Array.isArray(route.stops) ? route.stops : [];
  return stops.findIndex((stop) => stopNameKey(stop.name) === stopNameKey(stopName));
}

function inferBusStopIndex(route, bus) {
  const stops = Array.isArray(route.stops) ? route.stops : [];
  let stopIndex = findRouteStopIndex(route, bus.nextStop);
  if (stopIndex >= 0) {
    return stopIndex;
  }

  if (!hasValidCoordinates(bus.lat, bus.lng)) {
    return -1;
  }

  let nearestIndex = -1;
  let nearestScore = Number.POSITIVE_INFINITY;
  stops.forEach((stop, index) => {
    const score = Math.abs(Number(bus.lat) - Number(stop.lat)) + Math.abs(Number(bus.lng) - Number(stop.lng));
    if (score < nearestScore) {
      nearestScore = score;
      nearestIndex = index;
    }
  });
  return nearestIndex;
}

function estimateMinutesToStop(route, bus, stopName) {
  const targetIndex = findRouteStopIndex(route, stopName);
  const currentIndex = inferBusStopIndex(route, bus);
  const stops = Array.isArray(route.stops) ? route.stops : [];

  if (targetIndex < 0 || currentIndex < 0 || currentIndex > targetIndex) {
    return null;
  }

  const currentStop = stops[currentIndex];
  const targetStop = stops[targetIndex];
  return Math.max(Number(targetStop.minutes_from_start) - Number(currentStop.minutes_from_start), 0);
}

function estimateSegmentDistance(route, originStop, destinationStop) {
  const totalDistance = Number(route.distanceKm) || 0;
  const totalDuration = Number(route.expectedDurationMinutes) || 0;
  const originMinutes = Number(originStop.minutes_from_start) || 0;
  const destinationMinutes = Number(destinationStop.minutes_from_start) || 0;

  if (totalDuration > 0 && destinationMinutes >= originMinutes) {
    return (totalDistance * ((destinationMinutes - originMinutes) / totalDuration)).toFixed(1);
  }

  const indexGap = Math.max(Number(destinationStop.sequence) - Number(originStop.sequence), 0);
  const denominator = Math.max((route.stops || []).length - 1, 1);
  return (totalDistance * (indexGap / denominator)).toFixed(1);
}

function estimateFareTable(distanceKmValue, minimumFare = 15, discountedFare = 12) {
  const numericDistance = Number(distanceKmValue) || 0;
  const baseRegular = Math.max(Math.round(Number(minimumFare) || 15), 1);
  const baseDiscounted = Math.max(Math.round(Number(discountedFare) || 12), 1);
  const extraDistance = Math.max(numericDistance - 4, 0);
  const regular = Math.max(Math.round(baseRegular + (extraDistance * 1.35)), baseRegular);
  const discounted = Math.max(Math.round(baseDiscounted + (extraDistance * 1.1)), baseDiscounted);
  return {
    regular,
    student: discounted,
    pwd: discounted,
    senior: discounted
  };
}

function hasSpecificLandmark(stop) {
  const landmark = String(stop?.landmark || '').trim();
  if (!landmark) {
    return false;
  }

  const genericLandmarks = new Set([
    'corridor stop',
    'gajoda-cabanatuan corridor stop'
  ]);
  return !genericLandmarks.has(stopNameKey(landmark)) && stopNameKey(landmark) !== stopNameKey(stop?.name);
}

function formatStopPoint(stop) {
  const stopName = String(stop?.name || 'Selected stop').trim();
  if (!hasSpecificLandmark(stop)) {
    return stopName;
  }
  return `${stopName} (${stop.landmark})`;
}

function buildDirectionalRoute(route, direction = 'forward') {
  if (direction === 'forward') {
    return {
      ...route,
      directionLabel: `${route.startPoint} to ${route.endPoint}`
    };
  }

  const reversedStops = [...(route.stops || [])].reverse().map((stop, index, allStops) => ({
    ...stop,
    sequence: index + 1,
    minutes_from_start: (Number(route.expectedDurationMinutes) || 0) - (Number(stop.minutes_from_start) || 0)
  })).sort((a, b) => Number(a.minutes_from_start) - Number(b.minutes_from_start));

  return {
    ...route,
    startPoint: route.endPoint,
    endPoint: route.startPoint,
    stops: reversedStops,
    directionLabel: `${route.endPoint} to ${route.startPoint}`
  };
}

function findJourneyOptions(originName, destinationName) {
  if (!originName || !destinationName) {
    return [];
  }

  const options = [];
  getRoutes().forEach((route) => {
    const originIndex = findRouteStopIndex(route, originName);
    const destinationIndex = findRouteStopIndex(route, destinationName);
    if (originIndex < 0 || destinationIndex < 0 || originIndex >= destinationIndex) {
      return;
    }

    const originStop = route.stops[originIndex];
    const destinationStop = route.stops[destinationIndex];
    const estimatedMinutes = Math.max(
      Number(destinationStop.minutes_from_start) - Number(originStop.minutes_from_start),
      0
    );
    const estimatedDistanceKm = estimateSegmentDistance(route, originStop, destinationStop);
    const liveBuses = getRouteLiveBuses(route.routeName)
      .map((bus) => ({
        ...bus,
        etaMinutes: estimateMinutesToStop(route, bus, originName)
      }))
      .filter((bus) => bus.etaMinutes !== null)
      .sort((a, b) => a.etaMinutes - b.etaMinutes);

    options.push({
      route,
      canonicalRouteName: route.routeName,
      travelDirection: /to gajoda terminal$/i.test(route.routeName) ? 'reverse' : 'forward',
      originStop,
      destinationStop,
      estimatedMinutes,
      estimatedDistanceKm,
      fareTable: estimateFareTable(estimatedDistanceKm, route.minimumFare, route.discountedFare),
      liveBuses,
      nextBus: liveBuses[0] || null
    });
  });

  return options.sort((a, b) => a.estimatedMinutes - b.estimatedMinutes);
}

function renderPlannerResult(option) {
  const nextBusText = option.nextBus
    ? `${option.nextBus.id} in about ${option.nextBus.etaMinutes} min to ${option.originStop.name}`
    : 'No live bus has a clear ETA to this origin yet';

  return `
    <article class="planner-result-card">
      <div class="planner-result-head">
        <div>
          <h3>${option.route.routeName}</h3>
          <p class="planner-route-line">${option.originStop.name} to ${option.destinationStop.name}</p>
        </div>
      </div>
      <div class="planner-badges">
        <span>${option.estimatedMinutes} min estimated travel</span>
        <span>${option.estimatedDistanceKm} km estimated distance</span>
        <span>Minimum fare PHP ${Math.round(Number(option.route.minimumFare) || 0)}</span>
        <span>${option.liveBuses.length} live bus${option.liveBuses.length === 1 ? '' : 'es'} on route</span>
      </div>
      <div class="planner-meta-grid">
        <article class="planner-meta-card">
          <span>Boarding point</span>
          <strong>${formatStopPoint(option.originStop)}</strong>
        </article>
        <article class="planner-meta-card">
          <span>Arrival point</span>
          <strong>${formatStopPoint(option.destinationStop)}</strong>
        </article>
      </div>
      <div class="planner-meta">
        <span>Next bus: ${nextBusText}</span>
        <span>${option.route.stops.length} published stops on this corridor</span>
      </div>
      <div class="planner-fares">
        <article><p>Regular</p><strong>PHP ${option.fareTable.regular}</strong></article>
        <article><p>Student</p><strong>PHP ${option.fareTable.student}</strong></article>
        <article><p>PWD</p><strong>PHP ${option.fareTable.pwd}</strong></article>
        <article><p>Senior</p><strong>PHP ${option.fareTable.senior}</strong></article>
      </div>
    </article>
  `;
}

function renderPlannerEmpty(message) {
  if (!plannerResults) {
    return;
  }
  plannerResults.innerHTML = `<article class="planner-empty"><strong>Planner unavailable</strong><p>${message}</p></article>`;
}

function renderPlanner() {
  if (!plannerResults) {
    return;
  }

  const originName = plannerOrigin ? plannerOrigin.value.trim() : '';
  const destinationName = plannerDestination ? plannerDestination.value.trim() : '';

  if (!originName || !destinationName) {
    renderPlannerEmpty('Choose an origin and destination stop to see the direct route, estimated time, and fare guide.');
    return;
  }

  if (stopNameKey(originName) === stopNameKey(destinationName)) {
    renderPlannerEmpty('Origin and destination must be different stops.');
    return;
  }

  const options = findJourneyOptions(originName, destinationName);
  if (!options.length) {
    renderPlannerEmpty('No direct one-way route is published for that stop pair yet. For now the planner only handles direct trips inside the Gajoda corridor.');
    return;
  }

  plannerResults.innerHTML = options.map(renderPlannerResult).join('');
}

function renderStopDirectory() {
  if (!stopDirectoryList) {
    return;
  }

  const directory = getStopDirectory();
  if (!directory.length) {
    stopDirectoryList.innerHTML = '<article class="stop-card"><strong>No stops published</strong><p>Stop information will appear here after route publishing is completed.</p></article>';
    return;
  }

  stopDirectoryList.innerHTML = directory.map((stop) => `
    <article class="stop-card">
      <strong>${stop.name}</strong>
      <span>${stop.landmark}</span>
      <p>${stop.routes.join(' • ')}</p>
      <p>${Array.isArray(stop.nextArrivals) && stop.nextArrivals.length ? stop.nextArrivals.map((arrival) => `${arrival.routeName}: ${arrival.minutes} min`).join(' • ') : 'No live arrival estimate right now'}</p>
    </article>
  `).join('');
}

function renderRouteCards() {
  if (!plannerRouteList) {
    return;
  }

  plannerRouteList.innerHTML = getRoutes().map((route) => {
    const liveBuses = getRouteLiveBuses(route.routeName);
    return `
      <article class="route-mini-card">
        <strong>${route.routeName}</strong>
        <span>${route.startPoint} to ${route.endPoint}</span>
        <p>${route.expectedDurationMinutes} min • ${route.distanceKm} km • Min fare PHP ${Math.round(Number(route.minimumFare) || 0)} • ${liveBuses.length} live bus${liveBuses.length === 1 ? '' : 'es'}</p>
      </article>
    `;
  }).join('');
}

function populateStopOptions() {
  const stopNames = Array.isArray(commuterData.stopNames) ? commuterData.stopNames : [];
  const defaultRoute = getRoutes()[0];

  if (plannerOrigin && plannerOrigin.tagName === 'SELECT') {
    const currentOriginValue = plannerOrigin.value;
    plannerOrigin.innerHTML = `<option value="">Select origin</option>${stopNames.map((name) => `<option value="${name}">${name}</option>`).join('')}`;
    if (currentOriginValue && stopNames.includes(currentOriginValue)) {
      plannerOrigin.value = currentOriginValue;
    }
  }

  if (plannerDestination && plannerDestination.tagName === 'SELECT') {
    const currentValue = plannerDestination.value;
    const originValue = plannerOrigin ? plannerOrigin.value : '';
    const destinationOptions = stopNames.filter((name) => stopNameKey(name) !== stopNameKey(originValue));
    const groupedOptions = getRoutes().map((route) => {
      const routeStops = (route.stops || [])
        .map((stop) => stop.name)
        .filter((name) => destinationOptions.includes(name));
      if (!routeStops.length) {
        return '';
      }
      return `<optgroup label="${route.routeName}">${routeStops.map((name) => `<option value="${name}">${name}</option>`).join('')}</optgroup>`;
    }).join('');
    plannerDestination.innerHTML = `<option value="">Select destination</option>${groupedOptions}`;
    if (currentValue && destinationOptions.includes(currentValue)) {
      plannerDestination.value = currentValue;
    }
  }

  if (plannerOrigin && !plannerOrigin.value && defaultRoute && Array.isArray(defaultRoute.stops) && defaultRoute.stops.length) {
    plannerOrigin.value = '';
  }
  if (plannerDestination && !plannerDestination.value && defaultRoute && Array.isArray(defaultRoute.stops) && defaultRoute.stops.length > 1) {
    plannerDestination.value = defaultRoute.stops[defaultRoute.stops.length - 1].name;
  }
}

function refreshPublicPlanner() {
  populateStopOptions();
  renderRouteCards();
  renderStopDirectory();
  renderPlanner();
}

async function refreshCommuterData() {
  if (!window.publicCommuterEndpoint) {
    return;
  }

  try {
    const response = await fetch(window.publicCommuterEndpoint, { cache: 'no-store' });
    if (!response.ok) {
      return;
    }
    commuterData = await response.json();
    refreshPublicPlanner();
  } catch (error) {
    console.error('Planner data refresh failed', error);
  }
}

async function refreshLiveData() {
  try {
    const response = await fetch(window.liveBusEndpoint, { cache: 'no-store' });
    if (!response.ok) return;
    const payload = await response.json();
    applyLiveBusPayload(payload);
  } catch (error) {
    console.error('Live data refresh failed', error);
  }
}

function applyLiveBusPayload(payload) {
  buses = Array.isArray(payload.buses) ? payload.buses : [];
  renderSummary(payload);
  renderBusList();
  renderMap();
  renderRouteCards();
  renderPlanner();
}

function connectLiveTrackingSocket() {
  if (typeof io !== 'function') {
    return;
  }

  const socket = io({
    transports: ['polling'],
    reconnection: true
  });

  socket.on('live_buses:update', applyLiveBusPayload);
}

if (sortSelect) {
  sortSelect.addEventListener('change', () => {
    renderBusList();
    renderMap();
  });
}

if (locateMeBtn) {
  locateMeBtn.addEventListener('click', () => {
    shouldFocusUserLocation = true;
    detectUserLocation();
    setTimeout(focusUserMarker, 800);
  });
}

if (plannerOrigin) {
  plannerOrigin.addEventListener('change', () => {
    populateStopOptions();
    renderPlanner();
  });
}

if (plannerDestination) {
  plannerDestination.addEventListener('change', () => {
    renderPlanner();
  });
}

if (useCurrentLocationBtn && plannerOrigin) {
  useCurrentLocationBtn.addEventListener('click', () => {
    applyCurrentLocationToPlanner = true;
    if (!userLocation) {
      detectUserLocation();
      return;
    }
    const nearestStop = findNearestStopForCurrentLocation();
    if (nearestStop) {
      plannerOrigin.value = nearestStop.name;
      populateStopOptions();
      renderPlanner();
    }
    applyCurrentLocationToPlanner = false;
  });
}

function setMenuOpen(open) {
  if (!menuOverlay) {
    return;
  }

  menuOverlay.classList.toggle('open', open);
  document.body.classList.toggle('menu-open', open);
}

if (menuToggle && menuOverlay) {
  menuToggle.addEventListener('click', () => setMenuOpen(true));
}

if (menuClose && menuOverlay) {
  menuClose.addEventListener('click', () => setMenuOpen(false));
  menuOverlay.addEventListener('click', (event) => {
    if (event.target === menuOverlay) {
      setMenuOpen(false);
    }
  });
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    setMenuOpen(false);
  }
});

renderSummary(summarizeBuses(buses));
refreshPublicPlanner();
detectUserLocation();
renderBusList();
renderMap();
connectLiveTrackingSocket();
setInterval(refreshLiveData, LIVE_TRACKING_REFRESH_MS);
setInterval(refreshCommuterData, COMMUTER_DATA_REFRESH_MS);
